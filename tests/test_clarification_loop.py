"""#2 多轮澄清闭环的单元测试。

覆盖五道关，每道关对应一个"能悄悄坏掉"的地方：
  1. seed 造出的两轮 case：golden 与 turn-1 同源、condition 不被覆盖
  2. `apply_customer_reply` 的确定性解析：补该补的、不覆盖已有的、
     已办结工单拒绝答复、"什么也没解析出来"要如实说
  3. `run_case` 两轮化：只有带 turn2_state 的 case 才跑第二轮
  4. verifier 判据：`pending_question` 算澄清依据，`customer_reply` 不算
  5. 端到端：一条 case 从 need_clarification 走到办完

★ 第 4 条是全套测试里最重要的：它同时防两个方向 ——
  既不能让"在等客户"被记成漏处置（#2 要修的），
  也不能让"客户答了还没办"被放行（那会把缺陷 ⑲ 修好的又放回来）。
"""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

os.environ.setdefault("DATABASE_URL", "sqlite:///./eval_out/pytest_p2.db")

from ticket_agent.db import seed as seed_mod  # noqa: E402
from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import init_db, session_scope  # noqa: E402
from ticket_agent.eval.harness import run_case  # noqa: E402
from ticket_agent.graph.nodes import apply_customer_reply  # noqa: E402


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    init_db(drop=True)
    seed_all(n_customers=60, seed=7)
    return build_eval_cases(size=40, seed=11)


def _first_two_turn(cases) -> dict:
    two = [c for c in cases if c.get("customer_reply")]
    if not two:
        pytest.skip("这批造数没有两轮 case")
    return two[0]


@pytest.fixture()
def two_turn(cases) -> dict:
    """每条测试开工前把工单**重置回 turn-1 之前**。

    ★ 为什么必须这么做：这些测试会真的写库，而模块级 `cases` fixture 只
    跑一次。上一个测试把工单办成 resolved 之后，下一个测试拿到的就是
    "已办结工单"——`apply_customer_reply` 正确地拒绝它，于是一片红，
    而红的不是被测逻辑，是**测试之间的状态串味**。
    这与缺陷 ④"只看终态"的同形教训一样：Fixture 的初始状态必须是确定的。
    """
    c = _first_two_turn(cases)
    from ticket_agent.db.snapshot import apply_snapshot

    with session_scope() as s:
        from ticket_agent.db.session import TenantRepo

        t = TenantRepo(s, c["tenant_id"]).get_ticket(c["ticket_id"])
        if t is not None:
            apply_snapshot(s, c["tenant_id"], c["ticket_id"], c["initial_state"])
    # ★ 重置后必须真的回到 open：`run_case` 第一步也会 apply 一次，
    #   但 turn-1 跑完工单可能已 resolved —— 那时 `apply_customer_reply`
    #   正确地拒绝答复，测试会红，而红的不是被测逻辑。
    with session_scope() as s:
        from ticket_agent.db.session import TenantRepo

        t = TenantRepo(s, c["tenant_id"]).get_ticket(c["ticket_id"])
        assert t.status == "open", f"fixture 重置后工单应为 open，实际 {t.status}"
    return c


# ── 1. seed 侧 ────────────────────────────────────────────────


def test_seed_produces_two_turn_cases(cases):
    two = [c for c in cases if c.get("customer_reply")]
    assert two, "一条两轮 case 都没造出来 —— 多轮澄清闭环根本没有被评测覆盖"
    for c in two:
        assert c["turn2_state"], "有答复却没有 turn-2 golden"
        assert c["clarified"], "没有记录第二轮补齐了什么"


def test_turn2_condition_does_not_overwrite_turn1(cases):
    """覆盖度审计按 condition 数规则覆盖面，turn-2 的动作不能顶掉 turn-1 的。

    否则"信息不足"这一类会从报表上消失 —— 又是"0 条 case 与从不犯错同形"。
    """
    two = [c for c in cases if c.get("customer_reply")]
    assert two
    for c in two:
        assert c["condition"].endswith("need_clarification"), (
            f"{c['case_id']} 的 condition 被 turn-2 覆盖成了 {c['condition']!r}"
        )
        assert "turn2_condition" in c


def test_turn2_golden_is_not_open(cases):
    """turn-2 golden 必须是"能办完"的终态，否则这条 case 没有第二轮可言。"""
    for c in cases:
        if not c.get("turn2_state"):
            continue
        assert c["turn2_state"]["ticket"]["status"] != "open"


