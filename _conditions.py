"""诊断：库里工单的**条件分布**长什么样。

动机：`_sensitivity.py` 已证明 `guesser`(总 acc 0.900) 在有机会发作的每条 case 上
都发作且全错 —— 它的低边际影响纯粹是**覆盖度**问题（这类 case 只占 10%）。
那就该问一句：自然分布下每种条件各占多少？如果某些条件是 0 条，
评测集永远不可能发现对应故障，而这个事实必须摆在明面上。

用法：python _conditions.py
"""

from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, "src")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

from ticket_agent.db import build_eval_cases, init_db, seed_all  # noqa: E402

CUSTOMERS = 60
SEED = 7


def main() -> int:
    init_db(drop=True)
    stats = seed_all(n_customers=CUSTOMERS, seed=SEED)
    print(f"seed: {stats}")

    all_cases = build_eval_cases(size=100000, seed=SEED)
    print(f"全量工单 {len(all_cases)} 条\n")

    buckets = Counter(c.get("condition", "?") for c in all_cases)
    total = len(all_cases)
    print(f"{'条件':<14}{'条数':>6}{'占比':>9}   分层")
    print("─" * 46)
    for cond, n in buckets.most_common():
        share = n / total if total else 0.0
        levels = Counter(c["level"] for c in all_cases if c.get("condition") == cond)
        lv = "/".join(f"{k}={v}" for k, v in sorted(levels.items()))
        print(f"{cond:<14}{n:>6}{share:>8.1%}   {lv}")

    print("─" * 46)
    print(f"{'合计':<14}{total:>6}")

    # 拿 30 条时（跟 _sensitivity.py 同一设置）各条件能进几条 —— 这才是"覆盖度"的直接证据
    sampled = build_eval_cases(size=30, seed=SEED)
    got = Counter(c.get("condition", "?") for c in sampled)
    print(f"\n取 30 条（stratify=False，与 _sensitivity.py 同设置）时的构成：")
    for cond, n in buckets.most_common():
        mark = "✗ 一条都没有" if got.get(cond, 0) == 0 else ""
        print(f"  {cond:<14} 全量 {n:>3} → 抽样 {got.get(cond, 0):>2}   {mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
