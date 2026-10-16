"""#60 凭证能力的正式单测。

`_proofcheck.py` 验的是端到端链路；这里把同样的约束钉进 pytest，
否则下次改规则时它又会退化 —— 这正是本项目反复吃亏的地方。

覆盖四层：
  1. 规则层：需凭证未给 → need_clarification；已给 → 受理；不需凭证的原因不受影响
  2. 工具层：mark_evidence_received 能翻转字段、重复标记幂等
  3. 工具层：get_ticket 把 evidence_provided 暴露给 agent
  4. 造数层：seed 造出的工单里，需凭证的两个分支都出现
"""

from __future__ import annotations

import datetime as dt

import pytest

from ticket_agent import rules
from ticket_agent.db.models import Ticket
from ticket_agent.db.seed import seed_all
from ticket_agent.tools.registry import (
    get_ticket,
    mark_evidence_received,
    set_tool_context,
)
from ticket_agent.db.session import TenantRepo, session_scope


def _decide(**kw):
    base = dict(
        order_status="signed",
        amount=200.0,
        categories={"clothing"},
        signed_at=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc),
    )
    base.update(kw)
    return rules.decide(**base)


@pytest.mark.parametrize(
    "kind,cause",
    [
        ("exchange", "quality"),
        ("reship", "damaged"),
        ("reship", "wrong_item"),
        ("reship", "lost"),
        ("compensate", "quality"),
    ],
)
def test_missing_evidence_blocks_the_action(kind: str, cause: str) -> None:
    """政策要求凭证而客户没给 → 必须 need_clarification，不能默认受理。

    ★ 这是本次修复的核心断言。在 `Ticket.evidence_provided` 出现之前，
    这五条路径全部静默通过 —— 政策写了"需提供凭证"，代码里 0 处检查它。
    """
    d = _decide(
        request_kind=kind,
        request_cause=cause,
        request_amount=20.0 if kind == "compensate" else None,
        evidence_provided=False,
    )
    assert d.action == "need_clarification", d.reasons


@pytest.mark.parametrize(
    "kind,cause",
    [
        ("exchange", "quality"),
        ("reship", "damaged"),
        ("compensate", "quality"),
    ],
)
def test_provided_evidence_lets_it_through(kind: str, cause: str) -> None:
    """给了凭证 → 走正常受理路径（守卫不再拦截）。"""
    d = _decide(
        request_kind=kind,
        request_cause=cause,
        request_amount=20.0 if kind == "compensate" else None,
        evidence_provided=True,
    )
    assert d.action == rules.expected_action_for(kind), d.reasons


def test_non_proof_cause_is_unaffected() -> None:
    """不需要凭证的原因（尺码不符）不能被新守卫误伤。"""
    d = _decide(request_kind="exchange", request_cause="size", evidence_provided=False)
    assert d.action == "exchange"


def test_proof_causes_matches_policy_text() -> None:
    """PROOF_CAUSES 必须是 REQUEST_CAUSES 的子集，且非空。

    否则要么判定时 KeyError，要么整条守卫永远不触发。
    """
    assert rules.PROOF_CAUSES
    assert rules.PROOF_CAUSES <= set(rules.REQUEST_CAUSES)


# ── 工具层与造数层（用独立库，不碰开发库）───────────────────────


@pytest.fixture(scope="module")
def proof_db():
    """造 120 个客户 —— 需要凭证的原因合计只占语料约 7%，
    小样本抽不到稳定，会造出"时好时坏"的假失败。

    ★ 用 `init_db(drop=True)` 打在**测试库**上（conftest 已设好
    DATABASE_URL），不会碰开发库 —— 那正是 conftest 存在的理由。
    """
    from ticket_agent.db.session import init_db

    init_db(drop=True)
    seed_all(n_customers=120, seed=5)
    return True


def test_mark_evidence_tool_flips_the_field(proof_db) -> None:
    with session_scope() as s:
        t = next(
            (x for x in s.query(Ticket).all()
             if x.request_cause in rules.PROOF_CAUSES and not x.evidence_provided),
            None,
        )
        assert t is not None, "造数没造出「需凭证但未给」的工单"
        tenant, tid = t.tenant_id, t.id

    set_tool_context(tenant, tid, "resolver", "pytest-proof")
    r = mark_evidence_received("photo", "客户发了照片")
    assert r.get("ok") is True, r

    with session_scope() as s:
        t2 = TenantRepo(s, tenant).get_ticket(tid)
        assert t2.evidence_provided is True

    # 幂等：重复标记不报错
    r2 = mark_evidence_received("photo")
    assert r2.get("already") is True, r2


