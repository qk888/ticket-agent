"""关键验证：把 NLS 归一化加进判据入口，对**现有简体数据**是否完全无影响。

为什么这一步决定要不要改生产代码
--------------------------------
判据加一层「先归一化再匹配」有两种可能的结果，性质完全不同：

  甲、它对简体输入是**恒等**的 → 现有 6,638 条语料、589 张工单、全部测试的行为
      一个字节都不变，改动的唯一效果是多认得了繁体输入。这是**纯增益**，
      不需要任何"重新评估"就能上。
  乙、它对简体输入有**副作用**（把某些简体字也改了）→ 那它就是在悄悄改判据的
      语义，现有报表全部作废，必须先量化影响面。

不做这个验证就动手，等于赌甲成立。这份脚本把赌变成算。

同时量化另一半：需要多少覆盖率才够用 —— 用 NLS 去扫繁体数据集，
统计「NLS 转换后仍与原文相同」的字符（即繁简同形字），它们是归一化的天然上限。
"""

import ctypes
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ticket_agent.db import corpus  # noqa: E402

LCMAP_SIMPLIFIED_CHINESE = 0x02000000
LCID_ZH_CN = 0x0804
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def to_simplified(text: str) -> str:
    n = _kernel32.LCMapStringW(LCID_ZH_CN, LCMAP_SIMPLIFIED_CHINESE, text, -1, None, 0)
    if n == 0:
        raise OSError(ctypes.get_last_error())
    buf = ctypes.create_unicode_buffer(n)
    if _kernel32.LCMapStringW(LCID_ZH_CN, LCMAP_SIMPLIFIED_CHINESE, text, -1, buf, n) == 0:
        raise OSError(ctypes.get_last_error())
    return buf.value


def main() -> None:
    print("=" * 78)
    print("① 对现有简体语料：NLS 是恒等变换吗（决定改动风险）")
    print("=" * 78)
    tickets = corpus.load_corpus()
    changed: list[tuple[str, str]] = []
    for t in tickets:
        s = to_simplified(t.body)
        if s != t.body:
            changed.append((t.body, s))
    print(f"  语料条数            : {len(tickets)}")
    print(f"  归一化后发生变化的条数: {len(changed)} ({len(changed) / len(tickets) * 100:.2f}%)")
    if changed:
        print("  ★ 有副作用，逐条列出前 20：")
        for a, b in changed[:20]:
            print(f"    {a}")
            print(f"    → {b}")

    print()
    print("② 对内置模板与判据词典：NLS 有没有改字")
    print("=" * 78)
    from ticket_agent.db import seed

    tpl_changed = []
    for text in getattr(seed, "TICKET_TEMPLATES", []) or []:
        s = to_simplified(text)
        if s != text:
            tpl_changed.append((text, s))
    print(f"  内置模板条数          : {len(getattr(seed, 'TICKET_TEMPLATES', []) or [])}")
    print(f"  归一化后发生变化的条数: {len(tpl_changed)}")

    # 判据正则的源文本本身也过一遍
    import re

    pats = {
        "_REQUEST_RE": corpus._REQUEST_RE,
        "_DEFECT_RE": corpus._DEFECT_RE,
        "_PRE_RECEIPT_RE": corpus._PRE_RECEIPT_RE,
        "_POST_RECEIPT_RE": corpus._POST_RECEIPT_RE,
    }
    print()
    for name, pat in pats.items():
        src = pat.pattern
        s = to_simplified(src)
        mark = "原样" if s == src else "★ 被改动"
        print(f"  {name:<20} [{mark}]")

    print()
    print("=" * 78)
    print("③ 繁体数据集：归一化后还剩多少「与原文相同」的字符（天然上限）")
    print("=" * 78)
    rows = [
        json.loads(l)
        for l in open("data/probe/samples/hf_zhtw_ticket_train_FULL.jsonl", encoding="utf-8")
        if l.strip()
    ]
    total_chars = 0
    same_chars: dict[str, int] = {}
    for r in rows:
        text = r["ticket"]["ticket_text"]
        s = to_simplified(text)
        total_chars += len(text)
        for a, b in zip(text, s):
            if a == b and "\u4e00" <= a <= "\u9fff":
                same_chars[a] = same_chars.get(a, 0) + 1
    print(f"  数据集条数          : {len(rows)}")
    print(f"  总字符数            : {total_chars}")
    print(f"  归一化后繁简同形汉字数: {len(same_chars)}")
    print()
    top = sorted(same_chars.items(), key=lambda kv: -kv[1])[:40]
    print(f"  最高频的繁简同形汉字（这些字转换不转换都一样，是归一化的天然上限）:")
    print("    " + " ".join(f"{c}{n}" for c, n in top))
    print()
    print("  说明：同形字不影响判据 —— 它们在简体里也是这个样子，判据本来就认得。")
    print("        真正危险的是**异形但未被转换**的字，NLS 若不转换它们即是它的漏。")


if __name__ == "__main__":
    main()
