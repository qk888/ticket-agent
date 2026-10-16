"""诊断：price_dropped 池子到底有多大、为什么 R11 还是 0。

分别回答：
  ① 订单里满足 R11 全部四个条件的有几笔（池子大小）
  ② price_protect 工单最终关联到的订单，是不是从池子里选的
  ③ 如果池子够大但仍没选中，问题在抽样分支；如果池子就是空的，问题在造数分布
"""
from __future__ import annotations

import collections
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

os.environ["DATABASE_URL"] = f"sqlite:///{ROOT / 'eval_out' / 'ppdiag3.db'}"

from sqlalchemy import select  # noqa: E402

from ticket_agent import clock, rules  # noqa: E402
from ticket_agent.config import settings  # noqa: E402
from ticket_agent.db import seed as seed_mod  # noqa: E402
from ticket_agent.db.models import Order, Ticket  # noqa: E402
from ticket_agent.db.session import init_db, session_scope  # noqa: E402
from ticket_agent.db.seed import _as_aware  # noqa: E402

init_db(drop=True)
seed_mod.seed_all(n_customers=400, seed=11)

with session_scope() as s:
    orders = list(s.scalars(select(Order)))
    tickets = list(s.scalars(select(Ticket)))
    by_id = {o.id: o for o in orders}

    now = clock.now()
    has_cp = [o for o in orders if o.current_price is not None]
    dropped = [o for o in has_cp if float(o.current_price) < float(o.amount)]
    paid_ok = [o for o in dropped if o.paid_at is not None]
    in_window = [
        o for o in paid_ok
        if (now - _as_aware(o.paid_at)).days <= settings.price_protect_window_days
    ]
    status_ok = [o for o in in_window if o.status not in ("pending_paid", "cancelled")]

    print(f"订单 {len(orders)} 笔")
    print(f"  有现价            {len(has_cp)}")
    print(f"  现价 < 下单金额   {len(dropped)}")
    print(f"  有支付时间        {len(paid_ok)}")
    print(f"  在 15 天价保期内  {len(in_window)}")
    print(f"  状态可用          {len(status_ok)}   ← price_dropped 池子")

    print(f"\n价保期内订单的 paid_at 距今天数分布：")
    days = sorted((now - _as_aware(o.paid_at)).days for o in in_window)
    print(f"   {days[:25]}{' …' if len(days) > 25 else ''}")

    pp = [t for t in tickets if t.request_kind == "price_protect"]
    print(f"\nprice_protect 工单 {len(pp)} 条，各自关联的订单是否在池子里：")
    pool_ids = {o.id for o in status_ok}
    hit = 0
    for t in pp:
        o = by_id.get(t.order_id) if t.order_id else None
        ok = "★在池" if (o and o.id in pool_ids) else "  不在"
        d = rules.decide_for_order(o, request_kind="price_protect") if o else None
        if o and o.id in pool_ids:
            hit += 1
        print(
            f"   t#{t.id} order={t.order_id} {ok} "
            f"→ {d.action if d else '(无订单)'}"
        )
    print(f"   → 落在池子里的：{hit}/{len(pp)}")