def test_get_ticket_exposes_evidence_provided(proof_db) -> None:
    """任务输入必须对 agent 可见，否则 golden 与 agent 读的不是同一个值。"""
    with session_scope() as s:
        t = next(
            (x for x in s.query(Ticket).all()
             if x.request_cause in rules.PROOF_CAUSES and not x.evidence_provided),
            None,
        )
        assert t is not None
        tenant, tid = t.tenant_id, t.id

    set_tool_context(tenant, tid, "resolver", "pytest-proof")
    g = get_ticket()
    assert g.get("evidence_provided") is False
    assert "evidence_provided" in g


def test_task_input_fields_are_all_reachable_from_get_ticket(proof_db) -> None:
    """四条任务输入字段必须**全部**能从 `get_ticket` 的返回体读到。

    ★ 为什么是集合断言而不是像上一条那样手工挑一个字段：
      上面那条 `test_get_ticket_exposes_evidence_provided` 只断言了
      `evidence_provided` 一个 —— 这与 `_readmecheck.py` 那次教训同形
      （手抄标题字符串 → 加缺陷时体检报「标题与缺陷数不一致」）。
      手工挑的清单必然漏项，而漏项的后果是**一条不可赢的 case**：
      golden 用了一个 agent 拿不到的信息，报表只显示「agent 没办成」。

    ★ 这四例是同一个病根，已在同一个位置复发四次：
      ㉓ `rules.decide_for_order` 没把参数透传进 `decide`
         —— 参数传不进 ≡ 规则没写；
      ㉚ `current_price` 是五个计数器里唯一不从 order 回落的一个
         —— 数据在 order 上，判定入口没去读；
      本处 `get_ticket` 返回体漏 `request_amount`
         —— 而它的**上一行注释**已经把它算作解决过的同类例子。
      实测见 `_r10diff.py`：千级库 4/4 条目标 case 在补上该键后
      由 `passed=False`（终态卡 `open`、`after_sales=[]`）转为 `passed=True`。

    ★ 范围只取这四个，**不是** `TICKET_OBSERVED_FIELDS` 全 8 项：
      那个元组是「报表要看的」，里面 `priority` 并不在 `get_ticket` 返回体里
      （已核 `registry.py:294-316`），`intent` 虽可达但不是任务输入。
      把两者混为一谈会让本守卫写错范围 —— 观测组的语义是诊断，不是输入。
    """
    from ticket_agent.tools.registry import ToolRegistry

    with session_scope() as s:
        t = s.query(Ticket).first()
        assert t is not None, "proof_db 里一条工单都没有 —— 造数没跑"
        tenant, tid = t.tenant_id, t.id

    set_tool_context(tenant, tid, "resolver", "pytest-proof")
    # ★ 真调一次而不是扫源码：扫源码只能证明「代码里出现过这个字符串」，
    #   证明不了它真的在返回体里（拼错字典键也能扫到）。
    tool = ToolRegistry().get("get_ticket", "single")
    got = set(tool.invoke({"ticket_id": tid}) or {})

    required = {
        "request_kind",
        "request_cause",
        "request_amount",
        "evidence_provided",
    }
    missing = sorted(required - got)
    assert not missing, (
        f"get_ticket 没有暴露这些任务输入字段：{missing}"
        f"（返回体实际有 {sorted(got)}）"
        f" —— golden 按它们算分，agent 读不到就等于这条 case 不可赢。"
        f" 已在此复发过：㉓ 参数传不进、㉚ current_price 不回落、"
        f" 本处 request_amount 漏键（注释还把它算作已解决）。"
    )


def test_seed_produces_both_evidence_branches(proof_db) -> None:
    """两个分支都要出现 —— 只给不没给或只没给不给，
    都会让另一条规则路径永远测不到（缺陷 ⑫ 稀释形状）。"""
    with session_scope() as s:
        rows = s.query(Ticket).all()
    need = [t for t in rows if t.request_cause in rules.PROOF_CAUSES]
    given = [t for t in need if t.evidence_provided]
    assert need, "造数没造出任何需要凭证的工单"
    assert given, "所有需凭证工单都标成已给 —— need_clarification 分支测不到"
    assert len(given) < len(need), "所有需凭证工单都标成未给 —— 受理分支测不到"
