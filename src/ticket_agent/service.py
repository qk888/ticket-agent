"""服务层：把「跑一张工单」和「人工审批」封装成与框架无关的操作。

为什么要独立于 FastAPI：
- 同一段逻辑要被 HTTP 端点、CLI、评测脚本三处复用。
  写在路由函数里，CLI 就只能去 import HTTP 层的私有函数，两天就烂了。
- **租户校验发生在这一层**：`tenant_id` 由调用方（鉴权中间件）给出，
  绝不从请求体读取。HTTP 层和后端逻辑层各守一道，改一个入口不会漏出越权。

★ 关于「人工审批」的设计选择：
审批落库时**复用 `registry` 里的写工具**，而不是直接 `session.add(...)`。
理由：工具里有幂等键、审计日志、`refund_count` 维护、状态机校验；
绕过去手工写表，等于把同一份业务规则实现两遍 —— 评审项目里最常见的自伤。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal

from .config import settings
from .db.models import AuditLog
from .db.session import TenantRepo, session_scope
from .db.snapshot import export_snapshot, resolve_ticket_order
from .graph.builder import build_graph
from .graph.checkpoint import get_checkpointer
from .graph.nodes import apply_customer_reply
from .graph.state import new_state
from .rules import PolicyViolation, decide_for_order, refund_is_forbidden
from .tools.registry import registry, set_tool_context

Decision = Literal["approve_refund", "reject", "keep_pending"]


def _audit_human_override(
    tenant_id: str, ticket_id: int, run_id: str, verdict: Any, amount: float
) -> None:
    """把「人工显式越过政策」记进审计日志。

    ★ 为什么值得单独写一条：缺陷 ⑥ 的本质不是"人能批"，而是"人批了没人知道"。
    留下这条记录之后，「政策被越过几次、越的是哪条规则」就变成了可查询的数据，
    而不是一个只能靠翻数据库才偶然发现的窟窿。
    """
    with session_scope() as s:
        s.add(
            AuditLog(
                ticket_id=ticket_id,
                tenant_id=tenant_id,
                actor="human",
                action="policy_override",
                payload={
                    "run_id": run_id,
                    "rule_hits": list(verdict.rule_hits),
                    "reasons": list(verdict.reasons),
                    "amount": round(float(amount), 2),
                    "override": True,
                },
                ok=True,
            )
        )


# ══════════════════════════════════════════════════════════════
# 序列化：把 LangChain 消息等对象转成可 JSON 化的结构
# ══════════════════════════════════════════════════════════════


def _jsonable(obj: Any, depth: int = 0) -> Any:
    """尽力把任意对象转成可 JSON 序列化的结构。

    SSE 的每一帧都要过 json.dumps，一旦混入 LangChain 的 Message 对象
    就会在流式响应中途抛 TypeError —— 而那时 HTTP 200 和部分帧已经发出去了，
    客户端只能看到"流断了"，排查成本极高。所以这里**一律在出站前净化**。
    """
    if depth > 6:
        return str(obj)[:200]
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v, depth + 1) for v in obj]
    # LangChain 消息：保留角色与内容，丢掉对象图
    content = getattr(obj, "content", None)
    if content is not None:
        out: dict[str, Any] = {
            "role": getattr(obj, "type", type(obj).__name__),
            "content": content if isinstance(content, str) else _jsonable(content, depth + 1),
        }
        tcs = getattr(obj, "tool_calls", None)
        if tcs:
            out["tool_calls"] = _jsonable(tcs, depth + 1)
        return out
    if hasattr(obj, "to_dict"):
        try:
            return _jsonable(obj.to_dict(), depth + 1)
        except Exception:  # noqa: BLE001 —— 序列化兜底不应把正常流程炸掉
            pass
    return str(obj)[:400]


# ══════════════════════════════════════════════════════════════
# 运行记录
# ══════════════════════════════════════════════════════════════


@dataclass
class RunRecord:
    """一次工单处置的运行记录。"""

    run_id: str
    tenant_id: str
    ticket_id: int
    arm: str
    status: str = "running"  # running | finished | awaiting_human | failed
    created_at: float = field(default_factory=time.time)
    state: dict[str, Any] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def requires_human(self) -> bool:
        """是否等人裁决。

        只看 status —— 两种转人工来源（agent 主动 escalate / 预算熔断兜底）
        最终都落成库里的 `pending_human`，`_finalize` 已把它归一成 awaiting_human。
        再去看 state 上的布尔标志会漏掉"agent 主动转人工"这条主路径。
        """
        return self.status == "awaiting_human"

    def summary(self) -> dict[str, Any]:
        st = self.state or {}
        return {
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "ticket_id": self.ticket_id,
            "arm": self.arm,
            "status": self.status,
            "final_answer": st.get("final_answer", ""),
            # ★ 用 self.requires_human 而不是 st["requires_human_approval"]：
            #   state 上那个布尔只有**预算熔断**路径会置真，
            #   agent 主动调 escalate_to_human 时它是 False —— 直接读 state
            #   会把状态为 awaiting_human 的运行报成"不需要人工"。
            "requires_human_approval": self.requires_human,
            "actions_taken": _jsonable(st.get("actions_taken") or []),
            "forbidden_hits": _jsonable(st.get("forbidden_hits") or []),
            "verdict": _jsonable(st.get("verdict") or {}),
            "budget": _jsonable(st.get("budget") or {}),
            "snapshot": _jsonable(self.snapshot),
            "error": self.error,
        }


class RunStore:
    """进程内运行记录表。

    ★ 生产环境应换成 LangGraph 的 checkpointer（postgres）——
    那时 state 落库、进程重启可续跑、多副本共享。
    这里用内存 + 锁的原因是：单机 demo 不需要外部依赖，
    但**要显式写清替代方案**，不能装作内存字典就是终态设计。
    """

    def __init__(self, max_runs: int = 500) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._lock = threading.Lock()
        self._max = max_runs

    def add(self, rec: RunRecord) -> None:
        with self._lock:
            self._runs[rec.run_id] = rec
            if len(self._runs) > self._max:
                # 淘汰最老的终态记录；运行中的永不淘汰
                done = sorted(
                    (r for r in self._runs.values() if r.status != "running"),
                    key=lambda r: r.created_at,
                )
                for old in done[: max(1, len(self._runs) - self._max)]:
                    self._runs.pop(old.run_id, None)

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def list(self, tenant_id: str, limit: int = 50) -> list[RunRecord]:
        with self._lock:
            rs = [r for r in self._runs.values() if r.tenant_id == tenant_id]
        return sorted(rs, key=lambda r: r.created_at, reverse=True)[:limit]


run_store = RunStore()


# ══════════════════════════════════════════════════════════════
# 读取
# ══════════════════════════════════════════════════════════════


def list_tickets(tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """列出该租户的工单（不跨租户 —— 靠 TenantRepo 强制过滤）。"""
    from sqlalchemy import select

    from .db.models import Ticket

    with session_scope() as s:
        repo = TenantRepo(s, tenant_id)
        rows = s.execute(
            select(Ticket)
            .where(Ticket.tenant_id == tenant_id)
            .order_by(Ticket.created_at.desc())
            .limit(limit)
        ).scalars().all()
        out = []
        for t in rows:
            order, basis = resolve_ticket_order(repo, t)
            out.append(
                {
                    "ticket_id": t.id,
                    "subject": t.subject,
                    "status": t.status,
                    "priority": t.priority,
                    "customer_id": t.customer_id,
                    "order_id": t.order_id,
                    "order_basis": basis,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
            )
        return out


def get_ticket_detail(tenant_id: str, ticket_id: int) -> dict[str, Any]:
    with session_scope() as s:
        repo = TenantRepo(s, tenant_id)
        t = repo.get_ticket(ticket_id)
        if t is None:
            raise LookupError(f"工单 {ticket_id} 不存在或不属于租户 {tenant_id}")
        snap = export_snapshot(s, tenant_id, ticket_id)
        order, basis = resolve_ticket_order(repo, t)
        cust = repo.get_customer(t.customer_id) if t.customer_id else None
        return {
            "ticket": {
                "ticket_id": t.id,
                "subject": t.subject,
                "body": t.body,
                "status": t.status,
                "intent": t.intent,
                "priority": t.priority,
                "resolution": t.resolution,
                # #2 多轮澄清闭环：前端要能显示"这单正在等客户回答什么"。
                # 只读展示，不参与任何判定。
                "pending_question": t.pending_question,
                "customer_reply": t.customer_reply,
            },
            "customer": (
                {
                    "customer_id": cust.id,
                    "name": cust.name,
                    "risk_score": cust.risk_score,
                    "is_blacklisted": cust.is_blacklisted,
                    "complaint_count": cust.complaint_count,
                }
                if cust
                else None
            ),
            "order": (
                {
                    "order_id": order.id,
                    "order_no": order.order_no,
                    "status": order.status,
                    "amount": order.amount,
                    "category": order.category,
                    "refund_count": order.refund_count,
                    "signed_at": order.signed_at.isoformat() if order.signed_at else None,
                }
                if order
                else None
            ),
            "order_basis": basis,
            "snapshot": _jsonable(snap),
        }


# ══════════════════════════════════════════════════════════════
# 运行
# ══════════════════════════════════════════════════════════════


def _finalize(rec: RunRecord, state: dict[str, Any]) -> None:
    """图跑完后收尾：导终态快照、判定是否等人。"""
    rec.state = state
    try:
        with session_scope() as s:
            rec.snapshot = export_snapshot(s, rec.tenant_id, rec.ticket_id)
    except Exception as exc:  # noqa: BLE001 —— 快照失败不影响已有结论
        rec.error = f"导出终态失败：{type(exc).__name__}: {exc}"[:300]

    # ★ 判"是否等人"以**数据库里的工单状态**为准，而不是只看 state 上的布尔标志。
    #   escalate_to_human 工具写的是库里的 ticket.status="pending_human"；
    #   若只信 state 标志，agent 正常转人工的运行会被报成 finished，
    #   人工待办队列与运行列表就对不上了 —— 这是最容易在演示时被问到的地方。
    ticket_status = ((rec.snapshot or {}).get("ticket") or {}).get("status")
    if state.get("requires_human_approval") or ticket_status == "pending_human":
        rec.status = "awaiting_human"
    else:
        rec.status = "finished"


def stream_run(
    tenant_id: str,
    ticket_id: int,
    arm: str = "multi",
    user_input: str = "",
    dry_run: bool = False,
) -> Iterator[dict[str, Any]]:
    """跑一张工单并以事件流形式产出每一步。

    产出的事件：
      run_started / node_started / node_finished / tool_call / final / error / done

    ★ 为什么用 `stream_mode="updates"` 而不是自己埋点：
    LangGraph 的 updates 模式天然按节点粒度给增量，节点名就是观测单位；
    自己往节点里打日志反而会污染 state，且并发下顺序不可靠。
    """
    run_id = uuid.uuid4().hex[:12]
    rec = RunRecord(run_id=run_id, tenant_id=tenant_id, ticket_id=ticket_id, arm=arm)
    run_store.add(rec)

    yield {"type": "run_started", "run_id": run_id, "ticket_id": ticket_id, "arm": arm}

    graph = build_graph(arm=arm)  # type: ignore[arg-type]
    init = new_state(tenant_id, ticket_id, run_id, arm=arm, user_input=user_input)
    cfg: dict[str, Any] = {"recursion_limit": 50}
    # ★ 服务层不传 checkpointer，走 settings.checkpointer：配了 sqlite/postgres
    #   就落盘，进程重启后能捞回上次跑到哪一步。此时**必须给 thread_id**，
    #   否则 LangGraph 会因为"有 checkpointer 却没有线程标识"直接报错。
    #   run_id 在这里是每次运行新生成的，所以不会撞上"续跑旧状态"的坑
    #   （那个坑属于评测层 —— 它的 run_id 是确定性的，见 eval/harness.py）。
    if get_checkpointer() is not None:
        cfg["configurable"] = {"thread_id": run_id}

    final_state: dict[str, Any] = {}
    pending: list[dict[str, Any]] = []  # ★ 保留每个节点的增量，不要在循环里 update
    try:
        for chunk in graph.stream(init, config=cfg, stream_mode="updates"):
            # chunk = {node_name: 该节点返回的增量}
            for node, update in (chunk or {}).items():
                if node in ("__start__", "__end__"):
                    continue
                upd = update if isinstance(update, dict) else {}
                yield {"type": "node_started", "node": node}

                # 工具调用逐条播报 —— 这是"过程可见"的核心：
                # 客户/坐席看到的是"查了订单、算了天数、判定该转人工"，而不是黑箱等待
                for tc in upd.get("tool_calls") or []:
                    if isinstance(tc, dict):
                        yield {
                            "type": "tool_call",
                            "node": node,
                            "name": tc.get("name"),
                            "args": _jsonable(tc.get("args")),
                            "ok": tc.get("ok"),
                            "ms": tc.get("ms"),
                        }

                note = (upd.get("work_notes") or {}).get(node)
                yield {
                    "type": "node_finished",
                    "node": node,
                    "note": note,
                    "forbidden_hits": _jsonable(upd.get("forbidden_hits") or []),
                    "final_answer": upd.get("final_answer", ""),
                }
                # ★ 必须 append 而不是 update：`actions_taken`/`tool_calls`/`forbidden_hits`
                #   在 state 上挂的是 operator.add，覆盖语义会让最后一个节点
                #   （verifier，不调写工具）的空列表把 resolver 做过的动作抹掉，
                #   于是摘要显示"状态：awaiting_human / 动作：无"这种自相矛盾的结论。
                pending.append(upd)
        state = _aggregate(init, pending)
        _finalize(rec, state)
        yield {"type": "final", **rec.summary()}
    except Exception as exc:  # noqa: BLE001 —— 任何异常都要变成可读事件，而不是断流
        rec.status = "failed"
        rec.error = f"{type(exc).__name__}: {exc}"[:500]
        yield {"type": "error", "run_id": run_id, "error": rec.error}
    # ★ 收尾事件放在 try 之后而不是 finally 里：生成器被提前关闭时
    #   （例如非流式调用方拿到 final 就 return，TestClient/uvicorn 随即 close 生成器），
    #   finally 里的 yield 会触发 "generator ignored GeneratorExit" RuntimeError，
    #   在 ASGI 层被打印成吓人的堆栈。正常路径发 done，被提前关闭时不发也不报错。
    yield {"type": "done", "run_id": run_id, "status": rec.status}


def _aggregate(init: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """把 stream 各节点的增量依次合并成一份接近终态的 state。

    ★ 必须逐个复刻 state 上挂的 reducer 语义，否则摘要会骗人：
    `tool_calls` / `actions_taken` / `forbidden_hits` 都是 operator.add，
    `work_notes` 是 dict 合并，`messages` 是追加。

    这里踩过一个真实的坑：`actions_taken` 漏在加法列表外，加上调用方
    用 `dict.update` 而非逐个合并，最后一个节点（verifier，不调写工具）
    的空列表把 resolver 实际执行过的 `escalate_to_human` 冲掉了 ——
    CLI 上显示"状态：awaiting_human / 动作：无"这种自相矛盾的结论。
    """
    state = dict(init)
    additive = ("tool_calls", "actions_taken", "forbidden_hits")
    for upd in chunks:
        for k, v in (upd or {}).items():
            if k in additive:
                state[k] = list(state.get(k) or []) + list(v or [])
            elif k == "work_notes":
                state["work_notes"] = {**(state.get("work_notes") or {}), **(v or {})}
            elif k == "messages":
                state["messages"] = list(state.get("messages") or []) + list(v or [])
            else:
                state[k] = v
    return state


def run_once(
    tenant_id: str,
    ticket_id: int,
    arm: str = "multi",
    user_input: str = "",
) -> RunRecord:
    """非流式跑完一张工单（CLI / 测试用）。"""
    rid: str | None = None
    for ev in stream_run(tenant_id, ticket_id, arm=arm, user_input=user_input):
        if ev["type"] == "run_started":
            rid = ev["run_id"]
    if rid is None:  # 理论上不可达：stream_run 第一帧必是 run_started
        raise RuntimeError("stream_run 未产出 run_started 事件")
    rec = run_store.get(rid)
    if rec is None:
        raise RuntimeError(f"run {rid} 未登记到 run_store")
    return rec


# ══════════════════════════════════════════════════════════════
# 客户答复（#2 多轮澄清闭环的第二轮）
# ══════════════════════════════════════════════════════════════


def submit_customer_reply(
    tenant_id: str,
    ticket_id: int,
    reply: str,
    arm: str = "multi",
    dry_run: bool = False,
) -> dict[str, Any]:
    """客户答复到达 → 落库 → 以答复为输入重跑 agent（第二轮）。

    ★ 为什么答复要**重跑图**，而不是让 agent 在上一轮里"顺便"把问题问完：

    agent 的第一轮只能看到客户原话；客户补的信息是**之后才到的外部事件**。
    把它重跑一遍，第二轮 worker 拿到的 `user_input` 就是答复本身，
    而 `pending_question`（上一轮问了什么）已经从库里读得到 ——
    于是"问 → 等 → 答 → 办"这条链路在系统里是**四段都可追溯的**，
    而不是一段加了密的长对话。

    ★ 为什么这里不自己做第二轮，而是复用 `stream_run`：

    `stream_run` 已经处理好了 SSE、run 记录、异常兜底、状态聚合。
    在这里另写一份"第二轮专用"的执行路径，等于把同一套流程实现两遍 ——
    这正是本项目反复吃亏的形状（golden 与工具两遍实现规则，
    缺陷 ① ② ⑥ 全是它）。

    ★ `dry_run=True` 只落库不重跑（供 CLI 预览"这条答复会改变什么"）。
    """
    applied = apply_customer_reply(tenant_id, ticket_id, reply)
    if not applied.get("ok"):
        return applied
    if dry_run:
        return {**applied, "ran": False}
    rec = run_once(tenant_id, ticket_id, arm=arm, user_input=(reply or "").strip())
    return {**applied, "ran": True, "run_id": rec.run_id, "status": rec.status}


# ══════════════════════════════════════════════════════════════
# 人工审批（HITL）
# ══════════════════════════════════════════════════════════════


def submit_customer_reply(
    tenant_id: str,
    ticket_id: int,
    reply: str,
    *,
    arm: str = "multi",
    dry_run: bool = False,
) -> dict[str, Any]:
    """#2 多轮澄清闭环：客户答复到达 → 落库 → 以答复为输入重跑。

    分工（与 `POST /runs` 的区别）：`/runs` 是**第一轮**（客户原话进来），
    这里是**第二轮**（答复进来）。两条路径共用同一张图，差别只在输入是什么。

    ★ `dry_run=True` 只落库不重跑：排查"这条答复到底解析出了什么"时用，
      免得为了看一眼解析结果就烧一轮模型调用。

    ★ 幂等：重复提交同一条答复会再跑一轮（客户可能补充新信息），
      但 `customer_reply` 列只留最后一次 —— 全量答复历史在
      `audit_log`（actor=`customer`）里，那里才是追加语义。
    """
    applied = apply_customer_reply(tenant_id, ticket_id, reply)
    if not applied.get("ok"):
        err = applied.get("error")
        if err == "ticket_not_found":
            raise LookupError(f"ticket {ticket_id} not found in tenant {tenant_id}")
        raise ValueError(f"customer reply rejected: {err}")
    if dry_run:
        return {
            "ticket_id": ticket_id,
            "tenant_id": tenant_id,
            "applied": applied.get("changed", {}),
            "nothing_to_apply": applied.get("nothing_to_apply", False),
            "reran": False,
        }

    rec = run_once(tenant_id, ticket_id, arm=arm, user_input=reply)
    return {
        "run_id": rec.run_id,
        "ticket_id": ticket_id,
        "tenant_id": tenant_id,
        "applied": applied.get("changed", {}),
        "nothing_to_apply": applied.get("nothing_to_apply", False),
        "status": rec.status,
        "final_state": rec.state,
        "snapshot": rec.snapshot,
        "error": rec.error,
        "reran": True,
    }


def approve_run(
    run_id: str,
    tenant_id: str,
    decision: Decision,
    note: str = "",
    amount: float | None = None,
    override: bool = False,
) -> dict[str, Any]:
    """人工裁决（按 run 定位）。

    ★ 只适用于"创建这条 run 的进程还在"的场景：HTTP 服务、测试、
      同进程脚本（先 run_once 再 approve）。
    ★ 跨进程不要用这个 —— RunStore 是**进程内内存**，换一个进程拿 run_id
      什么也查不到（这正是缺陷 ⑦）。CLI 的 `pending` 与 `approve` 分属两条
      命令、两个进程，所以 CLI 走 `approve_ticket`。
    """
    rec = run_store.get(run_id)
    if rec is None:
        raise LookupError(f"run {run_id} 不存在")
    if rec.tenant_id != tenant_id:
        # 越权访问他人 run —— 报 404 而不是 403，避免探测出 run_id 是否存在
        raise LookupError(f"run {run_id} 不存在")
    return _approve_core(
        tenant_id=tenant_id,
        ticket_id=rec.ticket_id,
        decision=decision,
        note=note,
        amount=amount,
        override=override,
        run_id=run_id,
        rec=rec,
    )


def approve_ticket(
    tenant_id: str,
    ticket_id: int,
    decision: Decision,
    note: str = "",
    amount: float | None = None,
    override: bool = False,
    run_id: str = "",
) -> dict[str, Any]:
    """人工裁决（★ 按工单定位，跨进程可用）。

    ★★ 缺陷 ⑦：工单是**库里的持久对象**，run 是 RunStore 里的**进程内内存对象**。
    CLI 的 `pending` 列的是库里的工单，而 `approve` 原先只认 run_id ——
    于是"看到待办"与"裁决待办"一旦分属两个进程，这条最自然的人工流程就是断的
    （实测：`pending` 列出 1 张工单，`approve <run_id>` 报 `run ... 不存在`）。

    人工审批的自然主语本来就是"**哪张工单要判**"，不是"哪次运行"。
    `run_id` 因此变成可选：传了就写进审计与 override 记录（能对上是被哪次
    agent 运行转过来的人工），不传就只记工单 —— 裁决本身不依赖它。
    """
    return _approve_core(
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        decision=decision,
        note=note,
        amount=amount,
        override=override,
        run_id=run_id,
        rec=None,
    )


def _approve_core(
    *,
    tenant_id: str,
    ticket_id: int,
    decision: Decision,
    note: str,
    amount: float | None,
    override: bool,
    run_id: str,
    rec: RunRecord | None,
) -> dict[str, Any]:
    """两个入口共用的实现。

    `rec is None` 表示这次裁决与某个具体 run 无关（跨进程按工单裁决）：
    此时不更新 run 记录，但**政策护栏、审计日志、快照导出全部照旧** ——
    缺的只是"这条 run 记录的状态跟着翻页"，而不是任何一笔钱或一条痕迹。

    ★ 复用工具层而不是直接写表：幂等键、审计日志、refund_count 维护、
    状态机校验都在工具里，绕过去写等于把规则实现两遍。
    ★ 人工动作以 actor="human" 落审计，与 agent 的动作在同一条时间线上，
    评测/复盘时能分清"是谁办的"。

    ★★ 关于 `override`（修复缺陷 ⑥ 的产物）：

    人工审批**不能**绕过政策护栏。`escalate` 这一个动作名底下有两种语义：

    - **推迟（R4：黑名单/高风险/大额）**：政策允许退，只是要人工把关 ——
      审批通过就正常退款，`override` 不需要置 true。
    - **禁止（R5：同一订单已退过 / R6：生鲜完全不退）**：政策不允许这笔钱
      出去。此时本函数会带着 `decide_for_order` 的结论调 `create_refund`，
      写路径拒绝创建，最终抛 `PolicyViolation`（HTTP 409）。

    原实现直接以 `pay = order.amount` 写退款，实测把 `acme-t17`
    （R5，已退 47.53 元的图书订单）退成了合计 95.06 元。

    `override=True` 是留给"人工确实要破例"的显式出口：它会一路传到
    `create_refund(allow_policy_override=True)`，**在审计里留下 override 痕迹**，
    并在返回值里带 `policy_override: True`。设计取向是：人可以压过政策，
    但这件事必须是一次**被记录的决定**，而不是一个静默的漏洞。
    """
    if decision not in ("approve_refund", "reject", "keep_pending"):
        raise ValueError(f"未知决定 {decision!r}")

    with session_scope() as s:
        repo = TenantRepo(s, tenant_id)
        t = repo.get_ticket(ticket_id)
        if t is None:
            raise LookupError(f"工单 {ticket_id} 不存在")
        order, basis = resolve_ticket_order(repo, t)
        if decision in ("approve_refund", "reject") and t.status != "pending_human":
            raise ValueError(f"工单状态为 {t.status}，只有 pending_human 的工单需要人工裁决")
        pay = float(amount if amount is not None else (order.amount if order else 0.0))

        # 批准退款前先取权威判定：把「禁止」与「推迟」区分开
        verdict = None
        if decision == "approve_refund" and order is not None:
            items = repo.list_order_items(order.id)
            cust = repo.get_customer(order.customer_id)
            refunds = repo.list_refunds_of_order(order.id)
            verdict = decide_for_order(
                order, items=items, customer=cust, refund_count=len(refunds)
            )

    # 金额合理性：人工可以调整金额，但不能把订单退超
    if decision == "approve_refund" and order is not None:
        if pay < 0:
            raise ValueError(f"退款金额不能为负：{pay}")
        if pay > float(order.amount or 0.0) + settings.eval_amount_tolerance_cny:
            raise ValueError(
                f"退款金额 {pay} 元超过订单金额 {order.amount} 元，人工也不得超额退款"
            )

    set_tool_context(tenant_id, ticket_id, actor="human", run_id=run_id)
    performed: list[dict[str, Any]] = []
    policy_override = False

    if decision == "keep_pending":
        performed.append({"action": "keep_pending", "note": note or "人工确认维持转人工状态"})
    elif decision == "reject":
        out = registry.get("close_ticket", "resolver").invoke(
            {"resolution": note or "人工复核后驳回客户诉求", "status": "rejected"}
        )
        performed.append({"action": "close_ticket(rejected)", "result": out})
    else:
        if order is None:
            raise ValueError("该工单没有可处置的订单，无法批准退款")

        if verdict is not None and refund_is_forbidden(verdict) and not override:
            # 政策禁止：不开退款单，改成向客户说明政策并关闭工单。
            # 抛 PolicyViolation（ValueError 子类）→ HTTP 层自动映射 409。
            rule = verdict.rule_hits[0] if verdict.rule_hits else verdict.action
            raise PolicyViolation(
                f"政策禁止该订单退款（{rule}）：{'；'.join(verdict.reasons)}。"
                "人工审批不能绕过政策；如确需破例，请显式传 override=True（会记入审计）。"
            )

        if verdict is not None and refund_is_forbidden(verdict):
            policy_override = True
            _audit_human_override(tenant_id, ticket_id, run_id, verdict, pay)

        refund_out = registry.get("create_refund", "resolver").invoke(
            {
                "order_id": order.id,
                "amount": pay,
                "reason": note or "人工审批通过，同意退款",
                "requires_human": False,
                # 只有显式 override 时才把放行权交给写路径；否则由它自己按政策拒绝
                "allow_policy_override": policy_override,
            }
        )

        # ★ 只在退款真的落库时才关单。写路径若因护栏拒绝了
        # （policy_forbidden / amount_exceeds_order），这里必须**报错而不是
        # 静默关单** —— 否则会出现"工单显示已解决、钱一分没退"的静默失真，
        # 而且 rec.status 会被置成 finished、人工待办队列里也查不到它了。
        if isinstance(refund_out, dict) and refund_out.get("error"):
            raise PolicyViolation(
                refund_out.get("message") or f"退款未创建：{refund_out['error']}"
            )

        performed.append({"action": "create_refund", "result": refund_out})
        close_out = registry.get("close_ticket", "resolver").invoke(
            {"resolution": note or f"人工批准退款 {pay} 元", "status": "resolved"}
        )
        performed.append({"action": "close_ticket(resolved)", "result": close_out})

    with session_scope() as s:
        snapshot = export_snapshot(s, tenant_id, ticket_id)

    if rec is not None:
        # 与某个具体 run 绑定的裁决：同步那条 run 记录，让运行列表与待办队列一致。
        # 按工单裁决（rec is None）时没有 run 可翻页 —— 状态真相在库里，
        # 而库里的工单状态已经被上面的工具调用改掉了。
        rec.snapshot = snapshot
        rec.status = "finished"
        rec.state = {
            **(rec.state or {}),
            "human_decision": decision,
            "requires_human_approval": False,
        }

    return {
        "run_id": run_id or None,
        "ticket_id": ticket_id,
        "decision": decision,
        "policy_override": policy_override,
        "performed": _jsonable(performed),
        "snapshot": _jsonable(snapshot),
    }


def pending_human_tickets(tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """人工待办队列。"""
    from sqlalchemy import select

    from .db.models import Order, Ticket

    with session_scope() as s:
        rows = s.execute(
            select(Ticket)
            .where(Ticket.tenant_id == tenant_id, Ticket.status == "pending_human")
            .order_by(Ticket.created_at.desc())
            .limit(limit)
        ).scalars().all()
        out = []
        for t in rows:
            o = s.get(Order, t.order_id) if t.order_id else None
            out.append(
                {
                    "ticket_id": t.id,
                    "subject": t.subject,
                    "priority": t.priority,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "order_no": o.order_no if o else None,
                    "amount": o.amount if o else None,
                    "escalated_at": t.closed_at.isoformat() if t.closed_at else None,
                }
            )
        return out


def runtime_info() -> dict[str, Any]:
    """健康检查/自述：当前用哪个模型、哪些角色有哪些工具。"""
    from .graph.nodes import get_model_kind
    from .tools.registry import ROLE_TOOL_MATRIX

    return {
        "model_kind": get_model_kind(),
        "model_name": settings.model_name,
        "has_api_key": bool(settings.openai_api_key),
        "roles": {r: list(ns) for r, ns in ROLE_TOOL_MATRIX.items()},
        "write_tools": sorted(n for n in ROLE_TOOL_MATRIX["single"] if registry.is_write(n)),
        "budget": {
            "max_tokens_per_ticket": settings.max_tokens_per_ticket,
            "max_cost_per_ticket_cny": settings.max_cost_per_ticket_cny,
            "degrade_at_ratio": settings.degrade_at_ratio,
        },
        "thresholds": {
            "refund_hitl_threshold_cny": settings.refund_hitl_threshold_cny,
            "risk_score_hitl_threshold": settings.risk_score_hitl_threshold,
            "return_window_days": settings.return_window_days,
            "max_refunds_per_order": settings.max_refunds_per_order,
        },
    }


def dumps(obj: Any) -> str:
    return json.dumps(_jsonable(obj), ensure_ascii=False)
