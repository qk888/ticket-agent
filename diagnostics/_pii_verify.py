# -*- coding: utf-8 -*-
"""验收 PII 脱敏模块：误报 0 容忍 + 正样本必须全中。

★ 为什么单独一个脚本而不是只写 pytest：
  这两个方向要分开看 —— 误报是"砸业务"，正样本是"合规有效"。
  混在一张表里看，容易用一边的成绩掩盖另一边的失败。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ticket_agent.db import pii  # noqa: E402


def main() -> int:
    fail = 0

    print("=" * 78)
    print("A. 误报检查：真实语料里‘看起来像 PII 但其实是别的’")
    print("   任何一条被脱敏 = 规则退化 = 会砸掉业务（抹掉订单号 agent 就瞎了）")
    print("=" * 78)
    for s in pii.FALSE_POSITIVE_SAMPLES:
        out, found = pii.redact(s)
        ok = not found
        fail += 0 if ok else 1
        print(f"[{'OK ' if ok else 'FAIL'}] {s[:56]}")
        if not ok:
            for f in found:
                print(f"        ← 误脱敏 kind={f.kind} 原文片段={s[f.start:f.end]!r}")

    print()
    print("=" * 78)
    print("B. 正样本检查：语料 0 命中，所以必须自造。不中 = 模块从未被真正验证")
    print("=" * 78)
    for text, want in pii.TRUE_POSITIVE_SAMPLES:
        out, found = pii.redact(text)
        kinds = {f.kind for f in found}
        ok = want in kinds
        fail += 0 if ok else 1
        print(f"[{'OK ' if ok else 'FAIL'}] {text}")
        print(f"        期望 {want}；实际命中 {sorted(kinds) or '空'}")
        print(f"        脱敏后：{out}")

    print()
    print("=" * 78)
    print("C. 业务钥匙必须活着：订单号无论如何都不能被抹")
    print("=" * 78)
    guards = (
        "订单号:19627589320267052 帮我查一下（★ 这条 Luhn 恰好通过，最危险）",
        "运单号3239746110956160缺货请退款谢谢",
        "单号2203476352141368什么时候派送呢",
    )
    for s in guards:
        out, found = pii.redact(s)
        ok = not found
        fail += 0 if ok else 1
        print(f"[{'OK ' if ok else 'FAIL'}] {s}")
        if not ok:
            print(f"        ← 被脱敏了：{sorted({f.kind for f in found})}")

    print()
    print("=" * 78)
    print("D. 不可恢复性：脱敏后不得残留原文")
    print("=" * 78)
    t = "我叫张三，手机13812345678，邮箱zs@example.com"
    out, _ = pii.redact(t)
    leak = [x for x in ("13812345678", "zs@example.com") if x in out]
    ok = not leak
    fail += 0 if ok else 1
    print(f"  原文：{t}")
    print(f"  脱敏：{out}")
    print(f"  [{'OK ' if ok else 'FAIL'}] 残留原文：{leak or '无'}")

    print()
    print("=" * 78)
    print("E. strict 档：地址/姓名（默认不开，误报率高）")
    print("=" * 78)
    t = "我叫罗光亮，地址是隆回县南岳庙乡老屋场村11组26号"
    s_out, s_found = pii.redact(t, profile="strict")
    std_out, std_found = pii.redact(t)
    print(f"  standard：{std_out}   命中 {sorted({f.kind for f in std_found}) or '无'}")
    print(f"  strict  ：{s_out}   命中 {sorted({f.kind for f in s_found}) or '无'}")

    print()
    print("=" * 78)
    print(f"结论：{'★ 有失败项' if fail else '✓ 全部通过'}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
