"""三个覆盖缺口到底是"设计如此"还是"永久死条"。

背景：235 条 case 全通过后，`EXPECTED_CONDITIONS` 里仍有三条从未出现：
    R10:compensate / R11:price_protect / R13:change_address

同时冒出了 R10:need_clarification / R13:escalate / R13:reject。
这个脚本回答一个问题：**这些诉求能不能产生它们本该产生的动作？**

★ 为什么值得单独写一个脚本：
  "规则写了但永远凑不齐条件"和"这条规则压根没写"在报表上长得一模一样 ——
  都是 0 条 case。区别只在能不能造出一个让它成立的世界。
"""

from __future__ import annotations

import os
import sys

if "reqkind" not in os.environ.get("DATABASE_URL", ""):
    print("拒绝运行：DATABASE_URL 不含 reqkind。")
    raise SystemExit(2)

from ticket_agent.db.corpus import classify_cause, classify_request  # noqa: E402
from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import init_db, session_scope  # noqa: E402
from ticket_agent.rules import decide_for_order  # noqa: E402

print("=" * 72)
print("① 文本 → 诉求：价保/补偿/改址 这三类客户话术能不能被认出来")
print("=" * 72)
samples = [
    ("刚买就降价了，能价保吗", ""),
    ("我买的时候 199，现在 149，退我差价", ""),
    ("这个能保价吗", ""),
    ("商品降价了，申请价保", ""),
    ("东西坏了，赔我 20 元", "补偿方案"),
    ("赔我 15 块钱", "补偿方案"),
    ("太慢了，赔我 30 元", "补偿方案"),
    ("地址写错了，帮我改下地址", "改收货地址"),
    ("我要改收货地址", "改收货地址"),
]
for text, scene in samples:
    kind = classify_request(text, scene)
    cause = classify_cause(text, kind)
    print("   {:34s} scene={:8s} -> kind={:16s} cause={}".format(text, scene or "-", kind, cause))

print()
print("=" * 72)
print("② 造数分布：哪些诉求真的被造出来了")
print("=" * 72)
init_db(drop=True)
with session_scope() as s:
    stats = seed_all(400, 90, 7)
for k in sorted(stats):
    if k.startswith("request_"):
        print("   {:26s} {}".format(k, stats[k]))

print()
print("=" * 72)
print("③ case 里的条件分布（case 是 dict，只有 condition='规则号:动作'）")
print("=" * 72)
cases = build_eval_cases(size=300, seed=7)
from collections import Counter

conds = Counter(c["condition"] for c in cases)
for cond, n in sorted(conds.items()):
    print("   {:26s} {}".format(cond, n))
print("   合计 {} 条 case，{} 种条件".format(len(cases), len(conds)))

print()
print("=" * 72)
print("④ 直接问规则：造一个'能成立的世界'，这三个动作出得来吗")
print("=" * 72)
from datetime import timedelta
from types import SimpleNamespace

from ticket_agent import clock

now = clock.now()
base_order = dict(
    status="signed",
    amount=200.0,
    category="服饰",
    signed_at=now - timedelta(days=2),
    paid_at=now - timedelta(days=6),
    refund_count=0,
    exchange_count=0,
    reship_count=0,
    last_urge_at=None,
    invoice_issued=False,
)
cust_ok = SimpleNamespace(is_blacklisted=False, risk_score=0.0)

probes = [
    ("compensate", "shipping_fee", 20.0, "补偿 20 元运费（有金额）"),
    ("compensate", "shipping_fee", 0.0, "补偿但没给金额"),
    ("compensate", "late", 30.0, "晚到赔 30"),
    ("price_protect", "price_drop", 50.0, "价保 50 元差价"),
    ("price_protect", "", 0.0, "价保但没给金额"),
    ("address_change", "", 0.0, "改地址·已发货"),
]
for kind, cause, amount, desc in probes:
    for status in ("signed", "shipped", "paid"):
        o = SimpleNamespace(**{**base_order, "status": status})
        try:
            d = decide_for_order(o, customer=cust_ok, request_kind=kind, request_cause=cause)
            print(
                "   {:16s} cause={:12s} amount={:6.1f} status={:9s} -> {:20s} {}".format(
                    kind, cause or "-", amount, status, d.action, d.rule_hits
                )
            )
        except Exception as exc:  # noqa: BLE001
            print("   {:16s} status={:9s} -> 异常 {}".format(kind, status, exc))
    print()
