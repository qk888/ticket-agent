"""决定性问题：NLS 的漏字，是否落在本项目判据的词典里。

为什么这是决定性的
------------------
前面几份探针已经把事实摆清了：
  · NLS（Windows LCMapStringW）是一把**独立于手工表**的尺子，能转 `貨/還/籤/質/換`；
  · 它对 6,638 条简体语料里有 22 条做了改动（那些条混了繁体字），但**判据结论零变化**；
  · 它有真漏：`後/於/乾/牠/癡` 这几对字它双向都不转（表残缺，不是保守）。

那么"要不要给判据加归一化"就归结为一个可算的问题：

    本项目的四条判据正则，其词典里的每个字，NLS 转不转得动？

  · 若**全部转得动** → 加归一化是纯增益：繁体输入从此判得对，简体输入零影响。
  · 若**有字转不动** → 那些字会成为残留，必须配一张人工补充表，
    而补充表要覆盖的是**有限且可枚举**的几个字，不再是我一开始写的 84 条大表。

这是一张可以打勾的表，不是一个判断。做法：把四条正则的词典**拆成单字**，
每个字分别问 NLS「繁体形式你转得动吗」，然后把结果分成三档：

    同形   该字繁简同形（如 `退`/`款`/`瑕`），不存在字形问题
    可转   该字的繁体形式 NLS 能转回来（如 貨→货）
    ★ 不可转 该字的繁体形式 NLS 转不动 —— 需要进人工补充表
"""

import ctypes
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


def conv(text: str, flag: int) -> str:
    n = _kernel32.LCMapStringW(LCID_ZH_CN, flag, text, -1, None, 0)
    if n == 0:
        raise OSError(ctypes.get_last_error())
    buf = ctypes.create_unicode_buffer(n)
    if _kernel32.LCMapStringW(LCID_ZH_CN, flag, text, -1, buf, n) == 0:
        raise OSError(ctypes.get_last_error())
    return buf.value


def to_sim(text: str) -> str:
    return conv(text, LCMAP_SIMPLIFIED_CHINESE)


def main() -> None:
    pats = {
        "_REQUEST_RE": corpus._REQUEST_RE,
        "_DEFECT_RE": corpus._DEFECT_RE,
        "_PRE_RECEIPT_RE": corpus._PRE_RECEIPT_RE,
        "_POST_RECEIPT_RE": corpus._POST_RECEIPT_RE,
    }

    print("=" * 78)
    print("① 每条正则的词典逐字检验（只测汉字，标点/英文跳过）")
    print("=" * 78)
    all_chars: set[str] = set()
    for pat in pats.values():
        all_chars |= {c for c in pat.pattern if CJK.match(c)}
    print(f"  四条正则的汉字并集: {len(all_chars)} 个")

    same, ok, bad = [], [], []
    for c in sorted(all_chars):
        # 1. 该字的繁体形式 NLS 认为是什么？
        t = conv(c, LCMAP_TRADITIONAL_CHINESE)
        # 2. 再把那个繁体形式转回来
        back = to_sim(t)
        if t == c:
            same.append(c)           # 繁简同形
        elif back == c:
            ok.append((c, t))        # 可转，往返成功
        else:
            bad.append((c, t, back))  # ★ 转不回来

    print(f"    同形      : {len(same)} 个 —— {''.join(same)}")
    print(f"    可转往返成功: {len(ok)} 个")
    print(f"    ★ 转不回来  : {len(bad)} 个")
    if bad:
        for c, t, back in bad:
            print(f"        {c} → 繁体 {t} → 转回 {back}   ★")

    print()
    print("=" * 78)
    print("② 反过来的检验（更贴近实战）：把每个词典词写成繁体，看判据还认不认得")
    print("=" * 78)
    # 从正则里抽出词条（按 | 切），转成繁体，再问归一化后能否匹配
    for name, pat in pats.items():
        words = [w for w in pat.pattern.split("|") if w and not w.startswith("(")]
        lost = []
        for w in words:
            trad = conv(w, LCMAP_TRADITIONAL_CHINESE)
            if trad == w:
                continue  # 繁简同形，无需检验
            norm = to_sim(trad)
            if norm != w:
                lost.append((w, trad, norm))
        print(f"  {name:<18} 词条 {len(words):>2} 个，繁简异形 "
              f"{sum(1 for w in words if conv(w, LCMAP_TRADITIONAL_CHINESE) != w):>2} 个，"
              f"★ 归一化后仍失配 {len(lost)} 个")
        for w, trad, norm in lost:
            print(f"      {w} → 繁 {trad} → 归一 {norm}   ★ 失配")

    print()
    print("=" * 78)
    print("③ 结论判据：NLS 单独够用吗")
    print("=" * 78)
    print(f"  判据词典里「转不回来」的字: {len(bad)} 个")
    if not bad:
        print("  → NLS 覆盖了本项目判据词典的全部异形字。")
        print("     归一化可以直接用 NLS，不需要人工补充表。")
    else:
        print(f"  → 需要人工补充表，且只需覆盖这 {len(bad)} 个字：")
        print(f"     {''.join(c for c, _, _ in bad)}")


if __name__ == "__main__":
    main()
