"""Intercom 三率的回归测试。

★ 这个测试钉的不是"代码能跑"，是三条会静默出错的口径：

1. **恒等式必须恒等**。`automation = involvement × resolution` 不是近似式。
   少了"通过的一律算参与"那个并集，一条「0 工具调用却通过」的运行就会让
   等式差一个尾数 —— 而那种差最难查：公式对不上，但每个数单看都对。
2. **`engaged == 0` 时 resolution 返回 `None` 而不是 `0.0`**。
   0.0 会被读成「参与了但一件都没办成」，真相是「没有可分辨的参与」——
   两个结论的改法完全不同（前者改 agent，后者改数据/链路）。
3. **转人工算参与、不算解决**。它确实去查了订单、跑了资格工具。

★ 每条断言都对应一个"读错了会怎样"，不是凑覆盖率。
"""

from __future__ import annotations

from ticket_agent.eval.harness import CaseResult, three_rates


def _res(
    cid: str,
    *,
    passed: bool,
    tool_calls: int = 3,
    error: str = "",
) -> CaseResult:
    """造一条最小运行结果。"""
    return CaseResult(
        case_id=cid,
        level="L1",
        arm="single",
        kind="scripted",
        passed=passed,
        tool_calls=tool_calls,
        error=error,
    )


def test_identity_holds_and_counts_are_right() -> None:
    rs = [
        _res("a", passed=True),
        _res("b", passed=False),
        _res("c", passed=True),
    ]
    t = three_rates(rs)
    assert t["total"] == 3
    assert t["resolved"] == 2
    assert t["identity_holds"] is True
    # 恒等式必须恒等，不是近似
    assert (
        abs(
            t["automation_rate"] - t["involvement_rate"] * t["resolution_rate"]
        )
        < 1e-12
    )


def test_passed_with_zero_tool_calls_still_counts_as_engaged() -> None:
    """★ 这条是恒等式的保险丝。

    resolved ⊆ engaged 靠的是那个显式并集。少了它，
    一条"0 工具调用却通过"的运行会让等式差一个尾数，
    而报表上三个数单看都"对"。
    """
    rs = [_res("a", passed=True, tool_calls=0)]
    t = three_rates(rs)
    assert t["resolved"] == 1
    assert t["engaged"] == 1
    assert t["resolution_rate"] == 1.0
    assert t["identity_holds"] is True


def test_escalate_counts_as_engaged_but_not_resolved() -> None:
    """转人工：算参与（真的查了数据），不算解决（事没办成）。"""
    rs = [_res("a", passed=False, tool_calls=5)]
    t = three_rates(rs)
    assert t["engaged"] == 1
    assert t["resolved"] == 0
    assert t["involvement_rate"] == 1.0
    assert t["resolution_rate"] == 0.0


def test_error_run_is_neither_resolved_nor_engaged() -> None:
    """报错的运行既不算解决也不算参与 —— 它根本没跑到业务。"""
    rs = [_res("a", passed=False, tool_calls=5, error="boom")]
    t = three_rates(rs)
    assert t["resolved"] == 0
    assert t["engaged"] == 0
    assert t["automation_rate"] == 0.0
    assert t["identity_holds"] is True


def test_resolution_is_none_when_nothing_engaged() -> None:
    """★ 除零返回 None，不是 0.0。

    0.0 的含义是"参与了但一件都没办成"，
    而这里的真相是"没有可分辨的参与" —— 改法不同。
    """
    rs = [_res("a", passed=False, tool_calls=0)]
    t = three_rates(rs)
    assert t["engaged"] == 0
    assert t["resolution_rate"] is None


def test_empty_input_is_not_a_crash() -> None:
    t = three_rates([])
    assert t["total"] == 0
    assert t["resolved"] == 0
    assert t["engaged"] == 0
    assert t["resolution_rate"] is None
    assert t["identity_holds"] is True
