"""核对副作用：scene='赔付' 里被判成 price_protect 的 169 条，到底是不是价保诉求。

判据：客户说的是"退/补价格差"（price_protect），还是"赔偿我的损失"（compensate）？
打印样本，人工判。特别关注**同时**含价保词与损害词（碎/坏/漏/过期）的句子
—— 那才是真正会判错的一类。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from ticket_agent.db import corpus  # noqa: E402

RAW = ROOT / "data" / "raw" / "jddc_aftersale.jsonl"
rows = []
with RAW.open(encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass

DAMAGE_RE = re.compile(r"碎|破|坏|漏|过期|变质|瑕疵|少发|漏发|没人|损失|耽误|坏了")
flipped, mixed = [], []
kept = []
for r in rows:
    if (r.get("scene") or "") != "赔付":
        continue
    text = r.get("user") or ""
    kind = corpus.classify_request(text, r.get("scene") or "")
    if kind == "price_protect":
        flipped.append(text)
        if DAMAGE_RE.search(corpus.normalize(text)):
            mixed.append(text)
    elif kind == "compensate":
        kept.append(text)

print(f"scene='赔付' 共 {len(flipped) + len(kept)} 条")
print(f"  → price_protect {len(flipped)} 条（补判翻掉）")
print(f"  → compensate    {len(kept)} 条（scene 保住）")
print()
print("=" * 78)
print(f"★ 关键：翻掉的里面**同时含损害词**的 {len(mixed)} 条 —— 这些最可能是判错的")
print("=" * 78)
for t in mixed[:20]:
    print(f"  {t}")
print()
print("=" * 78)
print("翻掉里**不含**损害词的样本（这些几乎肯定是价保）")
print("=" * 78)
pure = [t for t in flipped if not DAMAGE_RE.search(corpus.normalize(t))]
for t in pure[:15]:
    print(f"  {t}")
print()
print("=" * 78)
print("保住的 compensate 样本（核对 scene 是否更准）")
print("=" * 78)
for t in kept[:15]:
    print(f"  {t}")