def test_single_turn_cases_have_no_reply(cases):
    """一次就能办完的 case 不该被强加第二轮（否则测的不是澄清闭环）。"""
    one = [c for c in cases if not c.get("customer_reply")]
    assert one
    for c in one:
        assert "turn2_state" not in c


# ── 2. 答复解析 ───────────────────────────────────────────────


def _first_two_turn(cases) -> dict:
    two = [c for c in cases if c.get("customer_reply")]
    if not two:
        pytest.skip("这批造数没有两轮 case")
    return two[0]


def test_apply_reply_fills_missing_fields(two_turn):
    c = two_turn
    r = apply_customer_reply(c["tenant_id"], c["ticket_id"], c["customer_reply"])
    assert r["ok"] is True
    assert r["changed"].get("customer_reply") is True
    # 补齐的字段必须与 seed 记录的 clarified 一致（两套代码不能漂）
    for k, v in (c.get("clarified") or {}).items():
        if k == "request_cause" and v:
            assert r["changed"].get(k) == v
        if k == "request_amount" and v:
            assert r["changed"].get(k) == v
        if k == "evidence_provided" and v:
            assert r["changed"].get(k) is True


def test_apply_reply_does_not_overwrite_existing_fields(two_turn):
    """已有原因/金额不能被答复覆盖（答复只补缺，不改答）。"""
    c = two_turn
    with session_scope() as s:
        from ticket_agent.db.session import TenantRepo

        t = TenantRepo(s, c["tenant_id"]).get_ticket(c["ticket_id"])
        before = (t.request_cause, t.request_amount, t.evidence_provided)
    # 用一条不含任何可解析信息的答复
    r = apply_customer_reply(c["tenant_id"], c["ticket_id"], "好的谢谢")
    assert r["ok"] is True
    after = r["changed"]
    assert "request_cause" not in after or after["request_cause"] == before[0]
    assert "request_amount" not in after or after["request_amount"] == before[1]
    assert "evidence_provided" not in after


def test_apply_reply_reports_nothing_to_apply(two_turn):
    """解析不出东西必须如实说，不能假装写成功了。"""
    c = two_turn
    r = apply_customer_reply(c["tenant_id"], c["ticket_id"], "嗯嗯")
    assert r["ok"] is True
    assert r["nothing_to_apply"] is True


def test_apply_reply_rejects_closed_ticket(cases):
    """已办结的工单不接受答复 —— 否则第二轮会在 resolved 工单上重跑。

    ★ 用 `_first_two_turn` 直接跑，不重置：这个测试要的**就是**已办结状态。
    """
    c = _first_two_turn(cases)
    run_case(c, arm="multi", kind="scripted", k_index=0)
    with session_scope() as s:
        from ticket_agent.db.session import TenantRepo

        t = TenantRepo(s, c["tenant_id"]).get_ticket(c["ticket_id"])
        status = t.status
    assert status in ("resolved", "rejected"), (
        "两轮跑完工单竟然没办结 —— turn-2 golden 与实跑不一致"
    )
    r = apply_customer_reply(c["tenant_id"], c["ticket_id"], "照片发了")
    assert r["ok"] is False
    assert r["error"] == "ticket_closed"


def test_apply_reply_rejects_empty():
    r = apply_customer_reply("acme", 1, "   ")
    assert r["ok"] is False
    assert r["error"] == "empty_reply"


def test_apply_reply_rejects_unknown_ticket():
    r = apply_customer_reply("no-such-tenant", 999999, "照片发了")
    assert r["ok"] is False
    assert r["error"] == "ticket_not_found"


def test_apply_reply_rejects_closed_ticket(cases):
    """已办结的工单不接受答复 —— 否则第二轮会在 resolved 工单上重跑。"""
    c = _first_two_turn(cases)
    run_case(c, arm="multi", kind="scripted", k_index=0)
    with session_scope() as s:
        from ticket_agent.db.session import TenantRepo

        t = TenantRepo(s, c["tenant_id"]).get_ticket(c["ticket_id"])
        status = t.status
    if status in ("resolved", "rejected"):
        r = apply_customer_reply(c["tenant_id"], c["ticket_id"], "照片发了")
        assert r["ok"] is False
        assert r["error"] == "ticket_closed"


