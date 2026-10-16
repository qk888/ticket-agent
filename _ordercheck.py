"""诊断：`ambiguous` case 里 guesser 的违规抖动，是不是订单排序不稳导致的？

线索：`db/session.py` 的 `list_orders_of_customer` 用 `.order_by(Order.created_at.desc())`，
而 `Order.created_at` 的默认值是 `models.utcnow()` —— **真实墙上时钟，未接线到 clock 锚点**。
guesser 在 `ambiguous` 分支取 `orders[0]`，排序一变选中的订单就变。

本脚本直接测量：
  ① 同一客户名下多笔订单，两次造数后 `list_orders_of_customer` 返回的 id 序列是否一致
  ② created_at 是否存在**并列**（并列时 SQLite 的 ORDER BY 不保证顺序）
  ③ guesser 实际选中的订单是不是 `orders[0]`，它与 condition 的关系
"""

import sys
from collections import Counter

sys.path.insert(0, "src")

from ticket_agent import clock  # noqa: E402
from ticket_agent.db import Ticket, build_eval_cases, init_db, seed_all  # noqa: E402
from ticket_agent.db.session import TenantRepo, session_scope  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def build():
    init_db(drop=True)
    seed_all(n_customers=60, seed=7)
    with session_scope() as s:
        # 每个客户名下订单的 return 顺序 + created_at 原始值
        repo = TenantRepo(s, "acme")
        out = {}
        tickets = {}
        for t in s.scalars(Ticket.__table__.select()):  # noqa: SIM118
            pass
        for row in s.execute(Ticket.__table__.select()).all():
            tickets[f"{row.tenant_id}-t{row.id}"] = row.customer_id
        for cid in sorted(set(tickets.values())):
            orders = repo.list_orders_of_customer(cid)
            out[cid] = ([(o.id, str(o.created_at)) for o in orders],)
        rows = [(cid, out[cid][0]) for cid in out]
    cases = build_eval_cases(size=30, seed=7)
    return rows, cases, tickets, str(clock.get_anchor())


runs = [build() for _ in range(3)]
for i, (rows, cases, tickets, anchor) in enumerate(runs):
    print(f"第 {i+1} 次 anchor={anchor}  case 数={len(cases)}")

print("=" * 84)
print("① 同一客户订单序列比对（第 1 次为基准）：")
base_rows = dict(runs[0][0])
for idx in (1, 2):
    other = dict(runs[idx][0])
    changed = []
    for cid in sorted(set(base_rows) | set(other)):
        a = [oid for oid, _ in base_rows.get(cid, [])]
        b = [oid for oid, _ in other.get(cid, [])]
        if a != b:
            changed.append((cid, a, b))
    print(f"  第 1 vs {idx+1}: {len(changed)} 个客户的订单顺序不同")
    for cid, a, b in changed[:5]:
        print(f"     customer {cid}: {a} → {b}")

print("=" * 84)
print("② created_at 并列情况（并列时 ORDER BY 不保证顺序）：")
for i, (rows, _, _, _) in enumerate(runs):
    tie_customers = 0
    tie_rows = 0
    dup_ts = 0
    for cid, order_pairs in rows:
        ts = [t for _, t in order_pairs]
        c = Counter(ts)
        if any(v > 1 for v in c.values()):
            tie_customers += 1
            tie_rows += sum(v for v in c.values() if v > 1)
        dup_ts += sum(v - 1 for v in c.values())
    print(f"  第 {i+1} 次: 有并列的客户 {tie_customers} 个 / 涉及 {tie_rows} 行；"
          f"全库重复时间戳计数 {dup_ts}")

print("=" * 84)
print("③ ambiguous case 与订单序列的关系：")
tickets = runs[0][2]
for cases, rows, _, _ in [runs[0], runs[1], runs[2]]:
    amb = [c["case_id"] for c in cases if c.get("condition") == "ambiguous"]
    print(f"  ambiguous case: {amb}")
    for cid_ in amb:
        cust = tickets.get(cid_)
        seq = [oid for oid, _ in dict(runs[0][0]).get(cust, [])]
        print(f"     {cid_} → customer {cust} 的 orders[0] = "
              f"{seq[0] if seq else None}（共 {len(seq)} 笔）")

print("=" * 84)
print("④ 各次运行里 ambiguous case 的 orders[0] 是否不同：")
for cid_ in [c["case_id"] for c in runs[0][1] if c.get("condition") == "ambiguous"]:
    cust = tickets.get(cid_)
    seqs = []
    for _, rows, _, _ in runs:
        seq = [oid for oid, _ in dict(rows).get(cust, [])]
        seqs.append(seq[0] if seq else None)
    flag = "✗ 不同" if len(set(seqs)) > 1 else "✓ 相同"
    print(f"   {cid_}: orders[0] 依次 = {seqs}  {flag}")
print("=" * 84)
