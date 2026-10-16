"""工具层：按角色物理裁剪的工具注册表。

★ 这是本项目最值得讲的设计。

大多数 agent 项目用 prompt 约束 agent"不要调某个工具"——这可以被提示注入绕过。
这里的做法是：**工具列表在运行时按角色物理裁剪**。

    registry.get_tools("triage")  → 只返回只读工具
    registry.get_tools("resolver")→ 只读 + 写工具
    registry.get_tools("verifier")→ 只读 + 断言工具

`resolver` 拿不到别的角色的写工具，因为那些函数的引用根本不在它拿到的列表里。
即使 LLM 在输出里"幻觉"出一个工具名，LangGraph 也会因找不到而报错，
不会静默执行任何越权动作。

配套的还有：
- **幂等**：写工具必须传 idempotency_key，DB 唯一约束兜底
- **重试**：只读工具挂 tenacity 重试；写工具**不自动重试**（避免重复副作用）
- **审计**：所有工具调用写 audit_log，评测时用于归因
"""

from __future__ import annotations

import contextvars
import datetime as dt
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Literal, get_args

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from tenacity import retry, stop_after_attempt, wait_exponential

from .. import clock
from ..config import settings
from ..db.models import (
    AFTER_SALES_ACTION_KINDS,
    MONEY_ACTION_KINDS,
    NON_REFUNDABLE_CATEGORIES,
    NON_RETURNABLE_CATEGORIES,
    AfterSalesAction,
    AuditLog,
    Refund,
    Ticket,
)
from ..db.session import TenantRepo, session_scope
from ..db.snapshot import export_snapshot
from ..db import pii
from ..rules import (
    RULE_PRECEDENCE,
    decide_for_order,
    expected_action_for,
    refund_is_forbidden,
)

Role = Literal["triage", "resolver", "verifier", "single"]

# ── 角色 → 工具白名单（唯一事实来源）─────────────────────────
# ★ 注意 ROLE_TOOL_MATRIX 是**物理权限裁剪**，不是 prompt 里的请求：
#   ToolRegistry 拿不到名字的工具根本不会出现在该角色的工具列表里，
#   模型没有机会"不听话"。（见 §4.1：护栏写在代码里，不写在提示词里。）
ROLE_TOOL_MATRIX: dict[str, tuple[str, ...]] = {
    "triage": (
        "get_ticket",
        "get_customer",
        "list_orders_of_customer",
        "get_order",
        "list_order_items",
        "list_refunds_of_order",
        # 与 list_refunds_of_order 对称：这是**数据列表**（已经做过什么），不是资格判定。
        # triage 拿它才能看出"这单已补发过"，但仍不判资格、不写数据。
        "list_after_sales_of_order",
        "query_policy",
        "days_since_signed",
    ),
    "resolver": (
        "get_ticket",
        "get_customer",
        "list_orders_of_customer",
        "get_order",
        "list_order_items",
        "list_refunds_of_order",
        "list_after_sales_of_order",
        "query_policy",
        "days_since_signed",
        "check_refund_eligibility",
        "check_after_sales_eligibility",
        # ↓ 写工具：只有 resolver 有
        "create_refund",
        "block_order",
        "close_ticket",
        "escalate_to_human",
        "submit_after_sales_action",
        "mark_evidence_received",
        # #2：resolver 是唯一能"问客户"的角色。triage 只读侦察、verifier 只读复核，
        # 都不该有发起澄清的权限 —— 否则"谁负责把案子往前推"就模糊了。
        "ask_customer",
    ),
    "verifier": (
        "get_ticket",
        "get_order",
        "list_refunds_of_order",
        # verifier 要能独立看到"非退款动作到底做了没有"。
        # 虽然 get_snapshot 里也含 after_sales，但快照是**聚合视图**：
        # 复核角色应该能直接查原始记录，而不是只能读别人整理好的视图。
        "list_after_sales_of_order",
        "get_snapshot",
    ),
    # single arm：单个 agent 拿全部工具（对照组）
    "single": (
        "get_ticket",
        "get_customer",
        "list_orders_of_customer",
        "get_order",
        "list_order_items",
        "list_refunds_of_order",
        "list_after_sales_of_order",
        "query_policy",
        "days_since_signed",
        "check_refund_eligibility",
        "check_after_sales_eligibility",
        "create_refund",
        "block_order",
        "close_ticket",
        "escalate_to_human",
        "submit_after_sales_action",
        "mark_evidence_received",
        "ask_customer",
        "get_snapshot",
    ),
}

# ★ WRITE_TOOLS 必须与 _WRITE_TOOLS_FN 保持同步。
#   它被用于两件事：① 只读工具的按角色去重缓存（写工具绝不能进缓存，
#   否则第二次调用会"回放"第一次的结果而不是真的执行）；② 报表里的
#   写调用计数（漏掉一个会让"多智能体写操作没变多"这个结论失真）。
WRITE_TOOLS = frozenset(
    {
        "create_refund",
        "block_order",
        "close_ticket",
        "escalate_to_human",
        "submit_after_sales_action",
        "mark_evidence_received",
        "ask_customer",
    }
)

# ── 工具调用上下文 ─────────────────────────────────────────────
# ★ 用 ContextVar 而不是模块级 dict：
#   评测要并发跑几十条 case，模块级 dict 会让并发 run 互相覆盖 tenant_id/ticket_id，
#   出现"给 A 租户的工单办了 B 租户的单"这种最严重的串号 bug。
#   ContextVar 每个线程/asyncio task 一份，天然隔离；每个 worker 节点开头都会重新 set。
_CTX: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "ticket_agent_tool_ctx", default={}
)


def set_tool_context(
    tenant_id: str, ticket_id: int, actor: str, run_id: str = "", dry_run: bool = False
) -> None:
    _CTX.set(
        {
            "tenant_id": tenant_id,
            "ticket_id": ticket_id,
            "actor": actor,
            "run_id": run_id,
            "dry_run": dry_run,
            "call_seq": 0,
        }
    )


def get_tool_context() -> dict[str, Any]:
    return dict(_CTX.get())


def _ctx_or_raise() -> dict[str, Any]:
    ctx = _CTX.get()
    if not ctx.get("tenant_id"):
        raise RuntimeError("工具上下文未初始化 —— 每个 run 必须先 set_tool_context()")
    return ctx


def _audit(
    actor: str,
    action: str,
    payload: dict[str, Any],
    ok: bool = True,
    ticket_id: int | None = None,
    tenant_id: str | None = None,
) -> None:
    """写一条审计日志。

    ★ C 层脱敏：payload 里的**字符串值**在落库前统一脱敏。
    改这一个函数就覆盖了全部 27 处调用点 —— 这正是"单一出口"的好处，
    若在每个调用点各脱敏一次，一定会有漏的（而漏的那个恰恰是最该脱敏的）。

    注意：订单号**不会**被抹（pii.redact 的白名单），否则审计就失去了
    "能追到是哪一单"的作用 —— 脱敏不是把日志变成废纸。
    """
    ctx = _CTX.get()
    if settings.pii_redact_audit:
        payload = _redact_payload(payload, profile=settings.pii_profile)
    with session_scope() as s:
        s.add(
            AuditLog(
                ticket_id=ticket_id if ticket_id is not None else ctx.get("ticket_id"),
                tenant_id=tenant_id or ctx.get("tenant_id", ""),
                actor=actor,
                action=action,
                payload=payload,
                ok=ok,
            )
        )


