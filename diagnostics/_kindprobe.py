"""探：诉求造出来之后，golden 为什么还是判不到 R11 / R10。

分两段：
  ① 直接问规则 —— price_protect / compensate 在各订单状态下分别判什么。
     （R11 的守卫是 `current_price is None → need_clarification`，
       而只有约 30% 订单写了 current_price，这是首要嫌疑）
  ② 真造一次数（强制 100% 指定 price_protect），数 DB 里
     `request_kind='price_protect'` 的工单有几条、它们的订单有没有现价。
"""
from __future__ import annotations

import collections
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from ticket_agent import rules  # noqa: E402

NOW = datetime.now(timezone.utc)

print("=" * 78)
print("① 直接问规则：price_protect（R11）")
print("=" * 78)


def order_stub(**kw):
    base = dict(
        status="signed",
        amount=100.0,
        signed_at=NOW - timedelta(days=2),
        current_price=None,
        refund_count=0,
        exchange_count=0,
        reship_count=0,
        last_urge_at=None,
        invoice_issued=False,
        is_blacklisted=False,
        risk_score=0.0,
        category="日用",
    )
    base.update(kw)
    return SimpleNamespace(**base)


for cp in (None, 80.0, 100.0, 120.0):
    d = rules.decide_for_order(order_stub(current_price=cp), request_kind="price_protect")
    print(f"  current_price={str(cp):<7} → {d.action:<18} {d.reasons[0][:52] if d.reasons else ""}")

print()
print("=" * 78)
print("①b 直接问规则：compensate（R10）")
print("=" * 78)
for amt in (None, 0.0, 20.0, 60.0, 500.0):
    d = rules.decide_for_order(
        order_stub(), request_kind="compensate", request_amount=amt
    )
    print(f"  request_amount={str(amt):<7} → {d.action:<18} {d.reasons[0][:52] if d.reasons else ""}")

print()
print("=" * 78)
print("② 真造数：强制 100% price_protect，看库里到底存了什么")
print("=" * 78)

os.environ["DATABASE_URL"] = f"sqlite:///{ROOT / 'eval_out' / 'kprobe.db'}"
from ticket_agent.db import seed as seed_mod  # noqa: E402
from ticket_agent.db.session import init_db, session_scope  # noqa: E402
from ticket_agent.db.models import Order, Ticket  # noqa: E402
from sqlalchemy import select  # noqa: E402

seed_mod._KIND_FORCE_SHARE = 1.0
seed_mod._KIND_FORCE_CANDIDATES = ("price_protect",)
seed_mod._KIND_RNG.seed(1)
init_db(drop=True)
seed_mod.seed_all(n_customers=120, seed=7)
seed_mod._KIND_FORCE_SHARE = 0.2
seed_mod._KIND_FORCE_CANDIDATES = (
    "compensate", "price_protect", "exchange", "address_change", "invoice", "reship",
)

with session_scope() as s:
    tickets = list(s.scalars(select(Ticket)))
    dist = collections.Counter(t.request_kind for t in tickets)
    print(f"工单总数 {len(tickets)}，诉求分布：")
    for k, n in dist.most_common():
        print(f"   {k:<18} {n:>5}")

    pp = [t for t in tickets if t.request_kind == "price_protect"]
    print(f"\nprice_protect 工单 {len(pp)} 条，逐条看订单现价：")
    has_cp = 0
    for t in pp[:12]:
        o = s.get(Order, t.order_id) if t.order_id else None
        cp = getattr(o, "current_price", None) if o else None
        if cp is not None:
            has_cp += 1
        print(
            f"   ticket#{t.id} order={t.order_id} "
            f"status={getattr(o, 'status', None)} amount={getattr(o, 'amount', None)} "
            f"current_price={cp}  金额={t.request_amount}"
        )
    print(f"   → 其中订单有现价的：{has_cp}/{len(pp)}")

with session_scope() as s:
    orders = list(s.scalars(select(Order)))
    n_cp = sum(1 for o in orders if o.current_price is not None)
    print(f"\n全部订单 {len(orders)} 笔，有现价的 {n_cp} 笔（{n_cp / max(len(orders), 1):.1%}）")

