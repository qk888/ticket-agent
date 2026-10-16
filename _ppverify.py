"""验证缺陷 ㉘ 的修法：正文补判（_BODY_ONLY_KINDS）是否既救回价保、又不误抢退换货。

三个问题，逐个回答：
  1. 整份语料里 price_protect 现在能认出多少条？（修法前：scene 优先 2 条）
  2. 那 244 条含价保关键词的话术，现在判成什么？
  3. ★ 最关键的**回归风险**：有没有句子本来该判退货/退款，现在被"降价/差价"
     这些词抢走了？逐条打印所有"正文含价保词但最终判成 price_protect"里
     同时含退货信号的句子，人工核。
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

# 与 corpus 内部同一套归一化，避免字形差异导致漏判
_norm = corpus.normalize

PROTECT_RE = corpus._BODY_ONLY_KINDS[0][1]
RETURN_HINT = corpus._RETURN_RE
EXCHANGE_HINT = corpus._EXCHANGE_RE

print("=" * 78)
print("① 整份语料的诉求分布（scene 优先 + 正文补判）")
print("=" * 78)

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

print(f"语料条数：{len(rows)}")

dist = collections.Counter()
protect_rows = []
for r in rows:
    text = r.get("user") or ""
    scene = r.get("scene") or ""
    kind = corpus.classify_request(text, scene)
    dist[kind] += 1
    if kind == "price_protect":
        protect_rows.append((text, scene))

for k, n in dist.most_common():
    print(f"  {k:<18} {n:>6}  ({n / len(rows):.1%})")

print()
print("=" * 78)
print("② 含价保关键词的话术，现在判成什么（只看 user 字段，即客户原话）")
print("=" * 78)

hits = [(r.get("user") or "", r.get("scene") or "") for r in rows if PROTECT_RE.search(_norm(r.get("user") or ""))]
print(f"含价保关键词的客户原话：{len(hits)} 条")
c2 = collections.Counter(corpus.classify_request(t, s) for t, s in hits)
for k, n in c2.most_common():
    print(f"  {k:<18} {n:>6}")

print()
print("=" * 78)
print("③ ★ 回归风险：被判成 price_protect 的句子里，有多少**同时**含退货/换货信号")
print("=" * 78)

risky = []
for text, scene in protect_rows:
    t = _norm(text)
    tag = []
    if RETURN_HINT.search(t):
        tag.append("退货词")
    if EXCHANGE_HINT.search(t):
        tag.append("换货词")
    if tag:
        risky.append((text, scene, "+".join(tag)))

print(f"price_protect 共 {len(protect_rows)} 条，其中含退换货信号的：{len(risky)} 条")
if risky:
    print("（这些是必须人工核的——若客户真实意图是退货，则说明补判抢错了）")
    for text, scene, tag in risky[:25]:
        print(f"  [{tag}] scene={scene!r}")
        print(f"      {text}")
else:
    print("  ✓ 没有一条 price_protect 同时含退换货信号 —— 补判没有抢走退货/换货")

print()
print("=" * 78)
print("④ 边界样本：含退换货信号 + 含价保词的句子，各自该判什么")
print("=" * 78)
print("★ 判据不是「含退货词就必须判退货」—— 那样会把 755 条真实语料判错。")
print("  见 `_sceneconflict.py`：scene='退款' 而正文明说退换货的 737 条里，")
print("  绝大多数是「我已经退回去了，退款到账了吗」—— 客户在说退货这件事，")
print("  不等于要我办退货。所以**验的是具体语义，不是关键词**。")

cases = [
    # (句子, scene, 期望, 为什么)
    ("我要退货，但是降价了很生气", "退换货", "return_refund", "scene 不足以定诉求 + 正文显式退货"),
    ("刚买就降价了，能价保吗", "补偿方案", "price_protect", "★ 缺陷 ㉘ 的主角：正文补出 scene 表达不了的价保"),
    ("能退我4元差价吗", "补偿方案", "price_protect", "同上"),
    ("保价怎么弄", "物流", "price_protect", "同上，且 scene 明明是物流"),
    ("我要换一件别的尺码", "退换货", "exchange", "换货家族不受补判影响"),
    ("我要退款", "退款", "refund", "scene 明确 + 正文一致"),
    ("帮我催一下快递", "物流", "urge", "scene 明确"),
    ("我退回去的九包纸巾收到了吗退款给我了吗", "退款", "refund", "★ 真实语料：说退货≠办退货，scene 更准"),
]
for text, scene, want, why in cases:
    got = corpus.classify_request(text, scene)
    mark = "✓" if got == want else "✗"
    print(f"  {mark} scene={scene:<6} → {got:<18} 期望 {want:<14} {text}")
    print(f"      {why}")

print()
print("=" * 78)
print("⑤ 全语料回归：每个诉求都能产出了吗")
print("=" * 78)
import collections as _c
per_scene: dict[str, _c.Counter] = _c.defaultdict(_c.Counter)
for r in rows:
    text = r.get("user") or ""
    scene = r.get("scene") or ""
    per_scene[scene][corpus.classify_request(text, scene)] += 1
for scene in sorted(per_scene):
    c = per_scene[scene]
    print(f"  scene={scene!r:<16} → {dict(c.most_common(4))}")
