# -*- coding: utf-8 -*-
"""量订单号在语料正文里的普遍程度 + 地址命中的真值。

★ 为什么这一步不能跳：
  上一步发现"银行卡 26 条命中"里 25 条其实是订单号。
  如果订单号在语料里很常见，那"误把订单号当 PII 抹掉"就不是 26 条的小事，
  而是会波及大量工单、直接让 agent 找不到订单。
  这个数决定豁免规则是"锦上添花"还是"生死线"。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
CORPUS_DIR = ROOT / "data" / "raw"
CORPUS_FILES = ("jddc_aftersale.jsonl", "ecom_aftersale.jsonl")

# 订单号/运单号的出现形态：前面带业务词，或裸数字
ORDER_WORD = re.compile(r"(?:订单号|单号|运单号|订单|快递单号|物流单号)\s*[:：]?\s*")
LONG_DIGITS = re.compile(r"(?<!\d)\d{10,25}(?!\d)")
ADDR = re.compile(
    r"[一-龥]{2,8}(?:省|市|区|县)[一-龥\d]{2,20}(?:路|街|道|巷|号|栋|幢|单元|室)"
)


def load_bodies() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in CORPUS_FILES:
        p = CORPUS_DIR / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            b = str(row.get("user") or "").strip()
            if b and b not in seen:
                seen.add(b)
                out.append(b)
    return out


def main() -> None:
    bodies = load_bodies()
    n = len(bodies)
    print(f"语料正文总数：{n}\n")

    with_word = sum(1 for b in bodies if ORDER_WORD.search(b))
    with_long = sum(1 for b in bodies if LONG_DIGITS.search(b))
    with_addr = sum(1 for b in bodies if ADDR.search(b))

    print(f"{'形态':<28}{'条数':>8}{'占比':>9}")
    print("-" * 48)
    for label, c in (
        ("含订单号业务词（订单号/单号/运单号…）", with_word),
        ("含 10 位以上长数字（含裸订单号）", with_long),
        ("含地址片段", with_addr),
    ):
        print(f"{label:<28}{c:>8}{c / n * 100:>8.2f}%")

    # 长数字里有多少是"紧跟业务词"的（这种绝不能抹）
    guarded = 0
    bare = 0
    for b in bodies:
        for m in LONG_DIGITS.finditer(b):
            before = b[: m.start()]
            if ORDER_WORD.search(before[-8:] or before):
                guarded += 1
            else:
                bare += 1
    print(f"\n长数字出现总数：{guarded + bare}")
    print(f"  紧跟业务词（必须豁免）：{guarded}")
    print(f"  裸数字（需按 Luhn 判断）：{bare}")

    print("\n=== 地址命中全部样本（人工判真值）===")
    for b in bodies:
        m = ADDR.search(b)
        if m:
            print(f"  {m.group(0)}")
            print(f"      ← {b[:80]}")


if __name__ == "__main__":
    main()
