"""为什么 `R10:compensate` / `R11:price_protect` / `R13:change_address` 还是 0 条？

★ 这个脚本只读，不改库。它回答一个具体问题：
  **这三个缺口是"造数里没有这种世界"还是"规则判不出来"？**

  两者的修法完全不同：
    · 世界里没有 → 改造数分布（多造这种订单/工单）
    · 规则判不出 → 改规则或改传参

  在报表上它们长得一模一样（都是 0 条 case），所以必须拆开看。

用法（对着刚跑完 `_reqkindcheck.py` 的那个库）：
    $env:DATABASE_URL="sqlite:///./eval_out/reqkind.db"
    python -X utf8 _gapdiag.py
"""

from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict

DSN = os.environ.get("DATABASE_URL", "")
if "reqkind" not in DSN:
    print("！请把 DATABASE_URL 指到 reqkind.db（本脚本只读，但要防止误连生产库）")
    sys.exit(2)

from sqlalchemy import select  # noqa: E402

from ticket_agent.db.models import Order, Refund, Ticket  # noqa: E402
from ticket_agent.db.session import session_scope  # noqa: E402
from ticket_agent.rules import (  # noqa: E402
    REQUEST_KINDS,
    decide,
    decide_for_order,
    expected_action_for,
)


def main() -> int:
    with session_scope() as s:
        tickets = s.execute(select(Ticket)).scalars().all()
        orders = s.execute(select(Order)).scalars().all()
        refunds = s.execute(select(Refund)).scalars().all()

    print(f"库规模：工单 {len(tickets)} / 订单 {len(orders)} / 退款 {len(refunds)}")

    # ── 1. 诉求分布：三种缺口的诉求到底造出来没有 ──────────────────
    kinds = Counter(t.request_kind or "" for t in tickets)
    print("\n【1】工单诉求分布（request_kind）")
    for k in REQUEST_KINDS:
        n = kinds.get(k, 0)
        mark = "  ★" if k in ("compensate", "price_protect", "address_change") else ""
        print(f"   {k:<16} {n:>4}{mark}")

    # ── 2. 订单状态分布：`paid` 到底造出来没有 ────────────────────
    st = Counter(o.status for o in orders)
    print("\n【2】订单状态分布（status）")
    for k, v in sorted(st.items(), key=lambda kv: -kv[1]):
        print(f"   {k:<14} {v:>4}  ({v / max(len(orders), 1):.1%})")

    # ── 3. 关键交叉表：诉求 × 订单状态 ───────────────────────────
    by_id = {o.id: o for o in orders}
    cross: dict[tuple[str, str], int] = defaultdict(int)
    for t in tickets:
        o = by_id.get(t.order_id)
        cross[(t.request_kind or "", o.status if o else "(无订单)")] += 1
    print("\n【3】交叉表：诉求 × 订单状态（只看三种缺口 + 有订单的）")
    for kind in ("compensate", "price_protect", "address_change"):
        row = {k[1]: v for k, v in cross.items() if k[0] == kind}
        total = sum(row.values())
        print(f"   {kind:<16} 共 {total:>3} 条：{dict(sorted(row.items(), key=lambda kv: -kv[1]))}")

    # ── 4. 逐条问规则：这些工单**应该**得到什么动作 ────────────────
    #    ★ 这一步是关键：如果规则说"need_clarification"，
    #      那就是参数没传到；如果规则说"compensate"，
    #      那就是 case 采样没抽到（概率问题，加大样本即可）。
    print("\n【4】逐条问规则（用与 golden 完全相同的传参）")
    verdicts: dict[str, Counter[str]] = {k: Counter() for k in
                                         ("compensate", "price_protect", "address_change")}
    n_with_price = 0
    n_with_amount = 0
    for t in tickets:
        kind = t.request_kind or ""
        if kind not in verdicts:
            continue
        o = by_id.get(t.order_id)
        if o is None:
            verdicts[kind]["(无订单)"] += 1
            continue
        if o.current_price is not None:
            n_with_price += 1
        if (t.request_amount or 0) > 0:
            n_with_amount += 1
        d = decide_for_order(
            o,
            refund_count=int(o.refund_count or 0),
            request_kind=kind,
            request_cause=t.request_cause or "",
            request_amount=(t.request_amount if (t.request_amount or 0) > 0 else None),
            current_price=o.current_price,
            exchange_count=int(o.exchange_count or 0),
            reship_count=int(o.reship_count or 0),
            last_urge_at=o.last_urge_at,
            invoice_issued=bool(o.invoice_issued),
        )
        verdicts[kind][d.action] += 1

    for kind, c in verdicts.items():
        exp = expected_action_for(kind)
        print(f"   {kind:<16} 期望终态动作 {exp:<18} 实测：{dict(c)}")

    print(f"\n   （其中带 current_price 的订单 {n_with_price} 个、")
    print(f"     带 request_amount>0 的缺口工单 {n_with_amount} 条）")

    # ── 5. 直接手工造出"理想世界"，验证规则本身通不通 ─────────────
    #    如果连手工造都判不出来，那是规则的 bug，不是造数的问题。
    print("\n【5】手工造理想世界（排除造数因素，验证规则本身）")
    from types import SimpleNamespace

    def _o(**kw):
        base = dict(
            status="paid", amount=120.0, category="服饰",
            signed_at=None, paid_at=None, refund_count=0,
            exchange_count=0, reship_count=0, last_urge_at=None,
            invoice_issued=False, current_price=None, id=1,
        )
        base.update(kw)
        return SimpleNamespace(**base)

    probes = [
        ("compensate 带金额", dict(request_kind="compensate", request_amount=30.0,
                                request_cause="quality"), _o()),
        ("compensate 无金额", dict(request_kind="compensate", request_amount=None,
                                request_cause="quality"), _o()),
        ("price_protect 有现价", dict(request_kind="price_protect",
                                   request_amount=None, current_price=99.0), _o()),
        ("price_protect 无现价", dict(request_kind="price_protect",
                                   request_amount=None, current_price=None), _o()),
        ("address_change paid", dict(request_kind="address_change"), _o(status="paid")),
        ("address_change pending_paid", dict(request_kind="address_change"),
         _o(status="pending_paid")),
        ("address_change shipped", dict(request_kind="address_change"), _o(status="shipped")),
    ]
    for label, kw, order in probes:
        kw.setdefault("request_cause", "")
        d = decide_for_order(order, **kw)
        print(f"   {label:<28} → {d.action:<18} {d.reasons[:1]}")

    # ── 6. 修完还 0 条的话，最可能的那个原因 ──────────────────────
    print("\n【6】结论")
    gaps = [k for k, c in verdicts.items()
            if c.get(expected_action_for(k), 0) == 0
            and c.get("(无订单)", 0) < sum(c.values())]
    if not gaps:
        print("   规则层面三种缺口都能判出来 → 剩下的 0 条是**采样概率**问题，")
        print("   加大 --size 或改 seed 就能出。若不放心，可在 _stratified_sample 里保底。")
    else:
        print(f"   {gaps} 仍然判不出来 → 是参数/规则问题，需要改代码，不是加大样本能解决的。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
