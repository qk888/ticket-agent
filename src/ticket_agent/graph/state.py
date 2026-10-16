"""图状态定义。

设计要点：
- **messages 用 add_messages reducer**：节点只返回增量，不手工拼接历史。
- **cost 用累加 reducer**：成本是跨节点累加的，不是被覆盖的。
- **decision / interrupt 相关字段**单独放，方便 trace 与评测归因。
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class Budget(TypedDict, total=False):
    """预算账本（难点 #5：多轮 token 累计熔断）。"""

    input_tokens: int
    output_tokens: int
    cost_cny: float
    model_calls: int
    # 是否已降级到便宜模型
    degraded: bool
    # 是否已触发熔断
    tripped: bool
    tripped_reason: str
    # 当前端点没声明单价 → cost_cny 是"不知道"，不是 0 元
    price_unknown: bool


class ToolCallRecord(TypedDict, total=False):
    """单次工具调用记录 —— 评测归因的数据源。"""

    seq: int
    actor: str
    name: str
    args: dict[str, Any]
    ok: bool
    ms: int
    result_digest: str


class Verdict(TypedDict, total=False):
    """verifier 的终态断言结果。"""

    passed: bool
    diffs: list[str]
    notes: str


class AgentState(TypedDict, total=False):
    # ── 运行时输入 ──
    tenant_id: str
    ticket_id: int
    run_id: str
    arm: Literal["single", "multi"]
    # 客户话语（可能多轮）
    user_input: str

    # ── 编排 ──
    messages: Annotated[list[AnyMessage], add_messages]
    # supervisor 派发目标
    next_worker: str
    # 转交次数，防止无限循环
    handoffs: int
    # 已完成的工作摘要（worker → 一句话产出）
    work_notes: Annotated[dict[str, str], lambda a, b: {**(a or {}), **(b or {})}]

    # ── 动作与产物 ──
    tool_calls: Annotated[list[ToolCallRecord], operator.add]
    actions_taken: Annotated[list[str], operator.add]
    forbidden_hits: Annotated[list[str], operator.add]
    verdict: Verdict

    # ── 预算 ──
    budget: Budget

    # ── 终局 ──
    finished: bool
    final_answer: str
    # HITL：是否需要人工确认，以及确认结果
    requires_human_approval: bool
    human_decision: str
    # 待人工确认的上下文（用于 interrupt 的展示）
    approval_request: dict[str, Any]


class RouteDecision(TypedDict):
    """supervisor 的路由决策（结构化输出）。"""

    next: Literal["triage", "resolver", "verifier", "FINISH"]
    reason: str


def new_state(
    tenant_id: str,
    ticket_id: int,
    run_id: str,
    arm: Literal["single", "multi"] = "multi",
    user_input: str = "",
) -> AgentState:
    return AgentState(
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        run_id=run_id,
        arm=arm,
        user_input=user_input,
        messages=[],
        next_worker="triage",
        handoffs=0,
        work_notes={},
        tool_calls=[],
        actions_taken=[],
        forbidden_hits=[],
        verdict={},
        budget=Budget(
            input_tokens=0,
            output_tokens=0,
            cost_cny=0.0,
            model_calls=0,
            degraded=False,
            tripped=False,
            tripped_reason="",
            price_unknown=False,
        ),
        finished=False,
        final_answer="",
        requires_human_approval=False,
        human_decision="",
        approval_request={},
    )
