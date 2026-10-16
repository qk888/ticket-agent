"""量一下评测集的风格分布与句子的复用倍数。

为什么要单独量：报表上「按话术风格」那张表的每一格都来自 `case["style"]`，
而它是**造数时**由 `level` 决定的（`pick` 按 `_LEVEL_TO_STYLES` 挑池子），
不是语料的自然分布。L1+L2 占样本 82% 而两层都优先 `defect` 池 ——
于是语料里只占 2.6% 的 defect 会在评测集里被放大十几倍。
本脚本把这个倍数算出来，作为修法（改映射 / 改抽样 / 改报表）的依据。
"""

from __future__ import annotations

import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

from ticket_agent.db import corpus, seed  # noqa: E402

st = corpus.corpus_stats()
print("语料 by_style :", st["by_style"])
print("语料唯一话术  :", st["unique_phrasings"])
print()

cases = seed.build_eval_cases(size=605, seed=7)
n = Counter(c["style"] for c in cases)
total = sum(n.values())
print("评测集 by_style:", dict(n), f"共 {total} 条")
print()

print(f"{'风格':<10}{'评测集条数':>10}{'占比':>9}{'语料池':>9}{'语料占比':>10}{'不同句子':>10}{'复用倍数':>10}")
for sty in ("explicit", "defect", "vague"):
    bodies = [c["body"] for c in cases if c["style"] == sty]
    uniq = len(set(bodies))
    pool = st["by_style"].get(sty, 0)
    share = len(bodies) / max(1, total)
    pool_share = pool / max(1, st["unique_phrasings"])
    reuse = len(bodies) / max(1, uniq)
    print(
        f"{sty:<10}{len(bodies):>10}{share:>9.1%}{pool:>9}{pool_share:>10.1%}"
        f"{uniq:>10}{reuse:>9.1f}x"
    )

print()
print("★ 放大倍数 = 评测集占比 / 语料占比：")
for sty in ("explicit", "defect", "vague"):
    share = n[sty] / max(1, total)
    pool_share = st["by_style"].get(sty, 0) / max(1, st["unique_phrasings"])
    print(f"  {sty:<10} {share / max(pool_share, 1e-9):>6.1f}x")

print()
print("按 level × style 交叉：")
lv = Counter((c["level"], c["style"]) for c in cases)
for k in sorted(lv):
    print(f"  {k[0]} × {k[1]:<9} {lv[k]:>5}")
