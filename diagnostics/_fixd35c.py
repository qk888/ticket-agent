"""接线：把 case 层的 should_clarify 送进 verifier（缺陷 ㉟ 第二半）。

已改（`_fixd35.py`）：
  · `nodes.verifier_node(state, *, should_clarify=None)` + 判据收紧
  · `harness._case_should_clarify(case)`（case 层判据）

本段补三段（都在 `graph/builder.py` + `eval/harness.py`，因为 `run_ticket`
就在 builder 里，service 层不直接调 verifier）：

  1. `build_graph(arm, checkpointer, *, should_clarify=None)` —— 用闭包包住
     verifier 节点，把值固定在那次装配的闭包里。
  2. `run_ticket(..., should_clarify=None)` 透传。
  3. `harness.run_case` 两处调用传 `_case_should_clarify(case)`。

★ 为什么用闭包而不是全局/ContextVar：`should_clarify` 是**一次运行一个值**，
   且只被 verifier 读。闭包把它绑在这次装配的节点上，多 run 互不干扰，
   也不用引入新的全局状态（全局状态在本项目里已经惹过 ㉛ 和 ⑮ 两次祸）。
★ 为什么 service 层不传：服务端一次处理一张工单，`should_clarify` 缺省
   None → verifier 退回只看 `basis`（保守：宁可漏放行、不多放行）。
   评测层必须传，因为那里有 case 层的 golden 事实可用。

用法：python _fixd35c.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent
BUILDER = ROOT / "src" / "ticket_agent" / "graph" / "builder.py"
HARNESS = ROOT / "src" / "ticket_agent" / "eval" / "harness.py"


def sub(text: str, old: str, new: str, where: str, count: int = 1) -> str:
    n = text.count(old)
    assert n == count, f"{where}：期望命中 {count} 次，实际 {n} 次"
    return text.replace(old, new)


def main() -> int:
    # ── builder.py ───────────────────────────────────────────────────────
    b = BUILDER.read_text(encoding="utf-8")

    b = sub(
        b,
        'def build_graph(arm: Literal["single", "multi"] = "multi", checkpointer: Any = None) -> Any:',
        'def build_graph(\n'
        '    arm: Literal["single", "multi"] = "multi",\n'
        '    checkpointer: Any = None,\n'
        '    *,\n'
        '    should_clarify: bool | None = None,\n'
        ') -> Any:',
        "builder.build_graph 签名",
    )

    b = sub(
        b,
        """    ★ `interrupt()` 不在本项目使用：HITL 走的是图外的
    `service.approve_run / approve_ticket`，因为审批要落审计日志、
    要复用工具层的幂等与护栏，这些在图的 checkpoint 里没有对应物。
    \"\"\"""",
        """    ★ `interrupt()` 不在本项目使用：HITL 走的是图外的
    `service.approve_run / approve_ticket`，因为审批要落审计日志、
    要复用工具层的幂等与护栏，这些在图的 checkpoint 里没有对应物。

    `should_clarify`：**这条工单的 golden 是否要求先澄清**（缺陷 ㉟）。
    ★ 为什么用闭包绑在节点上、不用全局：它是"一次运行一个值"的量，
      只被 verifier 读。闭包把它固定在这次装配的节点里，多 run 互不干扰；
      全局变量在本项目已经惹过 ㉛（辅助随机流不重置）和 ⑮（.env 污染进程）
      两次祸，不再添第三种。评测层每次都传，服务层不传（缺省 None →
      verifier 退回只看 `basis`，宁可漏放行不多放行）。
    \"\"\"""",
        "builder.build_graph docstring",
    )

    b = sub(
        b,
        '        g.add_node("verifier", verifier_node)',
        "        # ★ 缺陷 ㉟：用闭包把 case 层的 `should_clarify` 绑进 verifier，\n"
        "        #   这样 verifier 读到的不是模型自己的陈述，而是造数时定下的事实。\n"
        "        def _verifier_node(state: AgentState, _sc: bool | None = should_clarify) -> dict[str, Any]:\n"
        "            return verifier_node(state, should_clarify=_sc)\n"
        "\n"
        '        g.add_node("verifier", _verifier_node)',
        "builder verifier 节点注册",
    )

    b = sub(
        b,
        """    user_input: str = "",
    checkpointer: Any = None,
) -> dict[str, Any]:
    \"\"\"跑一条工单，返回最终 state。评测层直接调这个。""",
        """    user_input: str = "",
    checkpointer: Any = None,
    *,
    should_clarify: bool | None = None,
) -> dict[str, Any]:
    \"\"\"跑一条工单，返回最终 state。评测层直接调这个。

    `should_clarify`：见 `build_graph` 的同名参数（缺陷 ㉟）。
    \"\"\"""",
        "builder.run_ticket 签名",
    )

    b = sub(
        b,
        "    graph = build_graph(arm=arm, checkpointer=checkpointer)",
        "    graph = build_graph(arm=arm, checkpointer=checkpointer, should_clarify=should_clarify)",
        "builder.run_ticket 建图调用",
    )
    BUILDER.write_text(b, encoding="utf-8")
    print(f"builder.py 改完（{len(b)} 字符）")

    # ── harness.py：两处 run_ticket 调用 ─────────────────────────────────
    h = HARNESS.read_text(encoding="utf-8")
    old_call = """            checkpointer=False,
        )"""
    new_call = """            checkpointer=False,
            # ★ 缺陷 ㉟：把 case 层的"该不该澄清"送进 verifier，让放行依据
            #   从"agent 自己写过的 pending_question"换成"golden 要求先澄清"。
            should_clarify=_case_should_clarify(case),
        )"""
    h = sub(h, old_call, new_call, "harness.run_case 第一处 run_ticket", count=2)
    HARNESS.write_text(h, encoding="utf-8")
    print(f"harness.py 改完（{len(h)} 字符），两处调用都已接线")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
