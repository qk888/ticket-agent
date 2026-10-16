"""修两条因缺陷 ㉟ 收紧而变红的测试（红得对，不是回归）。

`test_pending_question_is_a_clarification_basis` 断言判据文本仍是
`'basis == "ambiguous" or bool('` —— 缺陷 ㉟ 收紧后这行已不存在，
所以它红。这个测试的**意图**（#2 的核心缺口：pending_question 必须算依据）
是对的，但它把"实现长什么样"钉死了；而 ㉟ 恰恰要求实现变。

改法：把断言从"源码文本"升级为"可执行语义"——
直接调 `verifier_node(state, should_clarify=...)` 喂四种组合，断言 verdict：

  · should_clarify=True  + pending_question 非空 → passed（#2 的缺口仍在）
  · should_clarify=False + pending_question 非空 → failed（缺陷 ㉟ 收紧）
  · should_clarify=None  + pending_question 非空 → failed（保守缺省）
  · basis=ambiguous                            → passed（原有行为）

这比读源码强：它测的是**判据**，不是**字面**。以后有人重构判据，
只要语义不变就不会红；语义真被改掉才会红。

`test_run_case_single_turn_stays_one` 也红：它取 `next(c for c in cases
if not c.get("customer_reply"))`，而现在两轮 case 的 `should_clarify`
恒 True，导致……先看它的实际报错再改，不要猜。

用法：python _fixtests35.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent
TESTS = ROOT / "tests" / "test_clarification_loop.py"


def sub(text: str, old: str, new: str, where: str) -> str:
    n = text.count(old)
    assert n == 1, f"{where}：期望命中 1 次，实际 {n} 次"
    return text.replace(old, new)


def main() -> int:
    t = TESTS.read_text(encoding="utf-8")

    old_block = '''def test_pending_question_is_a_clarification_basis():
    """`pending_question` 必须算澄清依据（#2 的核心缺口）。

    不直接调 verifier_node（要整张图），而是断言判据本身：
    读 `nodes.py` 里那段判据的语义。若有人把 `pending_question` 从判据里删掉，
    这个测试会红。
    """
    from ticket_agent.db.snapshot import TICKET_OBSERVED_FIELDS

    assert "pending_question" in TICKET_OBSERVED_FIELDS, (
        "pending_question 不在观测字段里 —— verifier 读不到它，#2 的判据就失效了"
    )
    # 判据文本仍须含 pending_question（防止有人改字段名/换实现）
    src = open(
        os.path.join(ROOT, "src", "ticket_agent", "graph", "nodes.py"),
        encoding="utf-8",
    ).read()
    assert 'basis == "ambiguous" or bool(' in src
    assert "pending_question" in src'''

    new_block = '''@pytest.mark.parametrize(
    "should_clarify,pending_question,expect_passed",
    [
        # #2 的核心缺口：golden 要求澄清 + agent 真问了 → 必须算依据
        (True, True, True),
        # 缺陷 ㉟：golden 不要求澄清，agent 自己问一句 → 不能免检
        (False, True, False),
        # 保守缺省：调用方没给 case 层事实时不放行
        (None, True, False),
        # 原有行为：多订单歧义时保持 open 是对的
        ("ambiguous", False, True),
        # 什么都没做、也没有歧义 → 必须报漏处置
        (False, False, False),
    ],
)
def test_pending_question_is_a_clarification_basis(
    should_clarify, pending_question, expect_passed
):
    """`pending_question` 算不算澄清依据，取决于 **golden 是否要求澄清**。

    ★ 这个测试原来读 `nodes.py` 的源码文本断言字面量
      （`'basis == "ambiguous" or bool('`）。缺陷 ㉟ 收紧判据后它红了 ——
      红得对：它把"实现长什么样"钉死了，而 ㉟ 恰恰要求实现变。
      所以升级成**直接调 verifier_node 喂四种组合**：测的是判据语义，
      不是字面。有人重构判据但语义不变 → 不红；语义真被改掉 → 红。
    """
    from ticket_agent.db.snapshot import TICKET_OBSERVED_FIELDS
    from ticket_agent.graph.nodes import verifier_node

    assert "pending_question" in TICKET_OBSERVED_FIELDS, (
        "pending_question 不在观测字段里 —— verifier 读不到它，#2 的判据就失效了"
    )

    basis = "ambiguous" if should_clarify == "ambiguous" else "linked"
    state = {
        "tenant_id": "acme",
        "ticket_id": 1,
        "run_id": "unit",
        "arm": "multi",
        "work_notes": {},
        "tool_calls": [],
        "messages": [],
    }
    # 让 verifier 读到我们指定的快照形状：monkeypatch 太重，这里用假工单
    # 会让 session_scope 打开真库 —— 所以直接在被测函数内部替换导出函数。
    import ticket_agent.graph.nodes as nodes_mod

    orig = nodes_mod.export_snapshot

    def fake_export(s, tenant_id, ticket_id):
        return {
            "ticket": {
                "status": "open",
                "pending_question": "请提供订单号" if pending_question else "",
                "customer_reply": "",
            },
            "order_basis": basis,
            "order": {"amount": 100.0, "refund_count": 0},
            "refunds": [],
        }

    nodes_mod.export_snapshot = fake_export
    try:
        out = verifier_node(state, should_clarify=should_clarify if should_clarify != "ambiguous" else None)
    finally:
        nodes_mod.export_snapshot = orig

    passed = out["verdict"]["passed"]
    assert passed is expect_passed, (
        f"should_clarify={should_clarify!r} pending_question={pending_question} "
        f"期望 passed={expect_passed}，实际 {passed}；diffs={out['verdict']['diffs']}"
    )'''

    t = sub(t, old_block, new_block, "test_pending_question_is_a_clarification_basis")

    # `test_customer_reply_is_not_a_clarification_basis` 读源码的 split 也要跟着改
    t = sub(
        t,
        '''    block = src.split('needs_clarification = basis == "ambiguous"')[1][:400]
    assert "customer_reply" not in block, (
        "答复被当成了澄清依据 —— 客户答了还没办成会被放行"
    )''',
        '''    # 缺陷 ㉟ 之后判据长这样：`asked and bool(should_clarify)`。
    # 仍然不许把 customer_reply 织进放行条件（否则 ⑲ 修好的漏处置又放回来）。
    assert "customer_reply" in src, "customer_reply 字段必须仍在 verifier 的视野里"
    block = src.split("needs_clarification = ")[1][:500]
    assert "customer_reply" not in block.split("if basis ==")[0], (
        "答复被当成了澄清依据 —— 客户答了还没办成会被放行"
    )''',
        "test_customer_reply_is_not_a_clarification_basis",
    )
    TESTS.write_text(t, encoding="utf-8")
    print(f"test_clarification_loop.py 改完（{len(t)} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
