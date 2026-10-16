"""缺陷 ㉟ 的回归测试（新文件，与 test_clarification_loop.py 分开）。

为什么单独一个文件：这条缺陷横跨**两个模块**（`eval/harness.py` 的汇总 +
`graph/nodes.py` 的判据），放进任一个现有文件都会让那个文件的名义
（"多轮澄清" / "过程探针"）对不上内容。项目里 test_defect33.py 已有先例。

覆盖四组，每组都先用**阴性对照**再断言阳性（否则分不清是判据生效还是
本来就不报）：

  1. `_summarize_action` 认七个售后 kind：
     · 阴性：只读工具 / 空轨迹 → 'none'
     · 阳性：submit_after_sales_action(kind=X) → X 的标签
     · 阳性2：`ok=False` 的提交 → 仍 'none'（缺陷 ⑫ 的修法不能丢）
  2. `_expected_action` 与 `_summarize_action` **同一张表**：
     同一个 kind，两边必须给出同一个标签（否则报表两列不可比）
  3. `_case_should_clarify`：condition 以 need_clarification 结尾 / turn2_state
     非空 → True；普通条件 → False
  4. verifier 判据：should_clarify 与 pending_question 的四种组合
     （这组在 test_clarification_loop.py 里已有参数化版本，这里补
      "asked 但 golden 不要求" 时必须给出**可读原因**，不是只有 passed=False）

用法：python -m pytest -q tests/test_defect35.py
"""
from __future__ import annotations

import pytest

from ticket_agent.eval.harness import (
    _AFTER_SALES_KIND_LABEL,
    _case_should_clarify,
    _expected_action,
    _summarize_action,
)


def _submit(kind: str, *, ok: bool = True) -> list[dict]:
    return [{"name": "submit_after_sales_action", "ok": ok, "args": {"kind": kind}}]


# ── 1. 汇总：七个售后 kind 都要认 ─────────────────────────────


def test_summarize_none_for_readonly_and_empty():
    """阴性对照：没有写操作时必须是 'none'。"""
    assert _summarize_action([]) == "none"
    assert _summarize_action([{"name": "get_order"}, {"name": "get_ticket"}]) == "none"
    assert _summarize_action([{"name": "ask_customer"}]) == "none"
    assert _summarize_action([{"name": "mark_evidence_received"}]) == "none"


@pytest.mark.parametrize("kind", sorted(_AFTER_SALES_KIND_LABEL))
def test_summarize_recognizes_every_after_sales_kind(kind):
    """缺陷 ㉟ 的第一半：八个 kind 一个都不能漏。"""
    got = _summarize_action(_submit(kind))
    assert got == _AFTER_SALES_KIND_LABEL[kind], (
        f"submit_after_sales_action(kind={kind}) 被汇总成 {got!r}，"
        f"期望 {_AFTER_SALES_KIND_LABEL[kind]!r} —— 报表会把'真写了'报成'零动作'"
    )


def test_summarize_rejected_submit_is_not_an_action():
    """被拦下的提交不算动作（缺陷 ⑫ 的修法必须保留）。"""
    assert _summarize_action(_submit("compensate", ok=False)) == "none"


def test_summarize_close_ticket_still_wins_over_submit():
    """既有行为不回归：close_ticket 在轨迹后面时仍由它定性。

    scripted 基线的顺序是 submit → close_ticket，`_summarize_action` 按
    轨迹顺序返回**第一个**成功写的标签。所以这条断言的是"顺序语义"：
    先 submit 后 close → 返回 submit 的 kind（这是修复后的新行为），
    而只有 close_ticket 时仍返回 resolve/reject。
    """
    trace = _submit("urge") + [{"name": "close_ticket", "args": {"status": "resolved"}}]
    assert _summarize_action(trace) == "urge"
    only_close = [{"name": "close_ticket", "args": {"status": "rejected"}}]
    assert _summarize_action(only_close) == "reject"


# ── 2. 两半边用同一张表 ───────────────────────────────────────


@pytest.mark.parametrize("kind", sorted(_AFTER_SALES_KIND_LABEL))
def test_expected_action_uses_same_table(kind):
    """缺陷 ㉟ 的另一半：golden 反推必须给出同一个标签。"""
    snap = {
        "ticket": {"status": "resolved"},
        "order": {"status": "signed"},
        "refunds": [],
        "after_sales": [{"kind": kind, "status": "approved", "amount": 0.0}],
    }
    assert _expected_action(snap) == _AFTER_SALES_KIND_LABEL[kind]


def test_expected_action_refund_beats_after_sales():
    """refunds 优先于 after_sales（原行为，保持不变）。"""
    snap = {
        "ticket": {"status": "resolved"},
        "order": {"status": "signed"},
        "refunds": [{"status": "approved", "amount": 10.0}],
        "after_sales": [{"kind": "compensate", "status": "approved", "amount": 0.0}],
    }
    assert _expected_action(snap) == "refund"


# ── 3. case 层判据 ────────────────────────────────────────────


@pytest.mark.parametrize(
    "case,expected",
    [
        ({"condition": "R10:need_clarification"}, True),
        ({"condition": "R9:need_clarification", "turn2_state": None}, True),
        ({"condition": "R1:reject", "turn2_state": {"ticket": {"status": "rejected"}}}, True),
        ({"condition": "R1:reject"}, False),
        ({"condition": "ambiguous"}, False),
        ({}, False),
    ],
)
def test_case_should_clarify(case, expected):
    assert _case_should_clarify(case) is expected
