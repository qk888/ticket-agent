"""数一次 60 客户 / seed=7 这份数据（测试用的那套）的条件分布。

目的：`test_service_api.py::test_human_approval_cannot_bypass_policy_guardrail`
依赖这份数据里**恰好**有 R6:escalate / R5:escalate / R4:escalate 各一条。
造数逻辑一改，这个"恰好"就没了 —— 说明测试依赖的是**运气**，
而它自己的报错文案也承认了这一点（"这次 seed 没造出…测试失去意义"）。
本脚本量清楚：R6 是彻底消失了，还是被 `_stratified_sample` 保住了？
"""
from __future__ import annotations

import collections
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

os.environ["DATABASE_URL"] = f"sqlite:///{ROOT / 'eval_out' / 'cond60.db'}"

from ticket_agent.db.models import Order, Ticket  # noqa: E402
from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import init_db, session_scope  # noqa: E402
from sqlalchemy import select  # noqa: E402

init_db(drop=True)
seed_all(n_customers=60, seed=7)
cases = build_eval_cases(size=40, seed=7)

print(f"case 总数 {len(cases)}")
by_cond = collections.Counter(c["condition"] for c in cases)
print("\n条件分布：")
for k, n in sorted(by_cond.items()):
    print(f"   {k:<24} {n}")

for want in ("R6:escalate", "R5:escalate", "R4:escalate", "R11:price_protect"):
    got = by_cond.get(want, 0)
    print(f"\n{want}: {got} 条 {'✓' if got else '✗ 缺'}")

# 生鲜订单到底有没有、有多少
with session_scope() as s:
    tickets = list(s.scalars(select(Ticket)))
    orders = {o.id: o for o in s.scalars(select(Order))}
    fresh = [
        (t.id, t.request_kind, orders[t.order_id].category if t.order_id in orders else None)
        for t in tickets
        if t.order_id in orders
    ]
    n_fresh_cat = sum(1 for _, _, c in fresh if c and "生鲜" in str(c))
    print(f"\n全部工单 {len(tickets)} 条，关联到生鲜类订单的：{n_fresh_cat}")
    for tid, rk, cat in fresh:
        if cat and "生鲜" in str(cat):
            print(f"   ticket#{tid} kind={rk} category={cat}")