def _redact_payload(
    payload: dict[str, Any], *, profile: str = "standard"
) -> dict[str, Any]:
    """递归脱敏 payload 里的字符串值。

    只处理 str；数字/布尔/None 原样返回（订单号 id 常以 int 出现，本就不该动）。
    容器递归一层即可覆盖审计的实际形态（dict 里套 list/dict）。
    """
    out: dict[str, Any] = {}
    for k, v in payload.items():
        out[k] = _redact_value(v, profile=profile)
    return out


def _redact_value(v: Any, *, profile: str = "standard") -> Any:
    if isinstance(v, str):
        safe, _ = pii.redact(v, profile=profile)
        return safe
    if isinstance(v, dict):
        return {kk: _redact_value(vv, profile=profile) for kk, vv in v.items()}
    if isinstance(v, (list, tuple)):
        return [_redact_value(x, profile=profile) for x in v]
    return v


# ══════════════════════════════════════════════════════════════
# 只读工具
# ══════════════════════════════════════════════════════════════


def get_ticket() -> dict[str, Any]:
    """获取当前工单的完整信息（主题、内容、状态、关联订单、澄清进度）。

    ★ 返回 `request_kind`（客户要什么）是**刻意的**，不是泄漏答案：
    它是任务输入。答案是我们做什么（动作 + 终态），那由政策判定得出，
    agent 必须自己去查订单、跑资格工具才能得到。
    不给它，golden 就会按一个 agent 读不到的信息算分 —— 那种不对称
    在本项目里已经造成过三次"这条 case 永远不可能赢"（见 `seed._decide_golden`）。
    """
    ctx = _ctx_or_raise()
    with session_scope() as s:
        repo = TenantRepo(s, ctx["tenant_id"])
        t = repo.get_ticket(ctx["ticket_id"])
        if not t:
            return {"error": "ticket_not_found"}
        _audit(ctx["actor"], "get_ticket", {"ticket_id": t.id})
        # ★ B 层脱敏：工单正文进 LLM 的 prompt 前先脱敏。
        #   注意订单号**不会被抹**（见 pii.redact 的白名单）—— 那是 agent 找订单的钥匙。
        safe_body, _ = (
            pii.redact(t.body or "", profile=settings.pii_profile)
            if settings.pii_redact_prompt
            else (t.body or "", ())
        )
        return {
            "ticket_id": t.id,
            "subject": t.subject,
            "body": safe_body,
            "status": t.status,
            "intent": t.intent,
            # 客户诉求：9 种之一，取值见政策 R8–R15 的分类
            "request_kind": t.request_kind or "refund",
            "request_cause": t.request_cause or "",
            # ★ 客户自报的赔付/价保金额。与 `request_cause` / `evidence_provided`
            #   同一条理由，但这条**曾经漏掉过**：
            #     - golden 侧：`seed.py:1197` 的 `_decide_golden` 回落
            #       `tickets.request_amount`；
            #     - agent 侧：`apply_customer_reply`（`graph/nodes.py:372-376`）
            #       在澄清第二轮把客户答复里的金额写回**同一列**；
            #     - 判定入口：`check_after_sales_eligibility` 的 `amount` 是
            #       **工具入参**，基线只能从 `get_ticket` 的返回体里拿。
            #   三条都对齐到这一列，唯独返回体漏了它 ⇒ 基线只能去猜自由文本
            #   （第一版用 `_amount_in_text(body)`）⇒ 第二轮永远读到 0.0
            #   ⇒ `rules.py:715 comp <= 0` ⇒ 又一次 need_clarification
            #   ⇒ 终态卡 `open`，而报表只显示「agent 没办成」。
            #   实测见 `_r10diff.py`（千级库逐项同参重建，4/4 条命中）。
            #   ★ 与缺陷 ㉓（参数传不进 ≡ 规则没写）、㉚（`current_price`
            #     不从 order 回落）同族：**数据落到了该到的地方，
            #     判定入口没去读**。这三例 + 本处共四例都在同一个位置复发，
            #     所以另加了「任务输入字段必须从 get_ticket 可达」的守卫测试。
            "request_amount": float(t.request_amount or 0.0),
            # ★ 客户是否已提供凭证（政策 R2/R9/R10 的守卫）。
            #   与 `request_kind` / `request_amount` 同一条理由：它是任务输入，
            #   golden 按它算分，agent 必须读得到。不给，就等于
            #   "标准答案用了一个 agent 拿不到的信息"——那种 case 不可赢。
            "evidence_provided": bool(t.evidence_provided),
            # ★ #2：把"我们上一轮问了客户什么 / 客户答了什么"也暴露出来。
            #   第二轮重跑时新 worker 是靠这个知道"这单卡在等什么"，否则
            #   它会当成一张全新工单重新走一遍，把已经问过的问题再问一次。
            #   与 `evidence_provided` 同一条理由：这是**任务输入**（对话状态），
            #   不是答案（我们做什么）。
            "pending_question": t.pending_question or "",
            "customer_reply": t.customer_reply or "",
            "customer_id": t.customer_id,
            "order_id": t.order_id,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }


def get_customer(customer_id: int) -> dict[str, Any]:
    """获取客户档案，含风控评分与是否黑名单（判断 R4 必查）。"""
    ctx = _ctx_or_raise()
    with session_scope() as s:
        repo = TenantRepo(s, ctx["tenant_id"])
        c = repo.get_customer(customer_id)
        if not c:
            return {"error": "customer_not_found"}
        _audit(ctx["actor"], "get_customer", {"customer_id": customer_id})
        return {
            "customer_id": c.id,
            "name": c.name,
            "risk_score": c.risk_score,
            "complaint_count": c.complaint_count,
            "is_blacklisted": c.is_blacklisted,
        }


def list_orders_of_customer(customer_id: int) -> dict[str, Any]:
    """列出该客户的所有订单。工单未关联订单时（L3），用这个看清有哪些候选。"""
    ctx = _ctx_or_raise()
    with session_scope() as s:
        repo = TenantRepo(s, ctx["tenant_id"])
        orders = repo.list_orders_of_customer(customer_id)
        _audit(ctx["actor"], "list_orders_of_customer", {"customer_id": customer_id, "n": len(orders)})
        return {
            "count": len(orders),
            "orders": [
                {
                    "order_id": o.id,
                    "order_no": o.order_no,
                    "category": o.category,
                    "amount": o.amount,
                    "status": o.status,
                    "paid_at": o.paid_at.isoformat() if o.paid_at else None,
                    "signed_at": o.signed_at.isoformat() if o.signed_at else None,
                    "refund_count": o.refund_count,
                }
                for o in orders
            ],
        }


