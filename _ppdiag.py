"""那 223 条"正文能认出价保"的语料，**scene 到底标的是什么**？

`_scenemap.py` 显示：只看正文能认出 223 条 price_protect，
但带上 scene 只剩 2 条 —— 说明 scene 优先这一步把它们吃掉了。
问题是：被哪一类 scene 吃掉的？

这个答案决定修法：
  · 如果落在 `退换货/维权投诉/差评/质量问题`（值为 None）→ 已经交给正文了，
    那 223 条的统计口径本身有问题，需要重算。
  · 如果落在 `退款/物流异常/补偿方案/...`（直接映射）→ 是 scene 优先
    这条**设计本身**在吃信号，要改优先级。
"""

from __future__ import annotations

import re
from collections import Counter

from ticket_agent.db import corpus

#: 与 corpus._REQUEST_HINT_PATTERNS 里 price_protect 那条保持一致
PRICE_RE = re.compile(r"价保|保价|退差|差价|降价|便宜了")

tickets = corpus.load_corpus()
hits = [t for t in tickets if PRICE_RE.search(t.body)]
print(f"含价保关键词的语料：{len(hits)} 条\n")

by_scene = Counter(t.scene for t in hits)
print("【这些句子的 scene 分布】")
for sc, n in by_scene.most_common():
    mapped = corpus._SCENE_TO_REQUEST.get(sc, None) if sc else None
    tag = "→ 直接映射" if mapped is not None else "→ 交给正文(None)"
    print(f"   {sc or '(空)':<10} {n:>4}   {tag}")

print("\n【逐条：scene 优先 vs 只看正文 的判定差异】")
diff: Counter[tuple[str, str]] = Counter()
same = 0
for t in hits:
    with_scene = corpus.classify_request(t.body, t.scene)
    body_only = corpus.classify_request(t.body, "")
    diff[(with_scene, body_only)] += 1
    if with_scene == body_only:
        same += 1

for (a, b), n in diff.most_common():
    mark = "" if a == b else "   ★ 被判成别的了"
    print(f"   scene优先={a:<16} 只看正文={b:<16} {n:>4}{mark}")
print(f"\n   两者一致 {same} 条 / 不一致 {len(hits) - same} 条")

print("\n【现在（质量问题已改 None）整份语料的 price_protect 数】")
n_pp = sum(1 for t in tickets if corpus.classify_request(t.body, t.scene) == "price_protect")
n_pp_body = sum(1 for t in tickets if corpus.classify_request(t.body, "") == "price_protect")
print(f"   scene优先 {n_pp} 条 / 只看正文 {n_pp_body} 条（语料共 {len(tickets)}）")
