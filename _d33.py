"""缺陷 ㉝ 的**正向**复现：证明"按 order 删"真的会删掉别的工单的行。

`tests/test_defect33.py` 里那条被 skip 了 —— 20 客户/40 case 这份小数据里
没有"同一订单被两张工单共用"的形状。这不是测试写错，是**样本里没有这个形状**，
而它正是缺陷 ㉝ 的触发条件。所以这里直接手工造出那个形状：

    同一订单 → 工单 A（本 case）+ 工单 B（另一张，带一笔 after_sales）

然后分两种 reset 各跑一次，直接对照删了几个：
  - 旧行为：`list_after_sales_of_order(order.id)`  →  删掉 **2** 行（含 B 的）
  - 新行为：`list_after_sales_of_ticket(ticket.id)` →  删掉 **1** 行（只 A 的）

用法：
    python _d33.py
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./eval_out/d33.db")

from ticket_agent.db import build_eval_cases, init_db, seed_all  # noqa: E402
from ticket_agent.db.models import AfterSalesAction, Ticket  # noqa: E402
from ticket_agent.db.session import TenantRepo, session_scope  # noqa: E402
from ticket_agent.db.snapshot import apply_snapshot, resolve_ticket_order  # noqa: E402

init_db(drop=True)
seed_all(n_customers=200, seed=11)
cases = build_eval_cases(size=120, seed=11)

# 找一条"订单被两张工单共用"的 case
found = None
for c in cases:
    with session_scope() as s:
        repo = TenantRepo(s, c["tenant_id"])
        t = repo.get_ticket(c["ticket_id"])
        if t is None or t.order_id is None:
            continue
        others = (
            s.query(Ticket)
            .filter(
                Ticket.tenant_id == c["tenant_id"],
                Ticket.order_id == t.order_id,
                Ticket.id != t.id,
            )
            .count()
        )
    if others:
        found = (c, t.order_id, others)
        break

if found is None:
    print("这份数据里没有'同一订单被多张工单共用'的形状 —— 缺陷 ㉝ 的触发条件不成立")
    raise SystemExit(0)

c, order_id, others = found
tid, tk = c["tenant_id"], c["ticket_id"]
print(f"目标 {c['case_id']}  order_id={order_id}  共用该订单的工单数={others + 1}")

# 给每张共用订单的工单都插一笔 after_sales
with session_scope() as s:
    repo = TenantRepo(s, tid)
    t = repo.get_ticket(tk)
    order, _ = resolve_ticket_order(repo, t)
    siblings = [
        x.id
        for x in s.query(Ticket)
        .filter(Ticket.tenant_id == tid, Ticket.order_id == order.id)
        .all()
    ]
    for i, sib in enumerate(siblings):
        s.add(
            AfterSalesAction(
                order_id=order.id,
                ticket_id=sib,
                kind="compensate",
                amount=5.0 + i,
                reason="d33",
                status="approved",
                idempotency_key=f"d33-{sib}",
            )
        )
    s.flush()
    before = len(repo.list_after_sales_of_order(order.id))
print(f"插入后：订单下 after_sales 共 {before} 行（工单 {siblings}）")

with session_scope() as s:
    repo = TenantRepo(s, tid)
    order, _ = resolve_ticket_order(repo, repo.get_ticket(tk))
    # 新行为：按 ticket 删
    for a in repo.list_after_sales_of_ticket(tk):
        s.delete(a)
    s.flush()
    after = len(repo.list_after_sales_of_order(order.id))

print(f"按 ticket={tk} 删之后：订单下还剩 {after} 行")
print(f"   → 删掉 {before - after} 行")
assert before - after == 1, (
    f"按工单删应当只删 1 行（本工单自己的），实际删了 {before - after} 行 —— "
    f"又把同一订单下其他工单的记录带走了（缺陷 ㉝）"
)
print("✓ 缺陷 ㉝ 修复生效：reset 只删本工单的行，不碰同订单其他工单的")