def get_order(order_id: int) -> dict[str, Any]:
    """获取订单详情，含状态、金额、类目、签收时间、已退款次数。"""
    ctx = _ctx_or_raise()
    with session_scope() as s:
        repo = TenantRepo(s, ctx["tenant_id"])
        o = repo.get_order(order_id)
        if not o:
            return {"error": "order_not_found"}
        _audit(ctx["actor"], "get_order", {"order_id": order_id})
        return {
            "order_id": o.id,
            "order_no": o.order_no,
            "category": o.category,
            "amount": o.amount,
            "status": o.status,
            "paid_at": o.paid_at.isoformat() if o.paid_at else None,
            "shipped_at": o.shipped_at.isoformat() if o.shipped_at else None,
            "signed_at": o.signed_at.isoformat() if o.signed_at else None,
            "refund_count": o.refund_count,
        }


def list_order_items(order_id: int) -> dict[str, Any]:
    """列出订单内商品明细及其类目（判断 R2/R6 需要看单品类目）。"""
    ctx = _ctx_or_raise()
    with session_scope() as s:
        repo = TenantRepo(s, ctx["tenant_id"])
        items = repo.list_order_items(order_id)
        _audit(ctx["actor"], "list_order_items", {"order_id": order_id, "n": len(items)})
        return {
            "count": len(items),
            "items": [
                {
                    "sku": i.sku,
                    "name": i.name,
                    "category": i.category,
                    "qty": i.qty,
                    "unit_price": i.unit_price,
                }
                for i in items
            ],
        }


def list_refunds_of_order(order_id: int) -> dict[str, Any]:
    """列出该订单已有的退款记录（判断 R5 退款次数上限必查）。"""
    ctx = _ctx_or_raise()
    with session_scope() as s:
        repo = TenantRepo(s, ctx["tenant_id"])
        rs = repo.list_refunds_of_order(order_id)
        _audit(ctx["actor"], "list_refunds_of_order", {"order_id": order_id, "n": len(rs)})
        return {
            "count": len(rs),
            "refunds": [
                {
                    "refund_id": r.id,
                    "amount": r.amount,
                    "status": r.status,
                    "requires_human": r.requires_human,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rs
            ],
        }


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.2, max=1), reraise=True)
def days_since_signed(order_id: int) -> dict[str, Any]:
    """计算订单签收至今的天数（判断 R1 七天无理由必用）。

    注意：这是**工具算而不是 LLM 算** —— 日期算术 LLM 容易错，
    而且错了没有任何提示。放在工具里还可以加单测。
    """
    ctx = _ctx_or_raise()
    with session_scope() as s:
        repo = TenantRepo(s, ctx["tenant_id"])
        o = repo.get_order(order_id)
        if not o:
            return {"error": "order_not_found"}
        if o.signed_at is None:
            return {
                "order_id": order_id,
                "signed": False,
                "days_since_signed": None,
                "note": "该订单尚未签收，R1 的七天期限不适用",
            }
        signed = o.signed_at
        if signed.tzinfo is None:
            signed = signed.replace(tzinfo=dt.timezone.utc)
        now = clock.now()
        days = (now - signed).days
        _audit(ctx["actor"], "days_since_signed", {"order_id": order_id, "days": days})
        return {
            "order_id": order_id,
            "signed": True,
            "days_since_signed": days,
            "within_window": days <= settings.return_window_days,
            "window_days": settings.return_window_days,
        }


def query_policy(question: str, top_k: int = 3) -> dict[str, Any]:
    """检索售后政策文档（RAG）。决策前必须查政策，不要凭记忆答。

    Args:
        question: 用自然语言描述你要判断的情形，例如"内衣类目签收 3 天能否无理由退货"
        top_k: 返回条款数量
    """
    ctx = _ctx_or_raise()
    from .knowledge import search_policies

    hits = search_policies(question, top_k=top_k)
    _audit(ctx["actor"], "query_policy", {"question": question, "hits": [h["rule_id"] for h in hits]})
    return {"query": question, "hits": hits}


# ══════════════════════════════════════════════════════════════
# 判定工具（不写库，但把规则集中实现，避免 LLM 各算各的）
# ══════════════════════════════════════════════════════════════


class EligibilityResult(BaseModel):
    eligible: bool = Field(description="是否可以按无理由退货处理")
    action: str = Field(description="建议动作：refund | reject | block_logistics | escalate | need_clarification")
    reasons: list[str] = Field(default_factory=list, description="命中的规则编号与说明")
    amount: float = Field(default=0.0, description="涉及金额")


def check_refund_eligibility(order_id: int) -> dict[str, Any]:
    """按 R1–R6 逐条检查订单是否可退款，返回结论与命中的规则。

    ★ 这是「把业务规则放进代码而不是 prompt」的体现：
    规则判断可单测、可解释、可审计；LLM 只负责决定**要不要调用它**，
    以及**如何向客户解释结论**。

    判定逻辑本身在 `ticket_agent.rules.decide()` —— 那是规则的唯一事实来源，
    评测集的 golden 也调同一个函数，避免两处实现漂移。
    """
    ctx = _ctx_or_raise()
    with session_scope() as s:
        repo = TenantRepo(s, ctx["tenant_id"])
        o = repo.get_order(order_id)
        if not o:
            return {"error": "order_not_found"}
        items = repo.list_order_items(order_id)
        cust = repo.get_customer(o.customer_id)
        refunds = repo.list_refunds_of_order(order_id)

        d = decide_for_order(o, items=items, customer=cust, refund_count=len(refunds))

        _audit(
            ctx["actor"],
            "check_refund_eligibility",
            {"order_id": order_id, "eligible": d.eligible, "action": d.action, "reasons": d.reasons},
        )
        return EligibilityResult(
            eligible=d.eligible, action=d.action, reasons=d.reasons, amount=d.amount
        ).model_dump()


def get_snapshot() -> dict[str, Any]:
    """获取当前工单相关业务状态的快照（verifier 用它做终态断言）。"""
    ctx = _ctx_or_raise()
    with session_scope() as s:
        snap = export_snapshot(s, ctx["tenant_id"], ctx["ticket_id"])
        return snap


# ══════════════════════════════════════════════════════════════
# 写工具（幂等 + 审计；★ 不自动重试）
# ══════════════════════════════════════════════════════════════


class CreateRefundInput(BaseModel):
    order_id: int = Field(description="订单 ID")
    amount: float = Field(description="退款金额（元）")
    reason: str = Field(description="退款原因，用中文简要说明")
    idempotency_key: str = Field(
        default="",
        description="幂等键。留空则由 order_id+amount 自动生成；重试请传同一个值以避免重复退款",
    )
    requires_human: bool = Field(default=False, description="是否需要人工审批（R4 命中时置 true）")
    allow_policy_override: bool = Field(
        default=False,
        description=(
            "★ 仅在人工显式要求越过政策时置 true（会记入审计）。"
            "agent 一律保持 false —— 政策禁止的退款不该由 agent 决定放行"
        ),
    )


