# -*- coding: utf-8 -*-
"""探测真实语料里到底有没有 PII、有多少、是什么形状。

★ 这一步必须做在写脱敏模块之前：
  语料里若根本没有 PII，那写一个脱敏模块就是"能力只存在于声明里"
  （缺陷 ⑧ 的形状）的又一次重现 —— 而这次是我亲手造的。

只统计 `user` 字段（客户说的话），因为要脱敏的是客户侧输入。
"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
CORPUS_DIR = ROOT / "data" / "raw"
CORPUS_FILES = ("jddc_aftersale.jsonl", "ecom_aftersale.jsonl")

# 中国电商售后场景的 PII 形状
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # 手机号：1 开头 + 第二位 3-9 + 共 11 位
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    # 身份证：18 位（末位可为 X）或 15 位老号
    ("身份证", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("身份证15位", re.compile(r"(?<!\d)\d{15}(?!\d)")),
    # 银行卡：16-19 位
    ("银行卡", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    # 邮箱
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    # 固话 / 400 电话
    ("固话", re.compile(r"(?<!\d)(?:0\d{2,3}-?)?\d{7,8}(?!\d)")),
    ("400电话", re.compile(r"400-?\d{3}-?\d{4}")),
    # 常见地址片段（粗筛，只看有没有"省/市/区/路/号/栋/单元"连串）
    ("地址片段", re.compile(
        r"[一-龥]{2,8}(?:省|市|区|县)[一-龥\d]{2,20}(?:路|街|道|巷|号|栋|幢|单元|室)"
    )),
]


def load_bodies() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in CORPUS_FILES:
        path = CORPUS_DIR / name
        if not path.exists():
            print(f"★ 缺失：{path}")
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            body = str(row.get("user") or "").strip()
            if body and body not in seen:
                seen.add(body)
                out.append(body)
    return out


def main() -> None:
    bodies = load_bodies()
    print(f"语料正文总数：{len(bodies)}\n")
    print(f"{'类型':<12}{'命中条数':>10}{'占比':>10}   样本")
    print("-" * 96)

    total_hits = 0
    for label, pat in PATTERNS:
        hits: list[str] = []
        samples: list[str] = []
        for b in bodies:
            m = pat.search(b)
            if m:
                hits.append(b)
                if len(samples) < 3:
                    samples.append(m.group(0))
        pct = (len(hits) / len(bodies) * 100) if bodies else 0.0
        total_hits += len(hits)
        print(f"{label:<12}{len(hits):>10}{pct:>9.2f}%   {samples}")

    print("\n★ 合计命中（有重叠，同一条可能命中多个类型）：", total_hits)

    # 任何一个都没命中就直接说没有，别硬凑
    if total_hits == 0:
        print("\n=== 结论：语料里没有可识别的 PII ===")
        print("→ 此时写脱敏模块只是形式。正确做法是：")
        print("  1) 脱敏模块**照写**（它是真实系统的必备件，且能被单测覆盖）")
        print("  2) 但必须**造带 PII 的样本**去验它，不能拿空命中当‘已脱敏’")
        return

    # 看看命中长什么样，判断是不是误报
    print("\n=== 手机号命中样本（前 10 条，人工看是不是误报）===")
    phone = PATTERNS[0][1]
    n = 0
    for b in bodies:
        m = phone.search(b)
        if m:
            print(f"  {m.group(0)}  ←  {b[:70]}")
            n += 1
            if n >= 10:
                break

    print("\n=== 银行卡命中样本（前 10 条，需 Luhn 校验辨真假）===")
    bank = PATTERNS[3][1]
    n = 0
    for b in bodies:
        m = bank.search(b)
        if m:
            s = m.group(0)
            luhn = _luhn(s)
            print(f"  {s}  Luhn={'通过' if luhn else '不通过'}  ←  {b[:60]}")
            n += 1
            if n >= 10:
                break


def _luhn(s: str) -> bool:
    """Luhn 校验（业界标准：Intercom 的 PAN 规则就用它）。"""
    digits = [int(c) for c in s if c.isdigit()]
    if len(digits) < 13:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


if __name__ == "__main__":
    main()
