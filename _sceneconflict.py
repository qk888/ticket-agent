"""量一量：语料里"scene 说是补偿，但客户正文明确说要退货/换货"的情况有多少。

决定性问题：`我要退货，但是降价了很生气` 这条我编的样本，在真实语料里
是一个**普遍现象**还是**我编出来的**？这决定修法该不该动。

判据（严格）：
  A. scene 映射给出一个**明确诉求**（非 None），且
  B. 正文里含**显式**退换货信号（_EXCHANGE_RE / _RETURN_RE），且
  C. 两者不一致

对 A∩B 的每一类，打印样本，人工判"客户到底要什么"。
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from ticket_agent.db import corpus  # noqa: E402

RAW = ROOT / "data" / "raw" / "jddc_aftersale.jsonl"
norm = corpus.normalize

rows = []
with RAW.open(encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

conflicts: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
n_scene_mapped = 0
n_text_explicit = 0

for r in rows:
    text = norm(r.get("user") or "")
    scene_raw = r.get("scene") or ""
    scene = corpus.normalize_scene(scene_raw) if hasattr(corpus, "normalize_scene") else scene_raw
    mapped = corpus._SCENE_TO_REQUEST.get(scene, None)
    if mapped is None:
        continue
    n_scene_mapped += 1

    text_kind = None
    if corpus._EXCHANGE_RE.search(text):
        text_kind = "exchange"
    elif corpus._RETURN_RE.search(text):
        text_kind = "return_refund"
    if text_kind is None:
        continue
    n_text_explicit += 1

    if text_kind != mapped:
        conflicts[(scene, mapped, text_kind)].append(r.get("user") or "")

print("=" * 78)
print("scene 给出明确诉求的行数：", n_scene_mapped)
print("其中正文含显式退换货信号的：", n_text_explicit)
print()
print(f"★ 两者冲突的组合数：{len(conflicts)}  总条数：{sum(len(v) for v in conflicts.values())}")
print("=" * 78)
for (scene, mapped, text_kind), samples in sorted(conflicts.items(), key=lambda kv: -len(kv[1])):
    print(f"\nscene={scene!r} → 映射 {mapped}，但正文显式是 {text_kind}   共 {len(samples)} 条")
    for s in samples[:6]:
        print(f"    {s}")

# 反向：正文没信号但 scene 给了明确诉求 —— 这是 scene 在独立贡献，不能动
print()
print("=" * 78)
print("对照：scene 给出明确诉求、正文**无**退换货信号的条数（scene 的净贡献）")
print("=" * 78)
dist = collections.Counter()
for r in rows:
    text = norm(r.get("user") or "")
    scene_raw = r.get("scene") or ""
    scene = corpus.normalize_scene(scene_raw) if hasattr(corpus, "normalize_scene") else scene_raw
    mapped = corpus._SCENE_TO_REQUEST.get(scene, None)
    if mapped is None:
        continue
    if corpus._EXCHANGE_RE.search(text) or corpus._RETURN_RE.search(text):
        continue
    dist[mapped] += 1
for k, n in dist.most_common():
    print(f"  {k:<18} {n:>6}")