def create_refund(
    order_id: int,
    amount: float,
    reason: str,
    idempotency_key: str = "",
    requires_human: bool = False,
    allow_policy_override: bool = False,
) -> dict[str, Any]:
    """创建退款单。★ 调用前必须先用 check_refund_eligibility 确认可退。

    幂等机制：同一 idempotency_key 重复调用只会成功一次，第二次返回 duplicate 提示。

    ★★ 为什么这里**也要**校验政策（而不是只靠调用方自觉）：

    这是修一个真实缺陷时加的护栏（README §4.2 缺陷 ⑥）。原设计把资格校验
    完全交给调用方，工具本身只做幂等 —— 结果人工审批接口拿到了一个
    「R5：该订单已有 1 条退款记录，不得再次退款」的工单，仍然直接调本函数，
    把 47.53 元的订单退成了 95.06 元。**幂等键防不住这个**：它防的是
    "同一个请求被提交两次"，而这里是"同一订单被合法地退了两次"，
    两次的幂等键不同（票号不同、金额也不同），唯一约束自然放行。

    所以写路径上必须有一道**不依赖调用方**的护栏。分工是：

    - **禁止（prohibition，R5/R6 等）**：政策根本不允许这笔钱出去，
      本函数默认拒绝，人工也不行 —— 除非显式传 `allow_policy_override=True`
      （此时会写一条 `override=True` 的审计日志，评测可统计）。
    - **推迟（deferral，R4）**：政策允许退，只是要人工审。
      本函数放行 —— 人工审批的正常路径不能被自己挡住。

    这道判据与工具、golden 共用 `rules.refund_is_forbidden()`，
    所以不会出现"三处各判一次然后漂移"。
    """
    ctx = _ctx_or_raise()
    key = idempotency_key or hashlib.md5(
        f"{ctx['tenant_id']}:{ctx['ticket_id']}:{order_id}:{amount}".encode()
    ).hexdigest()

    if ctx.get("dry_run"):
        _audit(ctx["actor"], "create_refund(dry_run)", {"order_id": order_id, "amount": amount})
        return {"dry_run": True, "would_create": {"order_id": order_id, "amount": amount, "requires_human": requires_human}}

    try:
        with session_scope() as s:
            repo = TenantRepo(s, ctx["tenant_id"])

            # ── 幂等回查（★ 必须在政策复核**之前**，见缺陷 ㉑）────────
            # 为什么顺序不能反：
            #   政策读的字段（refund_count）正是本函数写入时会改的字段。
            #   若先跑政策复核，那么"同一个请求重试"看到的是上一次写入后的世界，
            #   R5 会判它 policy_forbidden —— 把一个无害的重试说成违规，
            #   而正确的语义是 duplicate（"你已经做过了"）。
            #   两者对 agent 的含义完全不同：前者会让它以为判断错了、该换动作。
            existing = repo.find_refund_by_key(key)
            if existing is not None:
                _audit(
                    ctx["actor"],
                    "create_refund.duplicate",
                    {"order_id": order_id, "key": key, "existing_id": existing.id},
                    ok=False,
                )
                return {
                    "duplicate": True,
                    "refund_id": existing.id,
                    "order_id": existing.order_id,
                    "amount": existing.amount,
                    "status": existing.status,
                    "message": "该退款已存在（同一幂等键），未重复创建",
                    "idempotency_key": key,
                }

            o = repo.get_order(order_id)
            if not o:
                return {"error": "order_not_found"}

            # ── 政策护栏（不依赖调用方）──────────────────────────
            items = repo.list_order_items(order_id)
            cust = repo.get_customer(o.customer_id)
            refunds = repo.list_refunds_of_order(order_id)
            d = decide_for_order(o, items=items, customer=cust, refund_count=len(refunds))

            hit_rule = d.rule_hits[0] if d.rule_hits else d.action
            if refund_is_forbidden(d) and not allow_policy_override:
                _audit(
                    ctx["actor"],
                    "create_refund.blocked",
                    {
                        "order_id": order_id,
                        "amount": amount,
                        "action": d.action,
                        "rule_hits": d.rule_hits,
                        "reason": "policy_forbidden",
                    },
                    ok=False,
                )
                return {
                    "error": "policy_forbidden",
                    "action": d.action,
                    "rule_hits": d.rule_hits,
                    "reasons": d.reasons,
                    "message": (
                        f"政策禁止该订单退款（{hit_rule}）：{'；'.join(d.reasons)}。"
                        "不得创建退款单，应转人工向客户说明政策。"
                        "如确需例外，须由人工显式声明 override。"
                    ),
                }

            # 退款总额不得超过订单金额（prompt 里写了这条，但 prompt 不是护栏）
            already = sum(float(r.amount or 0.0) for r in refunds if r.status == "approved")
            if already + float(amount) - float(o.amount or 0.0) > settings.eval_amount_tolerance_cny:
                if not allow_policy_override:
                    _audit(
                        ctx["actor"],
                        "create_refund.blocked",
                        {
                            "order_id": order_id,
                            "amount": amount,
                            "already_approved": round(already, 2),
                            "order_amount": float(o.amount or 0.0),
                            "reason": "amount_exceeds_order",
                        },
                        ok=False,
                    )
                    return {
                        "error": "amount_exceeds_order",
                        "order_amount": round(float(o.amount or 0.0), 2),
                        "already_approved": round(already, 2),
                        "requested": round(float(amount), 2),
                        "message": (
                            f"退款金额超过订单金额：订单 {o.amount} 元，"
                            f"已批准退款 {round(already, 2)} 元，本次请求 {amount} 元。"
                            "不得超额退款。"
                        ),
                    }

            r = Refund(
                order_id=order_id,
                ticket_id=ctx["ticket_id"],
                amount=amount,
                reason=reason,
                status="requested" if requires_human else "approved",
                requires_human=requires_human,
                idempotency_key=key,
            )
            s.add(r)
            o.refund_count = (o.refund_count or 0) + 1
            s.flush()
            refund_id = r.id

        _audit(
            ctx["actor"],
            "create_refund",
            {"order_id": order_id, "amount": amount, "refund_id": refund_id, "key": key},
        )
        return {
            "ok": True,
            "refund_id": refund_id,
            "order_id": order_id,
            "amount": amount,
            "status": "requested" if requires_human else "approved",
            "requires_human": requires_human,
            "idempotency_key": key,
        }

    except IntegrityError:
        # ★ 幂等兜底：唯一约束拦住了重复写入
        _audit(
            ctx["actor"],
            "create_refund.duplicate",
            {"order_id": order_id, "key": key},
            ok=False,
        )
        return {
            "duplicate": True,
            "message": "该退款已存在（同一幂等键），未重复创建",
            "idempotency_key": key,
        }


