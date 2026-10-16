"""图节点实现。

★ 关于版本稳健性的一个工程决定：

LangChain 1.0 的 `create_agent` 与 middleware hook 名在小版本间调整过。为了不让
"框架升级导致项目跑不起来"，这里做了一个**降级策略**：

    优先 create_agent（能用框架的中间件、结构化输出、内置循环）
       ↓ 不可用或签名不匹配
    自建 ReAct 循环（直接调 model.bind_tools()，手工跑 tool_calls）

两条路径的**图拓扑、状态、评测口径完全一致**，所以指标可比。
代价是代码多 60 行，收益是这个项目在依赖升级后仍然能跑 —— 面试时这也是个可讲的点。
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from ..config import settings
from ..db.corpus import classify_cause, extract_amount, presupposes_receipt
from ..db.models import AuditLog
from ..db.session import TenantRepo, session_scope
from ..db.snapshot import diff_snapshots, export_snapshot
from ..tools.registry import (
    ROLE_TOOL_MATRIX,
    WRITE_TOOLS,
    _audit,
    registry,
    set_tool_context,
)
from .middleware import BudgetExceeded, BudgetTracker, merge_budget
from .prompts import (
    RESOLVER_PROMPT,
    SINGLE_PROMPT,
    SUPERVISOR_PROMPT,
    TRIAGE_PROMPT,
    VERIFIER_PROMPT,
)
from .state import AgentState

#: 写工具集合。从 registry 取，**不在这里重写一份** —— 两处各写一份是
#: 规则漂移的经典入口（这份列表决定哪些调用要跳过只读去重缓存）。
WRITE_TOOL_NAMES = WRITE_TOOLS

ROLE_PROMPTS = {
    "triage": TRIAGE_PROMPT,
    "resolver": RESOLVER_PROMPT,
    "verifier": VERIFIER_PROMPT,
    "single": SINGLE_PROMPT,
}

MAX_AGENT_STEPS = 12
"""单个 worker 内的最大 ReAct 步数。超了强制收尾，避免烧钱。"""


# ══════════════════════════════════════════════════════════════
# 模型构造
# ══════════════════════════════════════════════════════════════


#: 模型后端选择。"" 时按有无 API key 自动判断；显式设成 scripted/flaky/overprivileged
#: 则强制走脚本化模型（离线复现、故障注入、越权测试都靠它）。
_MODEL_KIND: str = ""


def set_model_kind(kind: str) -> None:
    """强制指定模型后端。评测 harness 用它切换 arm。"""
    global _MODEL_KIND
    _MODEL_KIND = kind


def get_model_kind() -> str:
    if _MODEL_KIND:
        return _MODEL_KIND
    # ★ 这里不能只看 `settings.openai_api_key` 了：多端点下 key 可能来自
    #   档案指定的环境变量（如 HUNYUAN_API_KEY / RELAY_API_KEY），也可能
    #   根本不需要 key（本地 Ollama）。判据统一收敛到 Endpoint.is_configured，
    #   否则会出现"用本地模型跑起来却掉回 scripted 基线"这种静默降级 ——
    #   而 scripted 是规则基线，它跑出来acc 100%，看上去像"模型效果很好"。
    return "llm" if settings.endpoint.is_configured else "scripted"


def build_model(cheap: bool = False, role: str = "single") -> Any:
    """构造模型。

    - `kind == "llm"`   → ChatOpenAI（真实 LLM）
    - `kind == "scripted"` / `"flaky"` / `"overprivileged"` → 脚本化确定性模型

    ★ 为什么要这个分支：没有 API key 时整个项目仍要能跑（CI、离线复现、
    以及做「规则引擎基线 vs LLM」的对照）。两条路径插在**完全相同的 ReAct 循环**里，
    所以工具调用、权限裁剪、终态判定口径都一致，指标可比。

    ★ 端点（base_url / 模型名 / key）统一从 `settings.endpoint` 取，不再直接读
    `settings.openai_*`：多端点下这三样可能来自档案、可能来自各自的环境变量。
    详见 config.py 里 Endpoint 的说明。
    """
    kind = get_model_kind()
    if kind != "llm":
        from ..agents.scripted import get_model

        return get_model(role, kind)

    from langchain_openai import ChatOpenAI

    ep = settings.endpoint
    name = ep.cheap_model if cheap else ep.model
    return ChatOpenAI(
        model=name,
        # ★ 本地端点不需要 key，但 ChatOpenAI 不接受空串；给一个占位值。
        #   占位串写死成 "not-needed" 而不是 "sk-xxx"，是为了让它一旦
        #   真的发到远端就在日志里显眼 —— 说明 key 没配上却打到了外部端点。
        api_key=ep.api_key or "not-needed",
        base_url=ep.base_url,
        temperature=settings.temperature,
        timeout=settings.request_timeout,
        max_retries=2,
    )


def _digest(obj: Any, n: int = 180) -> str:
    """把工具返回值压成短摘要写进 trace，避免评测产物过大。"""
    try:
        s = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        s = str(obj)
    s = s.replace("\n", " ")
    return s[:n] + ("…" if len(s) > n else "")


def _call_sig(name: str, args: dict[str, Any]) -> str:
    return f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)}"


def _tool_error_code(result: Any) -> str:
    """取工具返回里的错误码；不是错误返回空串。

    ★ 缺陷 ⑫ 的现场：工具层的拒绝走的是**正常返回**而不是抛异常
    （`create_refund` 的护栏返回 `{"error": "policy_forbidden", ...}`，
    另有 `amount_exceeds_order` / `order_not_found` / `invalid_status` 等，
    全项目 13 处）。而本函数原来的调用点只写 `ok = True`（只要没抛异常），
    于是**一次被护栏拦下的越权写操作，在过程指标里和一次成功的写操作
    完全无法区分** —— 实测表现为 `lenient` 故障模型掉 34 个点却报 0 违规。

    把"错误码"抽成一个函数，是为了让判定和读取用同一套规则：判定说这次调用
    失败了，读取就能拿到失败原因，不会出现"记为失败但说不出为什么"。
    """
    if isinstance(result, dict):
        err = result.get("error")
        if isinstance(err, str) and err:
            return err
    return ""


#: 有实质副作用的写工具 —— 失败时"什么都没办成"的那一类。
#: 与 `WRITE_TOOL_NAMES` 的区别：`close_ticket` 是告一段落的动作，它自己的失败
#: 不构成"承诺了没办成的事"，故不在此列（见 `eval/harness.py` 的探针 5）。
SUBSTANTIVE_WRITE_TOOLS: tuple[str, ...] = ("create_refund", "block_order", "escalate_to_human")


def _is_duplicate_call(cache: dict[str, Any], name: str, args: dict[str, Any]) -> bool:
    """只读调用是否命中缓存签名。"""
    return _call_sig(name, args) in cache


# ══════════════════════════════════════════════════════════════
# 通用 ReAct 执行器（两条路径共用）
# ══════════════════════════════════════════════════════════════


def _execute_tool_calls(
    tool_calls: list[dict[str, Any]],
    role: str,
    prev_trace: list[dict[str, Any]],
    *,
    dry_run: bool = False,
) -> tuple[list[ToolMessage], list[dict[str, Any]], list[str], list[str]]:
    """执行一批工具调用。返回 (tool_messages, trace_records, actions, forbidden_hits)。

    ★ 权限裁剪在这里生效：`registry.get(name, role)` 会拒绝未授权工具。
    模型即使在输出里"幻觉"出一个工具名，或试图调用别的角色的工具，也会在这里被拦下，
    **不会静默执行**。
    """
    msgs: list[ToolMessage] = []
    records: list[dict[str, Any]] = []
    actions: list[str] = []
    forbidden: list[str] = []

    # ★ 去重缓存的两个关键设计（早前都错过）：
    #  1) **按角色隔离**：跨角色不该互相短路 —— resolver 必须读到 triage 之后、
    #     乃至自己写入之后的新状态；全局去重会让它拿到旧数据。
    #  2) **回放真实结果**：命中缓存时必须返回当初那份数据，而不是
    #     `{"cached": true}` 这种空壳 —— 空壳会让下游 `_last_result()` 读出
    #     "没有 customer_id 的工单"，进而误判成"工单缺客户信息，转人工"。
    cache: dict[str, Any] = {}
    for p in prev_trace:
        sig = p.get("_sig")
        if sig and p.get("actor") == role and "_result" in p:
            cache[sig] = p["_result"]

    seq = len(prev_trace)
    for tc in tool_calls:
        name = tc.get("name", "")
        args = tc.get("args", {}) or {}
        tcid = tc.get("id", f"call_{seq}")
        seq += 1
        t0 = time.perf_counter()

        # ① 角色权限校验 —— 越权即拦截并记录
        if name not in ROLE_TOOL_MATRIX.get(role, ()):
            forbidden.append(f"{role} 越权调用 {name}")
            payload = {
                "error": "permission_denied",
                "message": (
                    f"角色 {role} 没有 {name} 的权限。可用工具："
                    f"{list(ROLE_TOOL_MATRIX.get(role, ()))}"
                ),
            }
            msgs.append(ToolMessage(content=json.dumps(payload, ensure_ascii=False), tool_call_id=tcid, name=name))
            records.append(
                {
                    "seq": seq,
                    "actor": role,
                    "name": name,
                    "args": args,
                    "ok": False,
                    "ms": int((time.perf_counter() - t0) * 1000),
                    "result_digest": "permission_denied",
                }
            )
            continue

        # ② 只读重复调用短路（回放真实结果）
        if name not in WRITE_TOOL_NAMES:
            sig = _call_sig(name, args)
            if sig in cache:
                result = cache[sig]
                msgs.append(
                    ToolMessage(
                        content=json.dumps(result, ensure_ascii=False, default=str),
                        tool_call_id=tcid,
                        name=name,
                    )
                )
                records.append(
                    {
                        "seq": seq, "actor": role, "name": name, "args": args,
                        "ok": True, "ms": 0, "result_digest": "deduped",
                        "_sig": sig, "_result": result,
                    }
                )
                continue

        # ③ 真正执行
        try:
            tool: BaseTool = registry.get(name, role)
            result = tool.invoke(args)
            # ★ 缺陷 ⑫：`ok` 必须同时反映**抛异常**和**正常返回的错误字典**。
            #   只看异常会让"被政策护栏拦下的写操作"记成成功 —— 实测中
            #   `lenient` 故障模型的 `create_refund` 被 `policy_forbidden` 挡住，
            #   轨迹里 `ok=True`、违规计数 0，于是它掉 34 个点却像"没有违规"。
            err_code = _tool_error_code(result)
            ok = not err_code
        except BudgetExceeded:
            raise
        except Exception as exc:  # 工具内部错误不应该炸掉整个图
            result = {"error": type(exc).__name__, "message": str(exc)[:300]}
            ok = False
            err_code = type(exc).__name__

        msgs.append(
            ToolMessage(
                content=json.dumps(result, ensure_ascii=False, default=str),
                tool_call_id=tcid,
                name=name,
            )
        )
        rec = {
            "seq": seq, "actor": role, "name": name, "args": args, "ok": ok,
            "ms": int((time.perf_counter() - t0) * 1000),
            "result_digest": _digest(result),
        }
        # 失败原因单独留一栏：`ok=False` 只说"失败了"，报表要回答"被什么挡的"。
        # 不塞进 `result_digest` 解析，那种做法会让归因依赖于摘要格式。
        if not ok:
            rec["error_code"] = err_code
        # 只给只读工具打去重签名（写工具必须每次真执行，靠幂等键保证不重复副作用）
        if name not in WRITE_TOOL_NAMES:
            rec["_sig"] = _call_sig(name, args)
            rec["_result"] = result
        records.append(rec)

        # 只有**真的执行成功**的写操作才算 agent 的动作。
        # ★ 否则 `_summarize_action` 会把"退款被挡下"读成"agent 退了款"，
        #   报表里这单看上去走了退款路径，实际钱没动 —— 第四处隐形。
        if name in WRITE_TOOL_NAMES and ok:
            actions.append(f"{name}({_digest(args, 80)})")

    return msgs, records, actions, forbidden


def apply_customer_reply(tenant_id: str, ticket_id: int, reply: str) -> dict[str, Any]:
    """把客户答复落库，并**同步刷新规则要读的那几个任务输入字段**。

    返回本次刷新了什么（审计与报表用）。它**不重跑图** —— 重跑由调用方
    （`service.submit_customer_reply` / `eval.harness.run_case`）决定，
    因为服务层还要管 SSE 与 run 记录。

    ★ 为什么答复能改变 `request_cause` / `request_amount` / `evidence_provided`：
    这三个字段本来就是**从客户原话里抽出来的任务输入**（`seed.py` 造工单时
    就是这么造的：`corpus.classify_request(body)` / `classify_cause` /
    `extract_amount`）。第一轮客户没说全，规则正确地返回
    `need_clarification`；第二轮补上了，就该按补上之后的输入重算 ——
    这不是"改答案"，是"输入变了"。

    ★ 为什么不让 agent 自己写：本项目铁律是「verifier 不许读模型自己写的散文」
    （缺陷 ⑩⑨ 的成因）。答复文本也一样 —— 让模型自由决定"客户答的是什么"，
    就等于让被评测者自己填标准答案。这里用**造数侧同一个 corpus 分类器**
    （`classify_cause` / `extract_amount` / `detect_evidence_type`），
    确定性、可单测、可复现。

    ★ 为什么不动 `request_kind`：客户答复的是"缺的那一项"（金额/原因/凭证），
    不是改变诉求本身。改诉求会绕过 `classify_request` 的判据，而那个函数
    同时被 seed 和 `pick` 的 `kinds` 筛选用 —— 两边必须一致。

    ★ 返回值带 `ok=False` 的几种情形要分清：
      - `empty_reply`：空答复（API 层由 pydantic 的 min_length 先挡）
      - `ticket_not_found`：调用方给错了 id（API 层返回 404）
      - `ticket_closed`：工单已办结。真实系统里答复只会到达未办结的工单；
        返回错误而不是静默写进去 —— 否则第二轮会拿一个已 resolved 的工单
        重跑，终态 diff 全是噪声（本函数第一次上线时就撞到过这个）。
      - `nothing_to_apply`：答复里解析不出任何可写字段（**不是错误**，
        例如客户只回"好的"）—— 工单仍是 open 且 `pending_question` 保留，
        第二轮跑完 verifier 会如实判"仍在等客户"。
    """
    reply = (reply or "").strip()
    if not reply:
        return {"ok": False, "error": "empty_reply"}

    with session_scope() as s:
        repo = TenantRepo(s, tenant_id)
        t = repo.get_ticket(ticket_id)
        if t is None:
            return {"ok": False, "error": "ticket_not_found"}
        if t.status in ("resolved", "rejected"):
            return {"ok": False, "error": "ticket_closed", "status": t.status}

        t.customer_reply = reply[:2000]
        # 问过的问题已回答 → 清掉，verifier 才能区分
        # "在等客户"（pending_question 非空）与"客户答了但还没办"
        # （pending_question 空、status 仍 open）
        t.pending_question = ""
        changed: dict[str, Any] = {"customer_reply": True}
        # ★ 拒收的凭证也要留痕（#60）：「客户交了凭证但这份凭证不管用」是
        #   一条独立于「有没有交」的信息，没它就只能从 agent 的散文里猜。
        rejected: list[str] = []

        # ① 原因：只在**原本缺**时补，不覆盖已有的
        if not t.request_cause:
            cause = classify_cause(reply, t.request_kind or "")
            if cause:
                t.request_cause = cause
                changed["request_cause"] = cause

        # ② 金额：只在**原本没提**时补（赔付 / 价保要用）
        if not (t.request_amount and t.request_amount > 0):
            amount = extract_amount(reply)
            if amount > 0:
                t.request_amount = amount
                changed["request_amount"] = amount

        # ③ 凭证：客户答复里带了"照片 / 截图 / 单号"就视作已提供。
        #    ★ 判据必须是确定性的，且与 `detect_evidence_type` 同源 ——
        #      不能让 agent 自己判断"这算不算给了凭证"。
        # ③ 凭证：答复里带了凭证词**不等于**凭证可用（#60）。
        #    判定拆成两步，与 `mark_evidence_received` 完全同源：
        #      ① `detect_evidence_type` —— 确定性地认出是哪一类凭证；
        #      ② `check_evidence`        —— 这类凭证能不能支撑这次索赔的原因。
        #    ★ 第 ② 步才是 #60 补的那一半。原来这里只做 ①，
        #      而且 ① 的词表里还混着「订单号 / 运单号」—— 那两个是
        #      **业务钥匙**不是凭证：它们证明「买过」，证明不了「坏了」。
        #      拿它们当凭证受理换货，是缺陷 ⑫「护栏守住钱没守住谎」的低配版。
        if not t.evidence_provided:
            ev_type = detect_evidence_type(reply)
            if ev_type:
                from ..rules import check_evidence

                ev_ok, why = check_evidence(ev_type, t.request_cause or "")
                if ev_ok:
                    t.evidence_provided = True
                    changed["evidence_provided"] = True
                    changed["evidence_type"] = ev_type
                else:
                    rejected.append(why)

        # 注意：这里显式传 ticket_id / tenant_id —— `_audit` 默认从
        # `_CTX`（工具上下文）取，而这条路径发生在**图外**，没有工具上下文。
        _audit(
            "customer",
            "submit_customer_reply",
            {"ticket_id": t.id, **changed},
            ticket_id=t.id,
            tenant_id=tenant_id,
        )

    return {
        "ok": True,
        "ticket_id": ticket_id,
        "changed": changed,
        # 什么都没解析出来也要说清楚，而不是假装写成功了
        "nothing_to_apply": not [k for k in changed if k != "customer_reply"],
        # ★ 被拒收的凭证也要报出来（#60）：「交了但这份凭证证明不了这件事」
        #   与「根本没交」是两种失败，agent 得知道是哪一种才能正确地追问。
        "rejected": rejected,
    }


def _run_react_loop(
    role: str,
    messages: list[Any],
    tracker: BudgetTracker,
    prev_trace: list[dict[str, Any]],
    *,
    dry_run: bool = False,
) -> tuple[list[Any], list[dict[str, Any]], list[str], list[str], str]:
    """自建 ReAct 循环。

    返回 (新增消息, **本轮新增**的 trace, actions, forbidden, 最终文本)。

    ★ 只返回新增记录：`tool_calls` 在 state 里挂了 `operator.add` reducer，
    若这里把"历史+新增"整份返回，每过一个节点 trace 就会翻倍。
    `prev_trace` 只用于两件事：seq 编号的起点、以及只读去重的历史缓存。
    """
    tools = registry.get_tools(role)
    model = build_model(cheap=tracker.degraded, role=role).bind_tools(tools)

    new_msgs: list[Any] = []
    history = list(prev_trace)
    produced: list[dict[str, Any]] = []
    actions: list[str] = []
    forbidden: list[str] = []
    convo = list(messages)
    final_text = ""

    for _step in range(MAX_AGENT_STEPS):
        try:
            ai: AIMessage = model.invoke(convo, config={"callbacks": [tracker]})
        except BudgetExceeded:
            final_text = "[预算熔断] 处置未完成，转人工。"
            new_msgs.append(AIMessage(content=final_text))
            break
        except Exception as exc:
            final_text = f"[模型调用失败] {type(exc).__name__}: {exc}"
            new_msgs.append(AIMessage(content=final_text))
            break

        new_msgs.append(ai)
        convo.append(ai)
        final_text = ai.content if isinstance(ai.content, str) else str(ai.content or "")

        calls = getattr(ai, "tool_calls", None) or []
        if not calls:
            break

        t_msgs, recs, acts, forb = _execute_tool_calls(
            calls, role, history, dry_run=dry_run
        )
        new_msgs.extend(t_msgs)
        convo.extend(t_msgs)
        history.extend(recs)
        produced.extend(recs)
        actions.extend(acts)
        forbidden.extend(forb)

        if tracker.tripped:
            break

    return new_msgs, produced, actions, forbidden, final_text


# ══════════════════════════════════════════════════════════════
# 节点：worker
# ══════════════════════════════════════════════════════════════


def _escalate_on_trip(tenant_id: str, ticket_id: int, budget: dict[str, Any]) -> dict[str, Any]:
    """预算熔断时把工单转人工。

    ★ 这个函数早前**被调用但从未定义**（`nodes.py:362` 是它唯一的引用），
    一旦预算真被烧穿就会 `NameError` —— 而它所在的代码路径平时跑不到，
    所以这个 bug 在"正常跑几轮"的冒烟测试里永远暴露不出来。

    ★ 语义要点：熔断**不等于什么都不做**。预算烧穿的典型场景恰恰是"模型开始绕圈
    或者碰上难缠工单"，此时最危险的结果是工单被静静丢在原地 —— 客户以为有人管。
    所以熔断必须留下一个明确的、可被人工接手的终态（`pending_human`），
    而且要写审计，这样"哪些单子是因为熔断没办完"在数据里查得到。

    这里刻意**复用 escalate_to_human 这个工具**而不是直接改表：
    走的副作用、幂等、审计字段与 agent 自己转人工时完全一致，
    评测才分得清"agent 判断该转人工"和"系统熔断兜底"。
    """
    reason = str(budget.get("tripped_reason") or "预算熔断")
    set_tool_context(
        tenant_id=tenant_id, ticket_id=ticket_id, actor="resolver", run_id="budget-trip"
    )
    try:
        tool = registry.get("escalate_to_human", "resolver")
        return tool.invoke(
            {
                "reason": f"[预算熔断兜底] {reason}",
                "suggested_action": "人工接手处置：模型预算已耗尽，本单未完成自动处置",
            }
        )
    except Exception as exc:  # 兜底路径自己不能把图炸了
        return {"error": type(exc).__name__, "message": str(exc)[:200]}


def _worker_node(state: AgentState, role: str) -> dict[str, Any]:
    """worker 通用节点实现。"""
    tenant_id = state["tenant_id"]
    ticket_id = state["ticket_id"]

    # ★ 每次进入 worker 都重设工具上下文 —— 保证工具拿到的 tenant/ticket 一定是本次 run 的
    set_tool_context(
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        actor=role,
        run_id=state.get("run_id", ""),
    )

    # 给 worker 一个"上手包"，避免它每次都从 get_ticket 重新了解背景
    notes = state.get("work_notes") or {}
    context_parts = [f"工单 ID：{ticket_id}，租户：{tenant_id}"]
    if state.get("user_input"):
        context_parts.append(f"客户原话：{state['user_input']}")
    for who, note in notes.items():
        context_parts.append(f"[{who} 已完成] {note}")
    if state.get("human_decision"):
        context_parts.append(f"[人工确认结果] {state['human_decision']}")

    base_msgs: list[Any] = [
        SystemMessage(content=ROLE_PROMPTS[role]),
        HumanMessage(content="\n".join(context_parts)),
    ]
    # 保留最近若干轮对话，避免上下文无限增长（SummarizationMiddleware 的手工等价物）
    hist = state.get("messages") or []
    if hist:
        base_msgs.extend(hist[-8:])

    tracker = BudgetTracker()
    try:
        new_msgs, trace, actions, forbidden, final_text = _run_react_loop(
            role, base_msgs, tracker, state.get("tool_calls") or []
        )
        tripped = own_tripped = False
    except BudgetExceeded as exc:
        tr = exc.tracker or tracker
        new_msgs = [AIMessage(content=f"[预算熔断] {tr.tripped_reason}，转人工处理。")]
        trace, actions, forbidden, final_text = [], [], [], new_msgs[0].content
        tripped = own_tripped = True

    if tracker.tripped:
        tripped = own_tripped = True

    budget = merge_budget(tracker, state.get("budget"))

    note = (final_text or "").strip().replace("\n", " ")[:300]
    if own_tripped:
        note = f"[预算熔断] {budget.get('tripped_reason', '')}"

    out: dict[str, Any] = {
        "messages": new_msgs,
        "tool_calls": trace,
        "actions_taken": actions,
        "forbidden_hits": forbidden,
        "budget": budget,
        "work_notes": {role: note},
        "final_answer": final_text if role in ("resolver", "single") else state.get("final_answer", ""),
    }

    # 熔断后直接走转人工，不再让后面的节点烧钱
    if own_tripped:
        out["finished"] = True
        out["requires_human_approval"] = True
        out["approval_request"] = {
            "ticket_id": ticket_id,
            "reason": f"预算熔断：{budget.get('tripped_reason', '')}",
        }
        _escalate_on_trip(tenant_id, ticket_id, budget)

    return out


def triage_node(state: AgentState) -> dict[str, Any]:
    """分诊节点：搞清楚客户要什么，必要时产出"需澄清"结论。"""
    return _worker_node(state, "triage")


def resolver_node(state: AgentState) -> dict[str, Any]:
    """处置节点：执行实际动作。"""
    return _worker_node(state, "resolver")


def verifier_node(
    state: AgentState,
    *,
    should_clarify: bool | None = None,
) -> dict[str, Any]:
    """校验节点：独立复核终态。

    `should_clarify`：**这条 case 的 golden 是否要求先澄清**（case 层事实，
    由造数时的 condition / turn2_state 决定，agent 改不了）。
    ★ 缺陷 ㉟：`pending_question` 由 agent 自己写，不能单独当放行依据 ——
      否则 agent 问一句就给自己开一张免检券（缺陷 ⑲ 的病根换了件衣服）。
      默认 None 表示调用方没给，此时退回只看 `basis`（保守：宁可漏放行）。
    """
    tenant_id, ticket_id = state["tenant_id"], state["ticket_id"]

    out = _worker_node(state, "verifier")

    # ★ verifier 的独立断言：直接把当前快照与"动作是否自洽"做机器校验，
    # 不依赖 LLM 判断 —— LLM 只负责解释，判定由代码做。
    with session_scope() as s:
        snap = export_snapshot(s, tenant_id, ticket_id)

    problems: list[str] = []
    ticket_snap = snap.get("ticket") or {}
    order_snap = snap.get("order")
    refunds = snap.get("refunds") or []

    status = ticket_snap.get("status")
    # ★ `basis` 必须提到 `if` 外面取：它下面两条断言都要用，而工单一旦
    #   不是 open 就会跳过 `if status == "open"` 整块 —— 放在块里等于
    #   "非 open 工单读一个没赋值的变量"（UnboundLocalError）。
    #   我第一版修复 ⑲ 时正是这么写的，当场被 test_writes_are_attributed_and_counted 逮住。
    basis = snap.get("order_basis")

    if status == "open":
        # 保持 open 只在「需澄清」时合理。
        #
        # ★ 这里曾经写错两处（缺陷 ⑩），两处都不报错、只是让断言永远判"不通过"：
        # ① 读的是 `out["work_notes"]` —— 而 `_worker_node` 只往自己的增量输出里放
        #    `{自己这个角色: note}`。verifier 的输出里只有 verifier 自己的笔记，
        #    triage 的笔记不在里面，于是 triage 明明写了"需要向客户澄清订单"，
        #    断言却读到空串，判"没有任何需澄清的依据"。**跨节点累积的笔记在 `state` 里，
        #    不在节点自己的增量输出里** —— 这是 reducer 语义读错，不是判断逻辑错。
        # ② 判定靠中文关键词匹配自由文本。`order_basis` 是快照里现成的**结构化事实**
        #    （ambiguous / no_order 意味着本来就没有可处置的订单），比在 prose 里找词可靠。
        #    ★ 缺陷 ⑲ 正是 ② 没改干净留下的后患：修 ⑩ 时把散文判据留下了，
        #      又把 `no_order` 和 `ambiguous` 并到一起 —— 详见下面。
        #
        # ★ 缺陷 ⑲（实测 22 条 multi 失败里 21 条由此漏放行）：
        #   这条判据把三件不同的事混成一句"需澄清所以 open 合理"。
        #
        #   1) `ambiguous` —— 候选订单多于 1 笔，正解确实是"保持 open、反问澄清"，
        #      24 条全部通过，放行**正确**，必须保留。
        #   2) `no_order`  —— 客户名下**一笔订单都没有**，正解是**转人工核实**
        #      （见 `seed.py:598-602`，golden 状态是 `pending_human`）。
        #      把它归进"需澄清"会让"什么都没做、订单还开着"被判通过 ——
        #      实测 `acme-t7` / `globex-t130`（k=1,2）三条全是这样漏过去的。
        #   3) `said_clarify` —— 中文关键词匹配**模型自己写的散文**。
        #      模型只要在笔记里写一句"需要与客户澄清订单"，就能给自己开一张通行证，
        #      哪怕它一个工具都没调用、一个动作都没产出。
        #
        #   机器断言不能建立在被审对象的**自我陈述**上。那 19 条 bound-order
        #   的漏处置全部走这条路径（basis 是 `linked`/`sole_order`，本就该处置）。
        #
        #   结论：**只认 `order_basis` 这个结构化事实**，它来自
        #   `classify_order_link` 纯函数，可复现、可单测；不认散文。
        #   （`basis` 已在上面 `if status == "open":` 之前取好，这里不再重复。）
        #
        # ★ #2（多轮澄清闭环）补上第二种结构化依据：`pending_question`。
        #
        #   上面三种情况只覆盖了"这张单**本来就**没有可处置的订单"，
        #   而政策里另有六处会返回 `need_clarification`（R8/R9/R10/R11
        #   缺凭证 / 缺原因 / 缺金额 / 缺现价）—— 那些工单的订单**是关联上的**
        #   （`linked` / `sole_order`），正解恰恰是"保持 open、先把缺的问清楚"。
        #   在此之前系统没有任何字段能表达"我已经问了客户"，于是它们全部
        #   落进这条断言，被记成"工单仍为 open，但没有任何需澄清的依据"。
        #
        #   修法不是放开断言（那会把 ⑲ 修好的漏处置又放回来），而是**补一个
        #   同样结构化的依据**：`pending_question` 由写工具 `ask_customer`
        #   落库、进审计日志、且只观测不参与终态判定。它与 `basis` 同级 ——
        #   都是"可复现、可单测的事实"，都不是模型自己写的散文。
        #
        #   ★ 为什么不再加"客户已答复"这条依据：`customer_reply` 非空意味着
        #     第二轮已经开始，此时若仍 open，说明 agent 拿到了答复却还是没办成 ——
        #     那是真的漏处置，不该被放行。所以答复**不算**澄清依据。
        # ★ 缺陷 ㉟：`pending_question` 是 agent 自己写进去的，与 `basis`
        #   （输入侧纯函数）不同级。只有**这条 case 本来就该澄清**时，它才是
        #   有效依据；否则它是"agent 自造的免检券"。
        asked = bool(ticket_snap.get("pending_question"))
        needs_clarification = basis == "ambiguous" or (
            asked and bool(should_clarify)
        )
        if asked and not needs_clarification:
            problems.append(
                "工单仍为 open，且 agent 自行向客户提问（pending_question 非空）—— "
                "但这条 case 的 golden 并不要求澄清，提问不能替代处置"
            )
        if not needs_clarification:
            problems.append("工单仍为 open，但没有任何需澄清的依据 —— 可能存在漏处置")
    if basis == "no_order" and status == "open":
        # no_order 的正解是转人工，不是维持 open 等澄清（缺陷 ⑲）。
        # 上面已经把 open 记成 problem 了，这里补一句可读的原因，
        # 免得报告里只看到"漏处置"却看不到"该转人工"。
        problems.append(
            "客户名下没有任何订单（order_basis=no_order），正解是转人工核实"
            "（ticket.status=pending_human），而不是保持 open 等澄清"
        )
    if order_snap and refunds:
        total = sum(float(r.get("amount") or 0) for r in refunds)
        order_amount = float(order_snap.get("amount") or 0)
        if total > order_amount + settings.eval_amount_tolerance_cny:
            problems.append(f"退款总额 {total} 超过订单金额 {order_amount}")
    if order_snap and order_snap.get("refund_count", 0) > settings.max_refunds_per_order:
        # 注意：转人工时也会先建 requested 退款，所以这里只作为提示不判失败
        problems.append("退款次数超过上限（若为转人工情形可接受）")

    out["verdict"] = {
        "passed": not problems,
        "diffs": problems,
        "notes": (out.get("work_notes") or {}).get("verifier", ""),
    }
    out["finished"] = True
    return out


# ══════════════════════════════════════════════════════════════
# 第二轮：客户答复到达（#2 多轮澄清闭环）
# ══════════════════════════════════════════════════════════════
#
# ★ 为什么"客户答复"要单独走一条路径，而不是让 agent 从 `user_input` 里自己读：
#
# 答复是一次**外部事件**（客户回了消息），与"客户发了照片"同类 ——
# `mark_evidence_received` 已经为前者建了写入口，这里为后者建同一个。
# 它必须落库：① 否则重跑时新 worker 看不到上一轮问过什么；② 否则审计里
# 看不出"结论是从哪句话推出来的"；③ 最要紧的是 **verifier 需要能区分
# "还在等客户"与"客户答了却还没办成"** —— 这两件事在 `ticket.status` 上
# 都是 `open`，只有 `customer_reply` 这一列能把它们分开。
#
# ★ 为什么答复解析必须用 `corpus` 的确定性分类器、不能让 agent 自由归纳：
# 本项目铁律是"verifier 不许读模型自己写的散文"（缺陷 ⑩⑲ 的成因）。
# 答复里含的是**结构化事实**（缺的金额、缺的原因、凭证到没到），
# 用 `corpus.classify_cause` / `corpus.extract_amount` / 凭证判定
# 这三个确定性函数抽取，与 seed 造数时用的是**同一套代码**——
# 否则第二轮的事实来源与第一轮不是一套，case 依然不可赢。
# （实现在本文件 `apply_customer_reply`，靠近它使用的其它图内函数。）


#: 凭证关键词 → `rules.EVIDENCE_TYPES` 受控词表。**顺序即优先级**。
#:
#: ★ 为什么顺序有意义：#60 的判据是「凭证要能证明**问题本身**」。
#:   一句话里同时出现「照片」和「运单号」（"照片发了，运单号 xxx"）时
#:   要取更强的那个，所以按序命中即停。
#:
#: ★ 为什么把「订单号 / 运单号」从原词表里踢出：它们不是凭证，是**业务钥匙**。
#:   PII 脱敏那一轮量过 —— 语料里 16–19 位的「疑似卡号」有 25/26 条
#:   Luhn 校验不通过，原文全是订单号 / 运单号。订单号是 agent 找订单的
#:   唯一入口，把它当凭证，等于用「有订单号」去证明「东西坏了」。
_EVIDENCE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("inspection_report", ("检测报告", "质检报告", "检验报告")),
    ("video", ("视频", "开箱录")),
    ("photo", ("照片", "图片", "拍了", "拍给你", "拍个", "实拍")),
    ("screenshot", ("截图",)),
    ("chat_log", ("聊天记录", "对话记录", "沟通记录")),
    (
        "tracking_proof",
        # ★ 为什么必须收**症状描述**，不能只认名词：
        #   探针 `_p2check2.py` 那条「物流停在转运中心一直没动」本来红着 ——
        #   词表只有「物流轨迹」，而真实客户描述丢件时说的是「物流停住了」
        #   「卡在中转」「一直没动」，很少有人会说「我给你一份快递轨迹」。
        #   只认名词就漏掉绝大多数表述 → 凭证被认成 `""` → agent 再追问一轮，
        #   正好抵消 #60 想解决的事（凭证路径要多走一轮）。
        #   ★ 位置仍保持最后一位：顺序即优先级，「照片发了，运单号 xxx」
        #     要按序取更强的 `photo`，不能被这条抢走。
        (
            "运单号",
            "快递单号",
            "物流轨迹",
            "轨迹",
            "转运中心",
            "中转站",
            "物流停",
            "物流没动",
            "签收异常",
            "快递单",
        ),
    ),
)


def detect_evidence_type(text: str) -> str:
    """这段话里的凭证属于受控词表里的哪一类？认不出就返回空串。

    ★ 它只负责「认出**是哪一类**」，不负责「够不够格」—— 后者是
      `rules.check_evidence` 的事。拆成两个函数是因为「认得出」与
      「够不够格」是两种不同的失败：前者是「没说清交的是什么」，
      要客户重发；后者是「交了但证明不了这件事」，要客户补对类型那一份。
      合成一个布尔会把两者压成同一种「没凭证」，改法就跟着错。
    """
    from ..db.corpus import normalize

    t = normalize(text)
    for ev_type, keys in _EVIDENCE_KEYWORDS:
        if any(k in t for k in keys):
            return ev_type
    return ""
