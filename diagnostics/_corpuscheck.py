"""接入多轮源之后的语料体检 —— 三个必须先验的问题。

★ 为什么这三件事必须验，而不是"跑完测试就好"：

  ① **繁体污染**：多轮源是新的一条来源，它可能来自不同的上游切分流程。
     §4.5 已经确认判据靠 `textnorm.normalize` 看穿字形，但那只保证
     **判据**不被繁体骗到；语料里若混进繁体，工单正文就会带着繁体字形
     进入报表，而报表是给人看的。更要紧的是 `_POST_RECEIPT_RE` 这类
     词典本来就是简体，繁体正文可能被**判反**。

  ② **风格分布**：`defect` 只有 193/11079 = 1.7%，比旧语料的 2.6% 还低。
     若新增的几千条几乎全是 vague，评测集会被进一步拉偏 ——
     这正是 §4.4 记录过的"放大 18.7 倍"那类问题的变体。

  ③ **单字/垃圾行**：合并两路来源时用清洗后的文本当去重键，
     若某路来源切分不当，可能产出"显示"、"不是"这类残句。它们在
     `reject_reason` 里本该被"太短"挡住（<10 字），但要亲眼确认。
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from ticket_agent.db import corpus  # noqa: E402
from ticket_agent.db.textnorm import normalize  # noqa: E402

RAW = ROOT / "data" / "raw" / "jddc_aftersale.jsonl"

print("=" * 78)
print("① 繁体污染：正文原字形 vs 归一化后是否不同")
print("=" * 78)

rows = [json.loads(l) for l in RAW.open(encoding="utf-8") if l.strip()]
print(f"语料总行数：{len(rows):,}")

changed = [r for r in rows if normalize(r["user"]) != r["user"]]
print(f"含表内繁体、会被 normalize 改变的行：{len(changed):,} "
      f"({len(changed) / len(rows):.2%})")
for r in changed[:12]:
    print(f"  · {r['user'][:52]}")
    print(f"      → {normalize(r['user'])[:52]}")

# 表外繁体（normalize 不动，但人眼一看就是繁体）
OUT_TABLE = re.compile(r"[麼樣訂結處會現確覺帳認還貨籤質賣買單費點問題語關]")
outside = [r for r in rows if OUT_TABLE.search(r["user"]) and normalize(r["user"]) == r["user"]]
print(f"\n★ 含表外繁体、normalize 不动它的行：{len(outside):,} "
      f"({len(outside) / len(rows):.2%})")
for r in outside[:8]:
    print(f"  · {r['user'][:56]}")

print()
print("=" * 78)
print("② 风格分布：新语料 vs 旧语料")
print("=" * 78)
corpus.load_corpus.cache_clear()
st = corpus.corpus_stats()
by_style = st["by_style"]
tot = sum(by_style.values())
old = {"explicit": 2327, "vague": 4139, "defect": 172}
ot = sum(old.values())
print(f"{'风格':<10}{'新条数':>8}{'新占比':>9}{'旧占比':>9}{'变化':>9}")
for k in ("explicit", "defect", "vague"):
    n, o = by_style.get(k, 0), old.get(k, 0)
    print(f"  {k:<8}{n:>8}{n / tot:>9.1%}{o / ot:>9.1%}"
          f"{n / tot - o / ot:>+9.1%}")

print()
print("=" * 78)
print("③ 最短的 20 条（确认没有被闸门漏放的残句）")
print("=" * 78)
short = sorted(rows, key=lambda r: len(r["user"]))[:20]
for r in short:
    print(f"  [{len(r['user']):>3}] [{r['scene']}] {r['user']}")

print()
print("=" * 78)
print("④ 去重键是否真的生效（同正文只应出现一次）")
print("=" * 78)
users = [r["user"] for r in rows]
dup = [t for t, c in Counter(users).items() if c > 1]
print(f"重复正文：{len(dup)} 种")
for t in dup[:5]:
    print(f"  · {t[:56]}")

print()
print("=" * 78)
print("⑤ 新语料能否被 corpus.pick 正常抽出（按业务层）")
print("=" * 78)
import random  # noqa: E402
rng = random.Random(7)
for lvl in ("L1", "L2", "L3"):
    picked = [corpus.pick(rng, lvl) for _ in range(400)]
    miss = sum(1 for p in picked if p is None)
    styles = Counter(p.style for p in picked if p)
    print(f"  {lvl}: 400 抽中 {400 - miss} 条，风格分布 {dict(styles)}")
