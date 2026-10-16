"""#60 凭证能力：端到端验收。

验的不是"模块能 import"，而是这条链路真的通：

  A. 规则层：需要凭证 + 没给 → need_clarification（政策第一次真的被检查）
  B. 规则层：需要凭证 + 已给 → 正常受理
  C. 规则层：不需要凭证的原因（如 size 尺码不符）→ 不受影响
  D. 工具层：mark_evidence_received 能翻转工单字段，且重复标记是幂等的
  E. 工具层：get_ticket 把 evidence_provided 暴露给 agent（任务输入可见）
  F. 造数层：seed 造出的工单里，需要凭证的有给有没给（两个分支都能测到）
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from ticket_agent import rules
from ticket_agent.db.models import Ticket
from ticket_agent.db.seed import build_eval_cases, seed_all
from ticket_agent.tools import registry

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✓' if cond else '✗'} {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _order(**kw):
    base = dict(
        status="signed",
        amount=200.0,
        category="clothing",
        signed_at=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc),
        paid_at=dt.datetime(2026, 9, 28, tzinfo=dt.timezone.utc),
        current_price=None,
        exchange_count=0,
        reship_count=0,
        invoice_issued=False,
        last_urge_at=None,
        refund_count=0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def main() -> None:
    # ── A/B/C：规则层 ────────────────────────────────────────
    print("── A/B/C 规则层 ──")
    for cause, kind in (("quality", "exchange"), ("damaged", "reship"), ("quality", "compensate")):
        d = rules.decide(
            order_status="signed",
            amount=200.0,
            categories={"clothing"},
            signed_at=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc),
            request_kind=kind,
            request_cause=cause,
            request_amount=20.0 if kind == "compensate" else None,
            evidence_provided=False,
        )
        check(
            f"A {kind}/{cause} 未给凭证 → need_clarification",
            d.action == "need_clarification",
            f"实际 {d.action}",
        )
        d2 = rules.decide(
            order_status="signed",
            amount=200.0,
            categories={"clothing"},
            signed_at=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc),
            request_kind=kind,
            request_cause=cause,
            request_amount=20.0 if kind == "compensate" else None,
            evidence_provided=True,
        )
        check(
            f"B {kind}/{cause} 已给凭证 → 正常受理",
            d2.action == rules.expected_action_for(kind),
            f"实际 {d2.action}",
        )

    # C：不需要凭证的原因不受影响（size 不在 PROOF_CAUSES）
    d3 = rules.decide(
        order_status="signed",
        amount=200.0,
        categories={"clothing"},
        signed_at=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc),
        request_kind="exchange",
        request_cause="size",
        evidence_provided=False,
    )
    check(
        "C exchange/size（不需凭证）→ 仍受理，不受新守卫影响",
        d3.action == "exchange",
        f"实际 {d3.action}",
    )

    # ── D/E：工具层（用独立库）───────────────────────────────
    print("\n── D/E 工具层 ──")
    import os

    os.environ["DATABASE_URL"] = "sqlite:///./eval_out/_proofcheck.db"
    # ★ engine 是模块级缓存，改环境变量后必须清掉，否则仍连旧库
    from ticket_agent.db import session as db_session

    db_session._engine = None
    db_session._SessionFactory = None
    from ticket_agent.db.session import init_db, session_scope

    init_db(drop=True)
    # 12 个客户太少，需要凭证的原因（quality/damaged/wrong_item/lost 合计约 7%）
    # 抽不到稳定。用 120 个客户 —— 这也是真实评测用的量级。
    seed_all(n_customers=120, seed=5)

    # 找一条「需凭证但未给」的工单
    target = None
    with session_scope() as s:
        for t in s.query(Ticket).all():
            if t.request_cause in rules.PROOF_CAUSES and not t.evidence_provided:
                target = t
                break
    check("D 找到一条「需凭证但未给」的工单", target is not None)
    if target is None:
        print("\n无法继续工具层验收")
    else:
        from ticket_agent.tools.registry import (
            get_ticket,
            mark_evidence_received,
            set_tool_context,
        )

        tenant = target.tenant_id
        set_tool_context(tenant, target.id, "resolver", "proofcheck-1")
        r = mark_evidence_received("photo", "客户发了破损照片")
        check("D mark_evidence_received 返回 ok", r.get("ok") is True, str(r))
        with session_scope() as s:
            from ticket_agent.db.session import TenantRepo

            repo = TenantRepo(s, tenant)
            t2 = repo.get_ticket(target.id)
            check("D 工单字段已翻转", bool(t2.evidence_provided) is True)
        r2 = mark_evidence_received("photo")
        check("D 重复标记幂等（already=True）", r2.get("already") is True, str(r2))
        g = get_ticket()
        check("E get_ticket 暴露 evidence_provided", g.get("evidence_provided") is True, str(g)[:120])

    # ── F：造数层两个分支都在 ────────────────────────────────
    print("\n── F 造数层 ──")
    with session_scope() as s:
        rows = s.query(Ticket).all()
        need = [t for t in rows if t.request_cause in rules.PROOF_CAUSES]
        given = [t for t in need if t.evidence_provided]
        print(f"   需凭证工单 {len(need)} 条，其中已给 {len(given)} 条")
        check("F 需凭证的工单存在", len(need) > 0)
        check("F 需凭证的工单里两个分支都出现", 0 < len(given) < len(need))

    print("\n" + "═" * 50)
    if FAILS:
        print(f"★ {len(FAILS)} 项未通过：{FAILS}")
        raise SystemExit(1)
    print("✓ #60 凭证能力端到端全部通过")


if __name__ == "__main__":
    main()