class BlockOrderInput(BaseModel):
    order_id: int = Field(description="要拦截的订单 ID")
    note: str = Field(default="", description="备注")


def block_order(order_id: int, note: str = "") -> dict[str, Any]:
    """发起物流拦截并将订单置为已取消（R3 的正确动作，不是退款）。"""
    ctx = _ctx_or_raise()
    with session_scope() as s:
        repo = TenantRepo(s, ctx["tenant_id"])
        o = repo.get_order(order_id)
        if not o:
            return {"error": "order_not_found"}
        if o.status != "shipped":
            return {
                "error": "invalid_state",
                "message": f"订单状态为 {o.status}，只有 shipped 状态才需要拦截",
            }
        o.status = "cancelled"
        _audit(ctx["actor"], "block_order", {"order_id": order_id, "note": note})
    return {"ok": True, "order_id": order_id, "new_status": "cancelled", "note": note}


class CloseTicketInput(BaseModel):
    resolution: str = Field(description="处置结论，面向客户的中文说明")
    status: Literal["resolved", "rejected"] = Field(
        default="resolved", description="resolved=已解决；rejected=诉求不成立"
    )


def close_ticket(resolution: str, status: str = "resolved") -> dict[str, Any]:
    """关闭工单。★ 只有真正完成处置（或明确驳回）才能调用。"""
    ctx = _ctx_or_raise()
    if status not in ("resolved", "rejected"):
        return {"error": "invalid_status", "message": "status 只能是 resolved 或 rejected"}
    with session_scope() as s:
        repo = TenantRepo(s, ctx["tenant_id"])
        t = repo.get_ticket(ctx["ticket_id"])
        if not t:
            return {"error": "ticket_not_found"}
        t.status = status
        t.resolution = resolution
        t.closed_at = clock.now()
        _audit(ctx["actor"], "close_ticket", {"status": status, "resolution": resolution[:200]})
    return {"ok": True, "ticket_id": ctx["ticket_id"], "status": status}


class EscalateInput(BaseModel):
    reason: str = Field(description="转人工的原因，需引用命中的规则编号")
    suggested_action: str = Field(default="", description="建议人工执行的动作")


def escalate_to_human(reason: str, suggested_action: str = "") -> dict[str, Any]:
    """转人工处理（R4/R5/R6 命中时的正确动作）。工单状态置为 pending_human。"""
    ctx = _ctx_or_raise()
    with session_scope() as s:
        repo = TenantRepo(s, ctx["tenant_id"])
        t = repo.get_ticket(ctx["ticket_id"])
        if not t:
            return {"error": "ticket_not_found"}
        t.status = "pending_human"
        t.resolution = f"[待人工] {reason}"
        _audit(ctx["actor"], "escalate_to_human", {"reason": reason, "suggested": suggested_action})
    return {"ok": True, "ticket_id": ctx["ticket_id"], "status": "pending_human", "reason": reason}


class MarkEvidenceInput(BaseModel):
    evidence_type: str = Field(
        description="凭证类型，如 photo / screenshot / inspection_report"
    )
    note: str = Field(default="", description="备注，例如凭证说明了什么")


def mark_evidence_received(evidence_type: str, note: str = "") -> dict[str, Any]:
    """标记客户已提供凭证（照片 / 截图 / 检测报告）。

    ★ 这个工具存在的理由，是一次"政策承诺了但系统从未检查"的修复：

    政策原文（`prompt/policies.md` 的 R2 / R9 / R10）写着
    "签收后如有质量问题，需走质量投诉流程并提供凭证"，
    但在 `Ticket.evidence_provided` 这一列出现之前，**全项目 0 处读它**。
    客户说"衣服破了我要换货"而照片还没给，系统照样判"受理换货"——
    政策写在文档里，从来没有抵达任何代码路径。

    ★ 为什么它必须是**一个写工具**而不是让 agent 自己传参：
    "客户给没给凭证"是一次**外部事件**（客户发了照片），不是 agent 的判断。
    给它一个独立的、可审计的写入口，`evidence_provided` 的每一次翻转
    都会留在审计日志里；否则它只会是一个谁都能悄悄置位的布尔字段。

    ★ 幂等：重复标记返回 `already=True`，不报错 —— 客户可能分两次发两张照片。

    ★ #60：光「有凭证」不够，还要「这份凭证能证明这一次的事」。
      原来 `evidence_type` 是任意自由字符串，而它翻的却是一个只表达
      「有 / 没有」的布尔列 —— 传 `none` / `待补` 同样能把 `evidence_provided`
      置成 True，系统对凭证的**内容**零检查。政策承诺了「需提供凭证」，
      而代码检查的只是「客户有没有说过凭证这两个字」。
      现在统一走 `rules.check_evidence`（受控词表 + 按索赔原因匹配），
      `apply_customer_reply` 那条答复路径也走同一条判据 —— 两个入口一条判据。
    """
    from ..rules import check_evidence

    ctx = _ctx_or_raise()
    with session_scope() as s:
        repo = TenantRepo(s, ctx["tenant_id"])
        t = repo.get_ticket(ctx["ticket_id"])
        if not t:
            return {"error": "ticket_not_found"}
        if t.evidence_provided:
            _audit(
                ctx["actor"],
                "mark_evidence_received.duplicate",
                {"evidence_type": evidence_type},
                ok=False,
            )
            return {
                "already": True,
                "ticket_id": t.id,
                "evidence_provided": True,
                "message": "该工单已标记收到凭证，未重复记录",
            }
        # ★ 拒收：不合法就不许翻布尔列，并把这个决定写进审计 ——
        #   否则「凭证不合格」这件事只活在 agent 的一句话里，不留痕。
        ev_ok, why = check_evidence(evidence_type, t.request_cause or "")
        if not ev_ok:
            _audit(
                ctx["actor"],
                "mark_evidence_received.rejected",
                {
                    "evidence_type": evidence_type,
                    "request_cause": t.request_cause or "",
                    "reason": why,
                },
                ok=False,
            )
            return {
                "ok": False,
                "error": "evidence_rejected",
                "reason": why,
                "ticket_id": t.id,
                "evidence_provided": False,
            }
        t.evidence_provided = True
        _audit(
            ctx["actor"],
            "mark_evidence_received",
            {"evidence_type": evidence_type, "note": note[:200]},
        )
    return {
        "ok": True,
        "ticket_id": ctx["ticket_id"],
        "evidence_provided": True,
        "evidence_type": evidence_type,
    }


class AskCustomerInput(BaseModel):
    question: str = Field(description="要向客户追问的内容，一句话说清缺什么信息")


