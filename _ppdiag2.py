"""诊断：price_protect 工单造出来了，为什么 golden 判不到 R11。

R11 的成功路径要同时满足四个条件（rules.py:616-640）：
  1. order_status 不是 pending_paid/cancelled
  2. `current_price is not None`          ← 只有约 30% 订单有现价
  3. `current_price < amount`（确实降价）  ← 造数保证：amount * uniform(0.70,0.95)
  4. `paid_at` 存在且不超过价保期限        ← signed/shipped/paid 才有

所以任一条不成立就落到 reject / escalate / need_clarification。
本脚本把**每个条件的通过数**数出来，指出卡在哪一层。
"""
from __future__ import annotations

import collections
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

os.environ["DATABASE_URL"] = f"sqlite:///{ROOT / 'eval_out' / 'ppdiag2.db'}"

from sqlalchemy import select  # noqa: E402

from ticket_agent import rules
from ticket_agent.config import settings  # noqa: E402
from ticket_agent.db import seed as seed_mod  # noqa: E402
from ticket_agent.db.models import Order, Ticket  # noqa: E402
from ticket_agent.db.session import init_db, session_scope  # noqa: E402

init_db(drop=True)
seed_mod.seed_all(n_customers=400, seed=11)

with session_scope() as s:
    tickets = list(s.scalars(select(Ticket)))
    orders = {o.id: o for o in s.scalars(select(Order))}

    dist = collections.Counter(t.request_kind for t in tickets)
    print(f"工单 {len(tickets)} 条，诉求分布：")
    for k, n in dist.most_common():
        print(f"   {k:<18} {n:>5}")

    pp = [t for t in tickets if t.request_kind == "price_protect"]
    print(f"\n★ price_protect 工单 {len(pp)} 条")

    c1 = c2 = c3 = c4 = 0
    verdicts = collections.Counter()
    print("\n逐条：")
    for t in pp:
        o = orders.get(t.order_id) if t.order_id else None
        if o is None:
            verdicts["(无订单)"] += 1
            print(f"   ticket#{t.id}  无关联订单")
            continue
        if o.status not in ("pending_paid", "cancelled"):
            c1 += 1
        if o.current_price is not None:
            c2 += 1
        if o.current_price is not None and o.current_price < o.amount:
            c3 += 1
        if o.paid_at is not None:
            c4 += 1
        d = rules.decide_for_order(
            o,
            request_kind="price_protect",
            request_cause=t.request_cause,
        )
        verdicts[d.action] += 1
        print(
            f"   ticket#{t.id} status={o.status:<12} amount={o.amount:>8.2f} "
            f"cur={str(o.current_price):>9} paid_at={'有' if o.paid_at else '无':<2} "
            f"→ {d.action:<18} {d.reasons[0][:40] if d.reasons else ''}"
        )

    print(f"\n条件通过数（分母 {len(pp)}）：")
    print(f"   1 状态可用          {c1}")
    print(f"   2 有现价            {c2}")
    print(f"   3 确实降价          {c3}")
    print(f"   4 有支付时间        {c4}")
    print(f"\n规则判定分布：{dict(verdicts)}")

    # 全部订单里有多少有现价 —— 这是 R11 的池子上限
    n_cp = sum(1 for o in orders.values() if o.current_price is not None)
    print(f"\n全部订单 {len(orders)} 笔，有现价 {n_cp} 笔（{n_cp / max(len(orders), 1):.1%}）")
    print(f"价保期限 price_protect_window_days = {settings.price_protect_window_days}")

