"""诊断：为什么 kinds= 筛选之后 price_protect 仍然抽不到？

三个可能：
  A. `classify_request(t.body, t.scene)` 在**语料对象**上判不出 price_protect
     （与直接喂 raw json 的 `user` 字段不同 —— 语料的 body 可能不是 user 字段）
  B. 那些句子落在 `level_to_styles(level)` **之外的风格池**里，压根不是候选
  C. 抽到了，但 golden 侧没走到 R11（那是另一半问题）
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from ticket_agent.db import corpus  # noqa: E402

items = corpus.load_corpus()
print(f"语料对象总数：{len(items)}")

# A. 在语料对象上重判，分布如何？
dist = collections.Counter(corpus.classify_request(t.body, t.scene) for t in items)
print("\nA. 语料对象上的诉求分布：")
for k, n in dist.most_common():
    print(f"   {k:<18} {n:>6}")

# price_protect 的那些句子，长什么样、什么风格
pp = [t for t in items if corpus.classify_request(t.body, t.scene) == "price_protect"]
print(f"\nprice_protect 共 {len(pp)} 条")
for t in pp[:5]:
    print(f"   style={t.style:<9} scene={t.scene:<8} {t.body[:44]}")

# B. 它们落在哪些风格里？各风格总条数多少？
by_style = collections.Counter(t.style for t in pp)
print(f"\nB. price_protect 的风格分布：{dict(by_style)}")
print("   全部风格池大小：", {s: len(corpus.pool(s)) for s in sorted({t.style for t in items})})

print("\n   各业务层允许的风格：")
for lv in ("L1", "L2", "L3"):
    styles = corpus.level_to_styles(lv)
    n = sum(1 for t in pp if t.style in styles)
    print(f"     {lv}: {styles}  → 可用的 price_protect 条数 = {n}")

# C. compensate 同理：带金额的有几条？
COMP = [t for t in items if corpus.classify_request(t.body, t.scene) == "compensate"]
with_amt = [t for t in COMP if corpus.extract_amount(t.body) > 0]
print(f"\nC. compensate 共 {len(COMP)} 条，其中带金额的 {len(with_amt)} 条")
for t in with_amt[:5]:
    print(f"   style={t.style:<9} {corpus.extract_amount(t.body):>7.2f}  {t.body[:40]}")
print("   各层可用的带金额 compensate：")
for lv in ("L1", "L2", "L3"):
    styles = corpus.level_to_styles(lv)
    n = sum(1 for t in with_amt if t.style in styles)
    print(f"     {lv}: {n}")
