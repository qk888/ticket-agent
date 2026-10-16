"""把整份语料过一遍 `classify_request`，看它到底能产出哪些诉求。

`_classifytrace.py` 证明分类器逐句是对的；`_gapdiag.py` 说成品工单里
price_protect 是 0 条。中间只有一个环节：**scene 优先**这一步。
这个脚本把那个环节摊开看。
"""

from __future__ import annotations

from collections import Counter

from ticket_agent.db import corpus

print("【_SCENE_TO_REQUEST 全表】")
for k, v in sorted(corpus._SCENE_TO_REQUEST.items()):
    print(f"   {k:<8} → {v}")

print(f"\n【_SCENE_CANON 全表】{corpus._SCENE_CANON}")

tickets = corpus.load_corpus()
print(f"\n语料 {len(tickets)} 条，逐条判诉求：")

by_kind: Counter[str] = Counter()
by_kind_scene: Counter[tuple[str, str]] = Counter()
for t in tickets:
    k = corpus.classify_request(t.body, t.scene)
    by_kind[k] += 1
    by_kind_scene[(k, t.scene)] += 1

for k, v in by_kind.most_common():
    print(f"   {k:<16} {v:>6}  ({v / max(len(tickets), 1):.1%})")

print("\n【price_protect 是从哪些 scene 出来的】（应为 0 才是问题）")
pp = {k[1]: v for k, v in by_kind_scene.items() if k[0] == "price_protect"}
print(f"   {pp or '无'}")

print("\n【各 scene 实际产出什么诉求】")
for sc in sorted({t.scene for t in tickets}):
    sub = {k[0]: v for k, v in by_kind_scene.items() if k[1] == sc}
    print(f"   {sc:<8} → {dict(sorted(sub.items(), key=lambda kv: -kv[1]))}")

print("\n【不含 scene 时（只看正文）产出什么】—— 这才是『正文能认出多少』的真值")
by_body: Counter[str] = Counter()
for t in tickets:
    by_body[corpus.classify_request(t.body, "")] += 1
for k, v in by_body.most_common():
    print(f"   {k:<16} {v:>6}  ({v / max(len(tickets), 1):.1%})")

print("\n【含金额的语料条数】")
n_amt = sum(1 for t in tickets if corpus.extract_amount(t.body) > 0)
print(f"   {n_amt} 条（占 {n_amt / max(len(tickets), 1):.1%}）")
