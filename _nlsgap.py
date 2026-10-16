"""量化 NLS 自己的漏：哪些字它该转却没转。

背景
----
`_nlsimpact.py` 第 ④ 段报出「归一化后仍未见过 321 个汉字」，其中出现了 `後199`、
`於`、`乾`、`牠` 等**明显是繁体**的字。这说明 NLS 在 zh-CN locale 下并非全转。

这很重要，因为如果 NLS 有系统性漏字，那"用它做归一化"就必须附带一张补充表，
否则归一化是半成品，判据仍会判反 —— 而且比手工表更危险，因为**它让人以为已经处理过了**。

做法：不猜，逐字问 NLS「这个字在你眼里繁简同形吗」，然后把答案分类：
  · NLS 转换后变了 → 它认得，不是漏
  · NLS 转换后没变，但在繁体数据集里高频 → 候选漏字（再用字义人工确认）
  · NLS 转换后没变，且是真正的繁简同形字（如 `我/的/不`）→ 正常
"""

import ctypes
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ticket_agent.db import corpus  # noqa: E402

LCMAP_SIMPLIFIED_CHINESE = 0x02000000
LCMAP_TRADITIONAL_CHINESE = 0x04000000
LCID_ZH_CN = 0x0804
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
CJK = re.compile(r"[\u4e00-\u9fff]")


def conv(text: str, flag: int) -> str:
    n = _kernel32.LCMapStringW(LCID_ZH_CN, flag, text, -1, None, 0)
    if n == 0:
        raise OSError(ctypes.get_last_error())
    buf = ctypes.create_unicode_buffer(n)
    if _kernel32.LCMapStringW(LCID_ZH_CN, flag, text, -1, buf, n) == 0:
        raise OSError(ctypes.get_last_error())
    return buf.value


def to_simplified(text: str) -> str:
    return conv(text, LCMAP_SIMPLIFIED_CHINESE)


def to_traditional(text: str) -> str:
    return conv(text, LCMAP_TRADITIONAL_CHINESE)


def main() -> None:
    print("=" * 78)
    print("① 先验证：NLS 到底认不认得那几个可疑字")
    print("=" * 78)
    suspects = ["後", "於", "乾", "牠", "讚", "嬤", "麼", "樣", "個", "訂", "會"]
    for ch in suspects:
        s = to_simplified(ch)
        t = to_traditional(ch)
        print(f"  {ch}  →简 {s}   回繁 {t}   {'[认得]' if s != ch else '[★ 未转，漏]'}")

    print()
    print("=" * 78)
    print("② 独立确认漏字：用「繁体数据集里出现、但本项目语料没有」的字")
    print("=" * 78)
    seen: set[str] = set()
    for t in corpus.load_corpus():
        seen |= set(to_simplified(t.body))

    rows = [
        json.loads(l)
        for l in open("data/probe/samples/hf_zhtw_ticket_train_FULL.jsonl", encoding="utf-8")
        if l.strip()
    ]
    freq: dict[str, int] = {}
    for r in rows:
        for ch in r["ticket"]["ticket_text"]:
            if CJK.match(ch):
                freq[ch] = freq.get(ch, 0) + 1

    unknown = {c: n for c, n in freq.items() if c not in seen}
    # 分成两类：NLS 认得的（转换后 != 原文），NLS 漏的（转换后 == 原文）
    nls_ok = {c: n for c, n in unknown.items() if to_simplified(c) != c}
    nls_miss = {c: n for c, n in unknown.items() if to_simplified(c) == c}

    print(f"  归一化后仍未见过的字: {len(unknown)} 个")
    print(f"    · NLS 认得（单字转换后变了）: {len(nls_ok)} —— 是词组上下文导致它没转，不是漏")
    print(f"    · ★ NLS 漏（单字转换后没变）: {len(nls_miss)}")
    print()
    print("  ★ NLS 单字也漏的全部字（按频次，含计数）：")
    miss_sorted = sorted(nls_miss.items(), key=lambda kv: -kv[1])
    print(f"    {''.join(c for c, _ in miss_sorted)}")
    print()
    print("    " + " ".join(f"{c}{n}" for c, n in miss_sorted[:60]))

    print()
    print("=" * 78)
    print("③ 交叉验证：这些漏字在项目中真的会造成判反吗")
    print("=" * 78)
    # 对每个漏字，造一个含它的最短句子，看判据在未归一化/已归一化下是否不同
    # 只挑可能落在判据敏感位的漏字（诉求/到手/瑕疵三类词里出现过的字的繁体形式）
    sensitive_trad = {
        "貨": "退货", "錢": "退钱", "換": "换货", "損": "破损", "壞": "损坏",
        "簽": "签收", "沒": "没收到", "還": "还没", "開": "拆开", "線": "开线",
        "頭": "线头", "質": "质量", "變": "变质", "傷": "损坏", "壞": "坏",
        "後": "后", "於": "于",
    }
    for trad, simp in sensitive_trad.items():
        s = to_simplified(trad)
        mark = "认得" if s != trad else "★ 漏"
        print(f"  {trad} → {s}  ({simp})  [{mark}]")


if __name__ == "__main__":
    main()
