"""诊断：为什么 R2 / R3 / R5 / CANCELLED / UNPAID 一条 case 都没有？

`_conditions.py` 已经测出 30 条 case 里只有 5 种条件，7 条规则中有 5 条
**从未被任何 case 触发过**。这个脚本定位机制 —— 是数据造不出来，还是规则短路？

结论（本脚本要打印的）大概率是：R4 的金额阈值在 `RULE_PRECEDENCE` 里排在
R2/R3/R1 之前，而订单金额是 uniform(29, 2500) → P(金额 > 500) ≈ 81%，
所以绝大多数订单还没走到 R2/R3 就被 R4 拦下了。

用法：python _whycoverage.py
"""

from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, "src")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

from ticket_agent.config import settings  # noqa: E402
from ticket_agent.db import init_db, seed_all  # noqa: E402
from ticket_agent.db.models import Customer, Order, Ticket  # noqa: E402
from ticket_agent.db.session import session_scope  # noqa: E402
from ticket_agent.rules import RULE_PRECEDENCE, decide  # noqa: E402
from sqlalchemy import select  # noqa: E402


def main() -> int:
    init_db(drop=True)
    seed_all(n_customers=60, seed=7)

    print(f"RULE_PRECEDENCE = {RULE_PRECEDENCE}")
    print(f"金额 HITL 阈值 = {settings.refund_hitl_threshold_cny} 元")
    print(f"七天期限 = {settings.return_window_days} 天\n")

    with session_scope() as s:
        orders = list(s.scalars(select(Order)))
        print(f"订单总数 {len(orders)}")

        # ① 金额分布 —— R4 吞掉多少
        over = [o for o in orders if o.amount > settings.refund_hitl_threshold_cny]
        print(f"  金额 > {settings.refund_hitl_threshold_cny} 元的订单：{len(over)}/{len(orders)} "
              f"= {len(over)/len(orders):.1%}  ← 这些订单在 R4 就被拦下")

        # ② 类目分布
        cats = Counter(o.category for o in orders)
        print(f"  不退货类目（R2 适用）：" +
              ", ".join(f"{c}={cats.get(c,0)}" for c in ("内衣", "定制", "数字商品")))
        print(f"  生鲜（R6 适用）：{cats.get('生鲜', 0)}")

        # ③ 状态分布
        st = Counter(o.status for o in orders)
        print(f"  状态：{dict(st)}")
        print(f"   → shipped（R3 适用）={st.get('shipped',0)}、"
              f"cancelled（CANCELLED）={st.get('cancelled',0)}、"
              f"pending_paid（UNPAID）={st.get('pending_paid',0)}")

        # ④ R5：已退过款的订单
        retried = [o for o in orders if (o.refund_count or 0) >= 1]
        print(f"  refund_count >= 1（R5 适用）：{len(retried)}")

        # ⑤ 关键：逐条订单跑 decide，看谁真的走到 R2/R3/R5
        #    并统计"如果 R4 不存在"会落到哪 —— 这就是 R4 掩盖效应的直接证明
        reached = Counter()
        would_be = Counter()
        for o in orders:
            cust = s.get(Customer, o.customer_id)
            kw = dict(
                order_status=o.status,
                amount=o.amount,
                categories={o.category},
                signed_at=o.signed_at,
                is_blacklisted=bool(cust.is_blacklisted) if cust else False,
                risk_score=float(cust.risk_score or 0.0) if cust else 0.0,
                refund_count=int(o.refund_count or 0),
            )
            d = decide(**kw)
            reached[d.rule_hits[0] if d.rule_hits else "?"] += 1
            # 假设金额没超阈值（其余不变），看会落到哪 —— 即"R4 掩盖了什么"
            kw2 = dict(kw)
            kw2["amount"] = min(o.amount, settings.refund_hitl_threshold_cny)
            d2 = decide(**kw2)
            would_be[d2.rule_hits[0] if d2.rule_hits else "?"] += 1

        print(f"\n  逐订单判定，**实际**首条命中规则：{dict(reached)}")
        print(f"  逐订单判定，**假设金额不超阈值**时的首条命中：{dict(would_be)}")
        print(f"\n  ★ R4 掩盖效应：去掉 R4 后 R2/R3/R5/R1 才露出水面")

        # ⑥ 工单实际关联的订单（评测集的采样对象）
        tickets = list(s.scalars(select(Ticket)))
        print(f"\n  工单 {len(tickets)} 条；其中关联了订单的 "
              f"{sum(1 for t in tickets if t.order_id)} 条")
        linked_cats = Counter(
            s.get(Order, t.order_id).category
            for t in tickets
            if t.order_id and s.get(Order, t.order_id)
        )
        print(f"  工单关联订单的类目分布：{dict(linked_cats)}")
        nonret = ("内衣", "定制", "数字商品")
        print(f"   → 工单落在 R2 类目上的：{sum(linked_cats.get(c,0) for c in nonret)} 条")
        print(f"   → 工单落在 R6（生鲜）上的：{linked_cats.get('生鲜', 0)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
