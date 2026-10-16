"""核对 `_compatprobe.py` 宽尺子 A 类残留样本的**性质**。

宽尺子（独立于生产判据的第二把尺子）在修正否定语境与 C 类判据后，A 类还剩
约 2.4%。这个脚本回答的是：**剩下的到底是真矛盾，还是宽尺子自身的误判？**

判据分三档打印，让数字可被读懂：
- `req=True`  正文点了动作集内可执行的诉求（退款/退货/拦截拒收）—— 系统有得办
- `req=False` 正文只描述状态、没点动作 —— 系统办不了，正解多半是转人工/澄清
"""

from __future__ import annotations

import re
import sys
from collections import Counter

sys.path.insert(0, "src")

from ticket_agent.db import corpus, seed  # noqa: E402

IN_TRANSIT_RE = re.compile(
    r"还没收到|没有收到|没收到|一直没|什么时候能到|什么时候到|多久能到|什么时候发货|"
    r"怎么还没|催|加急|物流|运单|快递单号|显示已签收|在途|派送|揽件|"
    r"还没发货|没发货|发货了没|发了吗|什么时候能收到"
)
RECEIVED_RE = re.compile(
    r"收到|到了|到手|打开|拆开|拆了|里面|用了|吃了|喝了|试了|穿上|"
    r"发来|寄来|送来|发过来|寄过来|给我发的是|包装|签收|到货|"
    r"漏发|少发|发错|碎了|坏了|破损|变质"
)

# ★ 真否定/未完成的语境。宽尺子收的 `签收`/`收到`/`到了` 在中文里大量出现在
#   这些语境中，而它们表示**货没到手**，与"已到手"相反。
#   注意 `还没收到**吗**` 是疑问，语气上像在确认，实质仍是"我没收到"。
NEGATED_RE = re.compile(
    r"不签收|没签收|未签收|拒收|没收到|未收到|还没收到|没有收到|一直没收到|"
    r"没到|未到|还没到|没有到|不知.*到|不会.*收到|没发货|还没发货"
)


def main() -> None:
    cases = seed.build_eval_cases(size=605, seed=7)
    print(f"case 数: {len(cases)}\n")

    buckets: Counter[str] = Counter()
    rows: dict[str, list[str]] = {}

    for c in cases:
        order = (c.get("initial_state") or {}).get("order") or {}
        status = order.get("status") or ""
        body = c["body"]
        if not order or status == "signed":
            continue
        if not RECEIVED_RE.search(body):
            continue
        if IN_TRANSIT_RE.search(body):
            continue

        negated = bool(NEGATED_RE.search(body))
        req = corpus.has_explicit_request(body)
        if negated:
            key = "1 真·否定语境（货没到手）— 宽尺子误判"
        elif req:
            key = "2 点了动作集内诉求 — 订单可处置，正解可执行"
        else:
            key = "3 只描述状态、无动作诉求 — ★真该追的"
        buckets[key] += 1
        rows.setdefault(key, []).append(f"[{status}/{c['level']}] {body[:66]}")

    total = sum(buckets.values())
    print(f"宽尺子 A 类残留: {total}\n")
    for k in sorted(buckets):
        print(f"{k:44s} {buckets[k]:4d}")
    print()
    for k in sorted(rows):
        print(f"── {k} ──")
        for r in rows[k][:8]:
            print("   ", r)
        print()


if __name__ == "__main__":
    main()