def ask_customer(question: str) -> dict[str, Any]:
    """把"我们正在等客户回答什么"写进工单（#2 多轮澄清闭环的写入口）。

    ★ 为什么它必须是**一个写工具**，而不是让 agent 在最终回复里自由提问：

    政策里有六处会返回 `need_clarification`（R8/R9/R10/R11 缺凭证、缺原因、
    缺金额、缺现价）。在此之前，系统**没有任何字段能表达"我已经问了客户"** ——
    agent 只能在回复文本里问一句，而那句话：

      · 不进数据库，于是重跑时新 worker 看不到上一轮问过什么；
      · 不进审计日志，于是"这单卡在等客户"这件事无从复盘；
      · 最要命的是 **verifier 读不到它**：`graph/nodes.py` 的机器断言原先只认
        `basis == "ambiguous"` 一种澄清依据，于是"订单已关联 + 规则判
        need_clarification"的 case 全被记成"工单仍为 open 但没有任何需澄清的
        依据，可能存在漏处置"。

    这不是 agent 做错了，是 **系统说不出它在等什么**（与缺陷 ⑧ 同形：
    能力只存在于声明里，只不过这次声明在政策文档里、缺的在数据库里）。

    ★ 为什么 `status` 保持 `open` 而不是新增 `awaiting_customer`：
    "问了、客户还没答"时工单 **确实就是 open**。`status` 是唯一参与终态判定的
    字段（`snapshot.TICKET_FIELDS`），加一个值要同时改 golden、diff、verifier、
    `_finalize` 四处；而"在等什么"是 **事实记录**不是业务后果，放进
    `pending_question` 这一列就够了。

    ★ 幂等：同一问题重复问返回 `already=True`。与 `mark_evidence_received`
    同一条理由 —— 外部事件（客户答复）可能分多轮到达，重复提问不是错误。
    """
    ctx = _ctx_or_raise()
    q = (question or "").strip()
    if not q:
        return {"error": "empty_question", "message": "question 不能为空"}
    with session_scope() as s:
        repo = TenantRepo(s, ctx["tenant_id"])
        t = repo.get_ticket(ctx["ticket_id"])
        if not t:
            return {"error": "ticket_not_found"}
        if t.pending_question:
            _audit(
                ctx["actor"],
                "ask_customer.duplicate",
                {"question": q[:200]},
                ok=False,
            )
            return {
                "already": True,
                "ticket_id": t.id,
                "pending_question": t.pending_question,
                "message": "该工单已有待答问题，未重复记录",
            }
        t.pending_question = q[:500]
        _audit(ctx["actor"], "ask_customer", {"question": q[:200]})
    return {
        "ok": True,
        "ticket_id": ctx["ticket_id"],
        "pending_question": q[:500],
        "message": "已记录待客户答复的问题，工单保持 open",
    }


class SubmitAfterSalesInput(BaseModel):
    order_id: int = Field(description="订单 ID")
    kind: Literal[
        "exchange",
        "reship",
        "compensate",
        "price_protect",
        "invoice",
        "address_change",
        "urge",
    ] = Field(description="售后动作种类：换货/补发/赔付/价保/开票/改址/催单")
    cause: str = Field(
        default="",
        description=(
            "原因码，用于规则判定。补发仅接受 missing/wrong_item/damaged/lost/quality；"
            "换货在不可无理由类目下仅接受 quality/wrong_item/damaged"
        ),
    )
    amount: float = Field(default=0.0, description="涉及金额（仅 compensate / price_protect 需要）")
    detail: str = Field(default="", description="补充说明（新地址、发票抬头、赔付凭证等）")
    idempotency_key: str = Field(default="", description="幂等键，留空则自动生成")


def _after_sales_key(
    tenant_id: str, ticket_id: int, order_id: int, kind: str, amount: float, cause: str
) -> str:
    """非退款售后动作的幂等键。

    ★ 缺陷 ㉝：`cause` 必须进键。原键是
    `tenant:ticket:order:kind:amount`，于是同一张工单对同一订单提两笔
    "同 kind 同金额、但原因不同"的售后（例如两件都赔 5 元，一件是破损、
    一件是少件）会算出**同一个键** —— 第二笔被幂等挡下返回 duplicate，
    而业务上它是两笔独立赔付。这不是"重复提交"，是"键的分辨率不够"。

    与 `create_refund` 的键保持同一形状（那里已含 reason）。
    """
    return hashlib.md5(
        f"{tenant_id}:{ticket_id}:{order_id}:{kind}:{amount}:{cause}".encode()
    ).hexdigest()


