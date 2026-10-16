"""诊断：无关联订单的工单（L3）到底该怎么处置？

怀疑 golden 的 `order is None → 保持 open` 分支漏掉了一种情况：
工单虽然没关联订单，但客户名下**恰好只有一笔**订单 —— 此时根本没有"歧义"需要澄清，
正确行为是直接处置那笔订单。若真如此，这些 case 无论 agent 怎么做都必失败。
"""

from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, r"C:\Users\Administrator\Documents\deepseek-harness\default-workspace\ticket-agent\src")

from sqlalchemy import select

from ticket_agent.db.models import Ticket
from ticket_agent.db.session import TenantRepo, init_db, session_scope

init_db(drop=False)

with session_scope() as s:
    tickets = list(s.scalars(select(Ticket).order_by(Ticket.id)))
    unlinked = [t for t in tickets if not t.order_id]

    print(f"总工单 {len(tickets)}，无关联订单 {len(unlinked)}")
    dist = Counter()
    detail = []
    for t in unlinked:
        repo = TenantRepo(s, t.tenant_id)
        orders = repo.list_orders_of_customer(t.customer_id)
        n = len(orders)
        dist[n] += 1
        detail.append((t.id, t.tenant_id, t.customer_id, n, t.subject[:30]))

    print("\n无关联订单工单的『客户名下单数』分布：")
    for k in sorted(dist):
        label = {0: "0 笔 → 无从处置", 1: "1 笔 → 无歧义，应直接处置那笔", 2: "多笔 → 需反问澄清"}.get(k, "3+ 笔 → 需反问澄清")
        print(f"  {k} 笔：{dist[k]} 条   {label}")

    print("\n明细（前 25 条）：")
    for tid, ten, cid, n, subj in detail[:25]:
        print(f"  工单{tid:>4} {ten:<7} 客户{cid:>4} 候选{n} 笔  {subj}")

    print("\n★ 结论：" f"其中 {sum(v for k, v in dist.items() if k == 1)} 条 case 的 golden 要求『保持 open』，"
          "但客户名下只有一笔订单 —— 处置它是正确行为。这些 case 无法通过。")