def test_apply_reply_clears_pending_question(two_turn):
    """答复到达必须清掉 pending_question，否则 verifier 永远认为在等客户。

    ★ 这里不能先跑 `run_case`（那会把工单办成 resolved，答复就进不来了），
    所以直接手工造一个"问了还没答"的状态再验清除。
    """
    c = two_turn
    with session_scope() as s:
        from ticket_agent.db.session import TenantRepo

        t = TenantRepo(s, c["tenant_id"]).get_ticket(c["ticket_id"])
        assert t.status == "open", "fixture 重置后工单应为 open"
        t.pending_question = "请提供商品照片"
    apply_customer_reply(c["tenant_id"], c["ticket_id"], c["customer_reply"])
    with session_scope() as s:
        from ticket_agent.db.session import TenantRepo

        t = TenantRepo(s, c["tenant_id"]).get_ticket(c["ticket_id"])
        assert t.pending_question == ""
        assert t.customer_reply


def test_apply_reply_writes_audit(two_turn):
    """答复是外部事件，必须进审计（否则"结论从哪句话推出来的"说不清）。"""
    c = two_turn
    apply_customer_reply(c["tenant_id"], c["ticket_id"], "照片已经发给你了")
    with session_scope() as s:
        from sqlalchemy import select

        from ticket_agent.db.models import AuditLog

        rows = list(
            s.scalars(
                select(AuditLog).where(
                    AuditLog.actor == "customer",
                    AuditLog.ticket_id == c["ticket_id"],
                )
            )
        )
    assert rows, "答复没有写审计"


# ── 3. harness 两轮化 ─────────────────────────────────────────


def test_run_case_two_turn_marks_turns(two_turn):
    c = two_turn
    res = run_case(c, arm="multi", kind="scripted", k_index=0)
    assert res.turns == 2, res.error
    assert res.tool_calls > 0


def test_run_case_single_turn_stays_one(cases):
    one = next(c for c in cases if not c.get("customer_reply"))
    res = run_case(one, arm="multi", kind="scripted", k_index=0)
    assert res.turns == 1


def test_run_case_two_turn_passes(two_turn):
    """端到端：拿到答复后要能办完，且终态与 turn-2 golden 一致。"""
    c = two_turn
    res = run_case(c, arm="multi", kind="scripted", k_index=0)
    assert res.error == "", res.error
    assert res.passed, res.diffs


# ── 4. verifier 判据 ──────────────────────────────────────────


@pytest.mark.parametrize(
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
    )


def test_customer_reply_is_not_a_clarification_basis():
    """答复**不算**澄清依据 —— 否则会把缺陷 ⑲ 修好的漏处置又放回来。"""
    src = open(
        os.path.join(ROOT, "src", "ticket_agent", "graph", "nodes.py"),
        encoding="utf-8",
    ).read()
    # 缺陷 ㉟ 之后判据长这样：`asked and bool(should_clarify)`。
    # 仍然不许把 customer_reply 织进放行条件（否则 ⑲ 修好的漏处置又放回来）。
    assert "customer_reply" in src, "customer_reply 字段必须仍在 verifier 的视野里"
    block = src.split("needs_clarification = ")[1][:500]
    assert "customer_reply" not in block.split("if basis ==")[0], (
        "答复被当成了澄清依据 —— 客户答了还没办成会被放行"
    )


# ── 5. 服务层 ─────────────────────────────────────────────────


def test_submit_customer_reply_service(two_turn):
    from ticket_agent.service import submit_customer_reply

    c = two_turn
    out = submit_customer_reply(c["tenant_id"], c["ticket_id"], c["customer_reply"])
    assert out["reran"] is True
    assert out["run_id"]


def test_submit_customer_reply_dry_run_does_not_rerun(two_turn):
    from ticket_agent.service import submit_customer_reply

    c = two_turn
    out = submit_customer_reply(
        c["tenant_id"], c["ticket_id"], c["customer_reply"], dry_run=True
    )
    assert out["reran"] is False
    assert "run_id" not in out


def test_submit_customer_reply_raises_on_closed(two_turn):
    from ticket_agent.service import submit_customer_reply

    c = two_turn
    run_case(c, arm="multi", kind="scripted", k_index=0)
    with session_scope() as s:
        from ticket_agent.db.session import TenantRepo

        t = TenantRepo(s, c["tenant_id"]).get_ticket(c["ticket_id"])
        status = t.status
    if status in ("resolved", "rejected"):
        with pytest.raises(ValueError, match="ticket_closed"):
            submit_customer_reply(c["tenant_id"], c["ticket_id"], "照片发了")


def test_ask_customer_tool_is_registered():
    from ticket_agent.tools.registry import registry

    for role in ("resolver", "single"):
        assert "ask_customer" in registry.names_for(role), (
            f"ask_customer 没给 {role} —— 需要它的角色问不出问题"
        )
    for role in ("triage", "verifier"):
        assert "ask_customer" not in registry.names_for(role), (
            f"ask_customer 不该给 {role}（只读角色不能写库）"
        )