def submit_after_sales_action(
    order_id: int,
    kind: str,
    cause: str = "",
    amount: float = 0.0,
    detail: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """提交非退款类售后动作（换货 / 补发 / 赔付 / 价保 / 开票 / 改址 / 催单）。

    ★ 调用前应先用 check_after_sales_eligibility 确认该诉求可受理。

    ## 为什么这个工具是**一个**而不是七个

    七个独立工具会让 LLM 面临"该调哪个"的选择，而那个选择恰好就是政策判断本身
    —— 把政策判断交给 LLM 选工具，等于把规则从代码里挪回 prompt 里。
    一个工具 + `kind` 参数，让"选哪个动作"变成一次**带参数的调用**，
    而参数是否合法由下面的政策复核回答。

    ## 三道护栏（与 create_refund 同一套分工）

    1. **政策复核**：用 `decide_for_order(request_kind=kind)` 重算一遍，
       要求 `d.action` 必须等于该诉求的期望动作。不相等说明 agent 声称的动作
       与政策结论不符 —— 返回 `policy_forbidden` 并带上政策给出的正确动作。
       ★ 这是把"工具只执行经过验证的动作"落到代码，而不是靠 prompt 请求。
    2. **账本不变量**：会出钱的动作（compensate / price_protect）必须与
       **已有退款**合并计算，累计付出不得超过订单金额。
       ★★ 这一条是缺陷 ⑥ 的教训的推广：原来的不变量只认 `refunds` 表，
       新加两种会出钱的动作如果不进同一个池子，护栏就会只守住它认识的那种钱。
    3. **幂等**：唯一约束兜底，重复提交返回 duplicate 而不是再写一笔。

    ## 与 create_refund 的关键差异：这里**没有** allow_policy_override

    `create_refund` 有一个 `allow_policy_override` 参数，因为人工审批确实可能
    破例退款。本工具**不提供**这个口子 —— 非退款线的破例必须走
    `approve_run(..., override=True, amount=...)` 显式指定金额，
    不能靠 agent 传一个布尔值就获得"绕过政策"的能力。
    理由见 `rules._decide_service` 的 docstring 第 3 条。
    """
    ctx = _ctx_or_raise()
    if kind not in AFTER_SALES_ACTION_KINDS:
        return {
            "error": "invalid_kind",
            "message": f"未知售后动作 {kind!r}，可选：{list(AFTER_SALES_ACTION_KINDS)}",
        }

    # ★ 缺陷 ㉝ 的另一半：键里**必须含 cause**，见 `_after_sales_key` 的 docstring。
    #   原键是 `tenant:ticket:order:kind:amount`，两笔同 kind 同金额但原因不同的
    #   售后会算出同一个键，第二笔被幂等挡下，而业务上它是两笔独立赔付。
    key = idempotency_key or _after_sales_key(
        ctx["tenant_id"], ctx["ticket_id"], order_id, kind, amount, cause
    )

    if ctx.get("dry_run"):
        _audit(ctx["actor"], f"submit_after_sales_action({kind})(dry_run)", {"order_id": order_id})
        return {"dry_run": True, "would_create": {"order_id": order_id, "kind": kind, "amount": amount}}

    try:
        with session_scope() as s:
            repo = TenantRepo(s, ctx["tenant_id"])

            # ── 幂等回查（★ 必须在政策复核**之前**，见缺陷 ㉑）────────
            # 与 create_refund 同一处修正、同一个理由：
            #   政策读的 reship_count / exchange_count / last_urge_at / invoice_issued
            #   正是本函数写入时会改的字段。若把政策复核放在前面，
            #   那么"同一个请求重试"会被 R9/R8/R14/R12 判成 policy_forbidden，
            #   而它其实只是一次无害的重试（正确语义是 duplicate）。
            existing = repo.find_after_sales_by_key(key)
            if existing is not None:
                _audit(
                    ctx["actor"],
                    "submit_after_sales_action.duplicate",
                    {"order_id": order_id, "kind": kind, "key": key, "existing_id": existing.id},
                    ok=False,
                )
                return {
                    "duplicate": True,
                    "action_id": existing.id,
                    "order_id": existing.order_id,
                    "kind": existing.kind,
                    "amount": existing.amount,
                    "status": existing.status,
                    "message": "该售后动作已存在（同一幂等键），未重复提交",
                    "idempotency_key": key,
                }

            o = repo.get_order(order_id)
            if not o:
                return {"error": "order_not_found"}

            items = repo.list_order_items(order_id)
            cust = repo.get_customer(o.customer_id)

            # ── 护栏 1：政策复核 ────────────────────────────────
            # ★ `evidence_provided` 从**工单**读，不从 order 读 —— 凭证是
            #   工单的属性（客户为这件事交的照片），不是订单的属性。
            #   golden 侧（`seed._decide_golden`）读同一个字段，两边必须一致，
            #   否则这条 case 天生不可赢（缺陷 ②③⑫ 的形状）。
            _ticket = repo.get_ticket(ctx["ticket_id"]) if ctx.get("ticket_id") else None
            d = decide_for_order(
                o,
                items=items,
                customer=cust,
                refund_count=len(repo.list_refunds_of_order(order_id)),
                request_kind=kind,
                request_cause=cause,
                request_amount=float(amount) if kind in MONEY_ACTION_KINDS else None,
                evidence_provided=bool(getattr(_ticket, "evidence_provided", False)),
            )
            want = expected_action_for(kind)
            if d.action != want:
                _audit(
                    ctx["actor"],
                    "submit_after_sales_action.blocked",
                    {
                        "order_id": order_id,
                        "kind": kind,
                        "cause": cause,
                        "policy_action": d.action,
                        "rule_hits": d.rule_hits,
                        "reason": "policy_forbidden",
                    },
                    ok=False,
                )
                hit = d.rule_hits[0] if d.rule_hits else d.action
                return {
                    "error": "policy_forbidden",
                    "requested_kind": kind,
                    "policy_action": d.action,
                    "rule_hits": d.rule_hits,
                    "reasons": d.reasons,
                    "message": (
                        f"政策结论与请求的动作不一致（{hit}）：请求 {kind}，"
                        f"政策给出 {d.action}。{'；'.join(d.reasons)}。"
                        "不得执行该动作，请按政策结论处置或转人工。"
                    ),
                }

            # ── 护栏 2：账本不变量（只对会出钱的动作）────────────
            amt = float(amount) if kind in MONEY_ACTION_KINDS else 0.0
            if amt > 0:
                approved = sum(
                    float(r.amount or 0.0)
                    for r in repo.list_refunds_of_order(order_id)
                    if r.status == "approved"
                )
                # ★★ 必须把**已经发出去的赔付/价保**也算进来 —— 它们和退款
                #    出自同一个钱包。只算 refunds 就是缺陷 ⑥ 的形状：
                #    护栏守住了它认识的那种钱，另一种钱安静地流出去。
                paid_out = sum(
                    float(a.amount or 0.0)
                    for a in repo.list_after_sales_of_order(order_id)
                    if a.status == "approved" and a.kind in MONEY_ACTION_KINDS
                )
                if (
                    approved + paid_out + amt - float(o.amount or 0.0)
                    > settings.eval_amount_tolerance_cny
                ):
                    _audit(
                        ctx["actor"],
                        "submit_after_sales_action.blocked",
                        {
                            "order_id": order_id,
                            "kind": kind,
                            "amount": amt,
                            "already_refunded": round(approved, 2),
                            "already_compensated": round(paid_out, 2),
                            "order_amount": float(o.amount or 0.0),
                            "reason": "amount_exceeds_order",
                        },
                        ok=False,
                    )
                    return {
                        "error": "amount_exceeds_order",
                        "order_amount": round(float(o.amount or 0.0), 2),
                        "already_refunded": round(approved, 2),
                        "already_compensated": round(paid_out, 2),
                        "requested": round(amt, 2),
                        "message": (
                            f"累计付出将超过订单金额：订单 {o.amount} 元，"
                            f"已退款 {round(approved, 2)} 元，已赔付 {round(paid_out, 2)} 元，"
                            f"本次 {round(amt, 2)} 元。不得超额。"
                        ),
                    }

            # ── 写库 ───────────────────────────────────────────
            a = AfterSalesAction(
                order_id=order_id,
                ticket_id=ctx["ticket_id"],
                kind=kind,
                amount=amt,
                reason=cause or detail,
                status="approved",
                idempotency_key=key,
                detail={"note": detail} if detail else {},
            )
            s.add(a)

            # 计数器同步：规则（R8/R9）和 R14 冷却读的就是这几个字段，
            # 如果这里不写，规则下次看到的还是 0 —— 那规则就等于没写。
            if kind == "exchange":
                o.exchange_count = (o.exchange_count or 0) + 1
            elif kind == "reship":
                o.reship_count = (o.reship_count or 0) + 1
            elif kind == "invoice":
                o.invoice_issued = True
            elif kind == "urge":
                o.last_urge_at = clock.now()
            s.flush()
            action_id = a.id

        _audit(
            ctx["actor"],
            f"submit_after_sales_action.{kind}",
            {"order_id": order_id, "kind": kind, "amount": amt, "action_id": action_id, "key": key},
        )
        return {
            "ok": True,
            "action_id": action_id,
            "order_id": order_id,
            "kind": kind,
            "amount": amt,
            "status": "approved",
            "idempotency_key": key,
        }

    except IntegrityError:
        _audit(
            ctx["actor"],
            "submit_after_sales_action.duplicate",
            {"order_id": order_id, "kind": kind, "key": key},
            ok=False,
        )
        return {
            "duplicate": True,
            "message": "该售后动作已存在（同一幂等键），未重复提交",
            "idempotency_key": key,
        }


def list_after_sales_of_order(order_id: int) -> dict[str, Any]:
    """列出该订单已有的非退款售后动作（判断"是否已补发过/换过"必查）。

    ★ 与 `list_refunds_of_order` 分成两个只读工具而不是合成一个：
    两者的字段与语义都不同（退款有 `requires_human`，售后动作有 `kind`/`detail`），
    合成一个会让 LLM 在一个混合列表里自己判断哪条是什么 —— 那是把归类工作
    推给模型，而归类应该是工具的返回值结构决定的。
    """
    ctx = _ctx_or_raise()
    with session_scope() as s:
        repo = TenantRepo(s, ctx["tenant_id"])
        acts = repo.list_after_sales_of_order(order_id)
        _audit(ctx["actor"], "list_after_sales_of_order", {"order_id": order_id, "n": len(acts)})
        return {
            "count": len(acts),
            "actions": [
                {
                    "action_id": a.id,
                    "kind": a.kind,
                    "amount": a.amount,
                    "status": a.status,
                    "reason": a.reason,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in acts
            ],
        }


def check_after_sales_eligibility(order_id: int, kind: str, cause: str = "", amount: float = 0.0) -> dict[str, Any]:
    """按 R8–R14 检查该订单能否受理指定的非退款售后诉求。

    ★ 与 `check_refund_eligibility` 并列的第二个"资格判定"工具，理由：
    退款线和售后线的规则条目、参数、结论都不相同（退款要看七天窗口，
    换货要看十五天窗口，催单要看 24 小时冷却）。合成一个工具会让
    "本该传三个参数的检查"变成一个参数巨多的检查，而 LLM 会漏传参数。

    判定逻辑在 `rules.decide()`，与 golden 共用 —— 不在这里重写规则。

    Args:
        order_id: 订单 ID
        kind: 诉求类型（exchange/reship/compensate/price_protect/invoice/address_change/urge）
        cause: 原因码，补发与换货的规则会读它
        amount: 涉及金额，赔付与价保需要
    """
    ctx = _ctx_or_raise()
    with session_scope() as s:
        repo = TenantRepo(s, ctx["tenant_id"])
        o = repo.get_order(order_id)
        if not o:
            return {"error": "order_not_found"}
        items = repo.list_order_items(order_id)
        cust = repo.get_customer(o.customer_id)

        # ★ `evidence_provided` 从工单读（与 `submit_after_sales_action` 同一处）：
        #   凭证是工单的属性。判定工具不读它，就会对一条"缺凭证"的诉求也判
        #   eligible —— 而提交工具那边会被政策护栏挡下，于是 agent 拿到
        #   "可以受理"的结论、照做却被拒，报表上只显示"agent 做错了"。
        #   这正是缺陷 ②③⑫ 的形状：两处读法不一致，case 天生不可赢。
        _ticket = repo.get_ticket(ctx["ticket_id"]) if ctx.get("ticket_id") else None
        d = decide_for_order(
            o,
            items=items,
            customer=cust,
            refund_count=len(repo.list_refunds_of_order(order_id)),
            request_kind=kind,
            request_cause=cause,
            request_amount=float(amount) if amount else None,
            evidence_provided=bool(getattr(_ticket, "evidence_provided", False)),
        )
        _audit(
            ctx["actor"],
            "check_after_sales_eligibility",
            {"order_id": order_id, "kind": kind, "action": d.action, "reasons": d.reasons},
        )
        return EligibilityResult(
            eligible=d.eligible, action=d.action, reasons=d.reasons, amount=d.amount
        ).model_dump()


# ══════════════════════════════════════════════════════════════
# 工具注册表
# ══════════════════════════════════════════════════════════════

_READ_TOOLS: list[Any] = [
    get_ticket,
    get_customer,
    list_orders_of_customer,
    get_order,
    list_order_items,
    list_refunds_of_order,
    list_after_sales_of_order,
    query_policy,
    days_since_signed,
    check_refund_eligibility,
    check_after_sales_eligibility,
    get_snapshot,
]

_WRITE_TOOLS_FN: list[Any] = [
    create_refund,
    block_order,
    close_ticket,
    escalate_to_human,
    submit_after_sales_action,
    mark_evidence_received,
    ask_customer,
]


# 显式声明每个工具的参数 schema。
# 不靠自动化推断：显式映射在改签名时会立刻报错，而自动推断会静默降级成宽松 schema，
# 让 LLM 有机会传错参数 —— 那种 bug 要到线上才发现。
ARG_SCHEMAS: dict[str, type[BaseModel] | None] = {
    # 只读工具的参数都是简单标量，LangChain 从类型注解推断即可
    "get_ticket": None,
    "get_customer": None,
    "list_orders_of_customer": None,
    "get_order": None,
    "list_order_items": None,
    "list_refunds_of_order": None,
    "list_after_sales_of_order": None,
    "query_policy": None,
    "days_since_signed": None,
    "check_refund_eligibility": None,
    "check_after_sales_eligibility": None,
    "get_snapshot": None,
    # 写工具需要显式 schema，给 LLM 更清晰的字段说明
    "create_refund": CreateRefundInput,
    "block_order": BlockOrderInput,
    "close_ticket": CloseTicketInput,
    "escalate_to_human": EscalateInput,
    "submit_after_sales_action": SubmitAfterSalesInput,
    "mark_evidence_received": MarkEvidenceInput,
    "ask_customer": AskCustomerInput,
}


def _to_structured(fn: Any) -> StructuredTool:
    name = fn.__name__
    if name not in ARG_SCHEMAS:
        raise KeyError(f"工具 {name!r} 未在 ARG_SCHEMAS 中声明参数 schema")
    return StructuredTool.from_function(
        func=fn,
        name=name,
        description=(fn.__doc__ or "").strip(),
        args_schema=ARG_SCHEMAS[name],
    )


class ToolRegistry:
    """按角色返回工具列表。★ 这是权限裁剪的唯一入口。"""

    def __init__(self) -> None:
        self._all: dict[str, Any] = {}
        for fn in _READ_TOOLS + _WRITE_TOOLS_FN:
            self._all[fn.__name__] = _to_structured(fn)

    def names_for(self, role: str) -> tuple[str, ...]:
        if role not in ROLE_TOOL_MATRIX:
            raise ValueError(f"未知角色 {role!r}，可选：{list(ROLE_TOOL_MATRIX)}")
        return tuple(n for n in ROLE_TOOL_MATRIX[role] if n in self._all)

    def get_tools(self, role: str) -> list[StructuredTool]:
        """★ 按角色物理裁剪。返回的 list 里**不包含**未授权工具的引用。"""
        return [self._all[n] for n in self.names_for(role)]

    def get(self, name: str, role: str) -> StructuredTool:
        """按名字取工具，但会校验该角色是否有权使用。"""
        if name not in ROLE_TOOL_MATRIX[role]:
            raise PermissionError(f"角色 {role!r} 无权使用工具 {name!r}")
        return self._all[name]

    def is_write(self, name: str) -> bool:
        return name in WRITE_TOOLS


registry = ToolRegistry()
