"""NLS 的漏字规律：为什么 `後/於/乾/牠` 不转，而 `貨/還/籤/質` 转。

猜想（待验证）
--------------
Windows NLS 只做**一对一**字形映射。对多对一的字对（如 `後→后`、`於→于`、`乾→干`、
`牠→它`），简化字本身在繁体字集里**也存在**（`后`/`于`/`干`/`它` 都是 Big5 字），
所以 NLS 无法判断该不该合并 —— 它选择不转。

如果猜想成立，判据就是：
    某字在简体方向不被 NLS 转换，**且**其繁体形式在 NLS 眼里有独立身份
→ 它多半是 NLS 的一对多保守项，**不是**"繁简同形字"。

验证方法：对每个可疑字，问 NLS 三个问题
  ① 繁→简 变不变
  ② 简→繁 变不变（若变，说明 NLS 知道这个字对）
  ③ 该字是否在汉语里本来就是同一个字（如 `覆`/`帽`）

第 ③ 问 NLS 答不了，但 ①② 合起来足以把 208 个"漏字"分成两类：
  · ①②都不变 → 该字在 NLS 的两张表里都是它自己 → 繁简同形字（`覆`/`帽`），无需处理
  · ①不变 ②变     → ★ NLS 知道这字对却不肯简化 = 一对多保守项（`後`/`於`），真漏
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
CJK = re.compile(r"[\u4e00-\u9fff]")
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def conv(text: str, flag: int, lcid: int = LCID_ZH_CN) -> str:
    n = _kernel32.LCMapStringW(lcid, flag, text, -1, None, 0)
    if n == 0:
        raise OSError(ctypes.get_last_error())
    buf = ctypes.create_unicode_buffer(n)
    if _kernel32.LCMapStringW(lcid, flag, text, -1, buf, n) == 0:
        raise OSError(ctypes.get_last_error())
    return buf.value


def to_sim(c: str) -> str:
    return conv(c, LCMAP_SIMPLIFIED_CHINESE)


def to_trad(c: str) -> str:
    return conv(c, LCMAP_TRADITIONAL_CHINESE)


def main() -> None:
    print("=" * 78)
    print("① 猜想的直接检验：那四对字的双向行为")
    print("=" * 78)
    pairs = [("後", "后"), ("於", "于"), ("乾", "干"), ("牠", "它"), ("還", "还"), ("貨", "货")]
    print(f"  {'繁体':<4} {'繁→简':<8} {'简体':<4} {'简→繁':<8}  判断")
    for trad, simp in pairs:
        s = to_sim(trad)
        t = to_trad(simp)
        print(f"  {trad:<4} {s:<8} {simp:<4} {t:<8}  ", end="")
        if s == trad and t == simp:
            print("①不变 ②不变 → 两表都不认这个字对")
        elif s != trad:
            print("①变 → NLS 认得，正常")
        else:
            print(f"①不变 ②变（{simp}→{t}）→ ★ 一对多保守项，真漏")

    print()
    print("=" * 78)
    print("② 换 locale 试：zh-TW / zh-HK / zh-SG 会不会转得更全")
    print("=" * 78)
    locales = {"zh-CN 0x0804": 0x0804, "zh-TW 0x0404": 0x0404, "zh-HK 0x0C04": 0x0C04,
               "zh-SG 0x1004": 0x1004, "系统默认 0x0800": 0x0800}
    probe = "後於乾牠還貨籤質"
    for name, lcid in locales.items():
        try:
            r = conv(probe, LCMAP_SIMPLIFIED_CHINESE, lcid)
            print(f"  {name:<18} {probe} → {r}")
        except OSError as e:
            print(f"  {name:<18} 失败: {e}")

    print()
    print("=" * 78)
    print("③ 把 208 个「NLS 不改」的字按 ①② 分类")
    print("=" * 78)
    seen: set[str] = set()
    for t in corpus.load_corpus():
        seen |= set(to_sim(t.body))

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
    unchanged = {c: n for c, n in unknown.items() if to_sim(c) == c}
    print(f"  归一化后仍未见过的字: {len(unknown)}")
    print(f"  其中 NLS 单字也不改的: {len(unchanged)}")

    mono, oneway = [], []   # mono = 繁简同形；oneway = 一对多保守
    for c, n in unchanged.items():
        if to_trad(c) != c:
            oneway.append((c, n, to_trad(c)))
        else:
            mono.append((c, n))

    print()
    print(f"  · 繁简同形（①②都不变）: {len(mono)} 个 —— 无需处理")
    mono_s = sorted(mono, key=lambda kv: -kv[1])
    print(f"    {''.join(c for c, _ in mono_s)}")
    print()
    print(f"  · ★ 一对多保守项（①不变②变）: {len(oneway)} 个 —— NLS 的真漏")
    ow = sorted(oneway, key=lambda kv: -kv[1])
    for c, n, t in ow:
        print(f"    {c}({n}) → NLS 认为它属于 {t}", end="")
        print("  ★ 高频，落在判据敏感位的风险高" if n >= 5 else "")

    print()
    if oneway:
        chars = "".join(c for c, _, _ in ow)
        print(f"  全部漏字字符: {chars}")
        print()
        print("  检验它们在繁体数据集里是否出现在判据敏感词组中：")
        sensitive = ["退後", "之後", "於我", "對於", "乾脆", "餅乾", "牠們"]
        for phrase in sensitive:
            hit = sum(1 for r in rows if phrase in r["ticket"]["ticket_text"])
            if hit:
                print(f"    {phrase}: {hit} 条")


if __name__ == "__main__":
    main()
