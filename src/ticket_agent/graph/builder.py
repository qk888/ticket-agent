"""图装配。

两条 arm：
- `single`：单 agent 拿全部工具，一次 ReAct 跑完（对照组）
- `multi` ：supervisor 调度 triage → resolver → verifier（实验组）

★ 这是本项目做消融实验的基础：**同一套工具、同一套状态、同一套评测口径**，
唯一变量是"要不要拆角色"。这样才能回答"多智能体到底带来了什么"。
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, StateGraph

from ..config import settings
from .checkpoint import get_checkpointer
from .nodes import (
    _run_react_loop,
    build_model,
    resolver_node,
    triage_node,
    verifier_node,
)
from .state import AgentState, new_state
from ..tools.registry import ROLE_TOOL_MATRIX, set_tool_context
from .middleware import BudgetExceeded, BudgetTracker, merge_budget
from .prompts import SINGLE_PROMPT

MAX_HANDOFFS = 6
"""supervisor 最多转交几次。超过就强制结束 —— 防死循环烧钱。"""


# ══════════════════════════════════════════════════════════════
# supervisor 路由
# ══════════════════════════════════════════════════════════════


def _pick_next(state: AgentState) -> tuple[str, str]:
    """决定下一步走哪个角色。

    ★ 这里用**确定性状态机**而不是"让 LLM 当 supervisor"：

    原因很直接 —— 在只有 3 个角色、依赖关系固定的流程里，LLM 路由是纯粹的
    不确定性来源，还会多烧一次 LLM 调用。它的唯一好处是"灵活"，而这个场景不需要灵活。

    什么情况才值得上 LLM supervisor：角色超过 5 个、依赖关系随任务动态变化、
    或者需要按内容分诊到不同专家（比如"这是技术问题就找技术组"）。
    本项目把 LLM supervisor 作为**可选对照**保留在 `llm_supervisor=True` 分支里，
    两种路由方式的 token 消耗差异会体现在评测报告里。
    """
    notes = state.get("work_notes") or {}
    handoffs = state.get("handoffs", 0)

    if handoffs >= MAX_HANDOFFS:
        return "FINISH", f"转交次数达上限 {MAX_HANDOFFS}，强制结束"

    if "triage" not in notes:
        return "triage", "尚未分诊"
    if "resolver" not in notes:
        return "resolver", "分诊完成，待处置"
    if "verifier" not in notes:
        return "verifier", "处置完成，待独立校验"
    return "FINISH", "校验完成"


def supervisor_node(state: AgentState) -> dict[str, Any]:
    nxt, reason = _pick_next(state)
    return {
        "next_worker": nxt,
        "handoffs": state.get("handoffs", 0) + 1,
        "work_notes": {"supervisor": f"→ {nxt}（{reason}）"},
    }


def route_from_supervisor(state: AgentState) -> Literal["triage", "resolver", "verifier", "__end__"]:
    nxt = state.get("next_worker")
    if nxt == "FINISH":
        return "__end__"
    return nxt  # type: ignore[return-value]


# ══════════════════════════════════════════════════════════════
# single arm 节点
# ══════════════════════════════════════════════════════════════


def single_node(state: AgentState) -> dict[str, Any]:
    """单 agent arm：一个 ReAct 循环跑完全流程。"""
    tenant_id, ticket_id = state["tenant_id"], state["ticket_id"]
    set_tool_context(
        tenant_id=tenant_id, ticket_id=ticket_id, actor="single", run_id=state.get("run_id", "")
    )

    parts = [f"工单 ID：{ticket_id}，租户：{tenant_id}"]
    if state.get("user_input"):
        parts.append(f"客户原话：{state['user_input']}")

    from langchain_core.messages import HumanMessage, SystemMessage

    base = [SystemMessage(content=SINGLE_PROMPT), HumanMessage(content="\n".join(parts))]

    tracker = BudgetTracker()
    tripped = False
    try:
        new_msgs, trace, actions, forbidden, final_text = _run_react_loop(
            "single", base, tracker, state.get("tool_calls") or []
        )
    except BudgetExceeded as exc:
        tr = exc.tracker or tracker
        from langchain_core.messages import AIMessage

        final_text = f"[预算熔断] {tr.tripped_reason}，转人工处理。"
        new_msgs = [AIMessage(content=final_text)]
        trace, actions, forbidden = [], [], []
        tripped = True
    if tracker.tripped:
        tripped = True

    budget = merge_budget(tracker, state.get("budget"))
    out: dict[str, Any] = {
        "messages": new_msgs,
        "tool_calls": trace,
        "actions_taken": actions,
        "forbidden_hits": forbidden,
        "budget": budget,
        "work_notes": {"single": (final_text or "").strip().replace("\n", " ")[:300]},
        "final_answer": final_text,
        "next_worker": "FINISH",
        "finished": True,
    }
    if tripped:
        out["requires_human_approval"] = True
        out["approval_request"] = {
            "ticket_id": ticket_id,
            "reason": f"预算熔断：{budget.get('tripped_reason', '')}",
        }
    return out


# ══════════════════════════════════════════════════════════════
# 图装配
# ══════════════════════════════════════════════════════════════


def build_graph(
    arm: Literal["single", "multi"] = "multi",
    checkpointer: Any = None,
    *,
    should_clarify: bool | None = None,
) -> Any:
    """装配并编译图。

    `checkpointer` 传入后即可用 thread_id 做**断点续跑**。

    ★ 注意这里传的是 `None` 而不是 `get_checkpointer()` 的结果：
    **传 `None` 表示"按 settings.checkpointer 走"**（`memory` 时等同不持久化）。
    这是刻意的默认 —— 服务层 `stream_run` 想持久化只需 `build_graph(arm)`，
    而评测层要的是**每次全新**，于是显式传 `False` 关掉。

    三态约定（别再引入第四态）：
      - `None`  → 按 `settings.checkpointer` 决定（`memory` → 不持久化）
      - `False` → 强制不持久化（评测用；见 `eval/harness.py` 的注释）
      - 其它    → 当作 checkpointer 实例直接用

    ★ `interrupt()` 不在本项目使用：HITL 走的是图外的
    `service.approve_run / approve_ticket`，因为审批要落审计日志、
    要复用工具层的幂等与护栏，这些在图的 checkpoint 里没有对应物。

    `should_clarify`：**这条工单的 golden 是否要求先澄清**（缺陷 ㉟）。
    ★ 为什么用闭包绑在节点上、不用全局：它是"一次运行一个值"的量，
      只被 verifier 读。闭包把它固定在这次装配的节点里，多 run 互不干扰；
      全局变量在本项目已经惹过 ㉛（辅助随机流不重置）和 ⑮（.env 污染进程）
      两次祸，不再添第三种。评测层每次都传，服务层不传（缺省 None →
      verifier 退回只看 `basis`，宁可漏放行不多放行）。
    """
    if checkpointer is None:
        checkpointer = get_checkpointer()
    elif checkpointer is False:
        checkpointer = None

    g = StateGraph(AgentState)

    if arm == "single":
        g.add_node("single", single_node)
        g.set_entry_point("single")
        g.add_edge("single", END)
    else:
        g.add_node("supervisor", supervisor_node)
        g.add_node("triage", triage_node)
        g.add_node("resolver", resolver_node)
        # ★ 缺陷 ㉟：用闭包把 case 层的 `should_clarify` 绑进 verifier，
        #   这样 verifier 读到的不是模型自己的陈述，而是造数时定下的事实。
        def _verifier_node(state: AgentState, _sc: bool | None = should_clarify) -> dict[str, Any]:
            return verifier_node(state, should_clarify=_sc)

        g.add_node("verifier", _verifier_node)

        g.set_entry_point("supervisor")
        g.add_conditional_edges(
            "supervisor",
            route_from_supervisor,
            {"triage": "triage", "resolver": "resolver", "verifier": "verifier", "__end__": END},
        )
        g.add_edge("triage", "supervisor")
        g.add_edge("resolver", "supervisor")
        g.add_edge("verifier", END)

    return g.compile(checkpointer=checkpointer)


def run_ticket(
    tenant_id: str,
    ticket_id: int,
    arm: Literal["single", "multi"] = "multi",
    run_id: str = "",
    user_input: str = "",
    checkpointer: Any = None,
    *,
    should_clarify: bool | None = None,
) -> dict[str, Any]:
    """跑一条工单，返回最终 state。评测层直接调这个。

    `should_clarify`：见 `build_graph` 的同名参数（缺陷 ㉟）。

    ★ `configurable.thread_id` 必须跟着**解析后**的 checkpointer 走，
    不能看入参：入参 `None` 表示"按配置来"，此时真正的 saver 决定权在
    `build_graph` 里。若图有 checkpointer 而 config 里没有 thread_id，
    LangGraph 会直接报错（不是静默降级）—— 所以这里先解析一次再判断。
    """
    import uuid

    run_id = run_id or uuid.uuid4().hex[:12]
    resolved = get_checkpointer() if checkpointer is None else checkpointer
    graph = build_graph(arm=arm, checkpointer=checkpointer, should_clarify=should_clarify)
    init = new_state(tenant_id, ticket_id, run_id, arm=arm, user_input=user_input)
    cfg: dict[str, Any] = {"recursion_limit": 50}
    if resolved is not None and resolved is not False:
        cfg["configurable"] = {"thread_id": run_id}
    return graph.invoke(init, config=cfg)
