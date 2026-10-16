"""从 Windows NLS 生成判据用的繁→简映射表（固化成纯 Python 数据）。

为什么不直接运行时调 NLS
------------------------
NLS 是 Windows 专有的。若判据在运行时调它，同一个输入在 Windows 和 Linux 上
会得到不同结论 —— 这对一个"判据必须可复现"的项目是不能接受的。
所以这里把 NLS **用一次**：让它把答案说出来，固化成静态表。

这样做的两层好处：
  ① 表是**独立来源**生成的，不是我手写的 —— 手写表的正确性由我负责，
     NLS 表的正确性由微软的映射表负责，我只是把它抄下来；
  ② 表覆盖什么、不覆盖什么**可以逐条审计**（见下），而运行时调用是个黑盒。

覆盖范围的选取原则
------------------
归一化的唯一用途是"让判据看穿字形"，所以表只需要覆盖**判据词典里出现过的字**。
不在词典里的字（如 `後`）转不转都不影响任何判定，收进来只会让表变大、
让"这张表为什么有这一条"变得说不清。

生成后由 `_nlsdict.py` 独立验证：词典 110 个汉字中「转不回来」的必须是 0 个。
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


def main() -> None:
    pats = [
        corpus._REQUEST_RE,
        corpus._DEFECT_RE,
        corpus._PRE_RECEIPT_RE,
        corpus._POST_RECEIPT_RE,
    ]
    chars = sorted({c for p in pats for c in p.pattern if CJK.match(c)})
    print(f"判据词典汉字: {len(chars)} 个")

    pairs: list[tuple[str, str]] = []
    for c in chars:
        t = conv(c, LCMAP_TRADITIONAL_CHINESE)
        if t == c:
            continue
        # 只收「繁体→简体」能往返的，保证表是可逆的单字映射
        if conv(t, LCMAP_SIMPLIFIED_CHINESE) == c:
            pairs.append((t, c))
        else:
            print(f"  ★ 跳过（往返失败）: {c} ↔ {t}")

    print(f"异形字对: {len(pairs)} 个")
    print()
    print("生成的表内容：")
    # 按繁体字的 Unicode 码位排序，保证生成结果稳定（不受 dict 顺序影响）
    for t, s in sorted(pairs):
        print(f"    {t} → {s}")
    print()

    # 输出可直接粘贴的 Python 字面量
    print("=" * 78)
    print("Python 字面量（可直接贴进 textnorm.py）")
    print("=" * 78)
    body = "".join(f'    "{t}": "{s}",  # ' for t, s in sorted(pairs))
    print(body)


if __name__ == "__main__":
    main()
