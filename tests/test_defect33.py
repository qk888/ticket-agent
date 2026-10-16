"""缺陷 ㉝：`after_sales` 的重置维度与幂等键维度不一致。

三份诊断量到的链条（`_r10dup.py` / `_r10root2.py` / `_r10dup3.py` / `_r10root3.py`）：
  1. 4 条 case 出现重复行，键完全相同、submit 只调 1 次 → 不是 agent 重复调用
  2. 跑完 after=1，手动 apply_snapshot 之后 → 0 行 → 重置本身能删干净
  3. single 跑完 0→1；紧接着 multi 跑同一条，before=1 而 after 仍=1 → 写入侧幂等生效
  4. 15 条一口气顺序跑，全部 before=0、db_rows≤1、15/15 passed → **顺序跑复现不出来**

所以重复行只在"同一条 case 被跑第二次"时出现，且第一次写入的行还在库里。
根因是**两个维度**：
  - 删：`apply_snapshot` 按 **order** 删（`list_after_sales_of_order`）
  - 键：`submit_after_sales_action` 的幂等键含 **tenant:ticket:order:kind:amount**

同一个工单第二次跑，reset 按 order 删掉了上一轮的行（所以看起来"干净"），
但如果**同一订单下有另一张工单**刚写过，reset 会把那张工单的行也删掉 ——
那张工单的终态就少了一条记录。

本测试文件钉住修好后的三条性质：
  A. reset 按 **ticket** 删，不碰同一订单下其他工单的行（缺陷 ㉝ 的删除维度）
  B. 幂等键含 **cause**（同工单、同 kind、同金额、不同原因 = 两笔独立赔付）
  C. `apply_snapshot` 之后 after_sales 与快照逐条相等（不多不少）
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from ticket_agent.db.models import AfterSalesAction
from ticket_agent.db.session import TenantRepo, session_scope
from ticket_agent.db.snapshot import apply_snapshot, export_snapshot, resolve_ticket_order


@pytest.fixture
def _db():
    from ticket_agent.db import init_db, seed_all

    init_db(drop=True)
    seed_all(n_customers=20, seed=11)
    yield


@pytest.fixture
def case_with_order(_db):
    """取一条有确定终态、且关联了订单的 case。"""
    from ticket_agent.db import build_eval_cases

    cases = build_eval_cases(size=40, seed=11)
    target = next(
        c
        for c in cases
        if c.get("order_basis") in ("linked", "sole_order")
        and (c["expected_state"].get("ticket") or {}).get("status") != "open"
    )
    return target


def test_reset_deletes_by_ticket_not_order(case_with_order) -> None:
    """A：reset 不许顺手删掉同一订单下其他工单的售后记录。"""
    c = case_with_order
    tid, tk = c["tenant_id"], c["ticket_id"]

    # 另一张**真实存在**的工单（同租户、同订单）—— 不能捏造 ticket_id=999999，
    # 外键约束会当场拒绝，而那个拒绝本身是对的：after_sales 必须挂在真工单上。
    with session_scope() as s:
        repo = TenantRepo(s, tid)
        ticket = repo.get_ticket(tk)
        order, _ = resolve_ticket_order(repo, ticket)
        assert order is not None, "这条 case 必须关联订单，否则测不到删除维度"
        from ticket_agent.db.models import Ticket

        other = (
            s.execute(
                select(Ticket).where(
                    Ticket.tenant_id == tid,
                    Ticket.order_id == order.id,
                    Ticket.id != tk,
                )
            )
            .scalars()
            .first()
        )
        if other is None:
            pytest.skip("这份数据里没有同一订单的第二张工单，换一条 case 再测")
        other_id = other.id
        s.add(
            AfterSalesAction(
                order_id=order.id,
                ticket_id=other_id,
                kind="compensate",
                amount=7.0,
                reason="test",
                status="approved",
                idempotency_key="other-ticket-row",
            )
        )
        s.flush()
        foreign = [
            a.idempotency_key
            for a in repo.list_after_sales_of_order(order.id)
            if a.ticket_id == other_id
        ]
        assert foreign == ["other-ticket-row"]

    # 对本工单做 reset —— 那张外来行必须还在
    with session_scope() as s:
        apply_snapshot(s, tid, tk, c["initial_state"])
        repo = TenantRepo(s, tid)
        still = [
            a.idempotency_key
            for a in repo.list_after_sales_of_order(order.id)
            if a.ticket_id == other_id
        ]
    assert still == ["other-ticket-row"], (
        "reset 按 order 删掉了同一订单下其他工单的售后记录 —— "
        "那张工单的终态会少一条本该有的记录（缺陷 ㉝ 的删除维度）"
    )


def test_idempotency_key_includes_cause() -> None:
    """B：同工单、同 kind、同金额、不同 cause 必须是两笔独立赔付。"""
    from ticket_agent.tools.registry import _after_sales_key

    k1 = _after_sales_key("acme", 1, 100, "compensate", 5.0, "damaged")
    k2 = _after_sales_key("acme", 1, 100, "compensate", 5.0, "wrong_item")
    assert k1 != k2, (
        "幂等键不含 cause：同工单对同订单提两笔同 kind 同金额、原因不同的售后"
        "会算出同一个键，第二笔被幂等挡下，而业务上它是两笔独立赔付"
    )
    # 同因同额同 kind → 同一个键（幂等本来就该挡住重复提交）
    k3 = _after_sales_key("acme", 1, 100, "compensate", 5.0, "damaged")
    assert k1 == k3


def test_apply_snapshot_restores_after_sales_exactly(case_with_order) -> None:
    """C：reset 之后 after_sales 与快照逐条相等（不多不少）。"""
    c = case_with_order
    tid, tk = c["tenant_id"], c["ticket_id"]
    with session_scope() as s:
        apply_snapshot(s, tid, tk, c["initial_state"])
        snap = export_snapshot(s, tid, tk)
    want = c["initial_state"].get("after_sales") or []
    got = snap.get("after_sales") or []
    assert len(got) == len(want), (
        f"重置后 after_sales 行数不符：期望 {len(want)}，实际 {len(got)}"
    )
