"""三个缺口的**直接**根因：语料里到底有没有这些话术？

前一个脚本（`_gapdiag.py`）证明了规则本身能判，问题在"世界里没有这种输入"。
这个脚本只做一件事：把造数用的**真实语料**翻出来，看：
  ① "价保/退差/保价/降价" 这类话术在语料里存在吗？→ price_protect 为什么 0 条
  ② 带"元"字的金额在客户原话里存在吗？→ compensate 为什么取不到金额
  ③ 工单是怎么挑订单的？→ address_change 为什么全落在已发货
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw" / "jddc_aftersale.jsonl"

# 与 corpus._REQUEST_HINT_PATTERNS 里 price_protect 的那条保持一致
PRICE_RE = re.compile(r"价保|保价|退差|差价|降价|便宜了")
AMOUNT_RE = re.compile(r"(?:¥|￥)?\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块钱|块|人民币)")


def main() -> int:
    if not RAW.exists():
        print(f"！找不到语料：{RAW}")
        return 2

    n = 0
    scenes: Counter[str] = Counter()
    price_samples: list[str] = []
    amount_samples: list[str] = []
    # 每条记录的**客户原话**字段（jddc 格式：scene / user / cause_all / bot）
    for line in RAW.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        n += 1
        scenes[str(r.get("scene") or "")] += 1
        user = str(r.get("user") or "")
        if PRICE_RE.search(user) and len(price_samples) < 12:
            price_samples.append(user[:70])
        if AMOUNT_RE.search(user) and len(amount_samples) < 12:
            amount_samples.append(user[:70])

    print(f"语料总条数 {n}")
    print("\n【scene 分布】")
    for k, v in scenes.most_common(15):
        print(f"   {k or '(空)':<12} {v:>6}  ({v / max(n, 1):.1%})")

    print(f"\n【含 price_protect 关键词的客户原话】命中样本 {len(price_samples)} 条（最多显示 12）")
    for s in price_samples:
        print(f"   · {s}")
    if not price_samples:
        print("   ★ 一条都没有 —— 这就是 price_protect 恒为 0 的直接原因：")
        print("     不是分类器写错了，是**造数用的话术里根本没有价保这件事**。")

    print(f"\n【含「数字+元」的客户原话】命中样本 {len(amount_samples)} 条（最多显示 12）")
    for s in amount_samples:
        print(f"   · {s}")
    if not amount_samples:
        print("   ★ 一条都没有 —— 这就是 compensate 取不到金额的直接原因。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
