"""补完 `_adddefect35b.py` 剩下的两件事（README 与字符类已完成，脚本断在第 3 步）。

1. `_readmecheck.py` 的 `EXPECTED_DEFECTS = 34` → 35
2. `交付总览.md`：缺陷节标题 三十四个 → 三十五个 + 清单追加一行

幂等：两边都已改就直接退出。

用法：python _adddefect35c.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent
CHECK = ROOT / "_readmecheck.py"
OVERVIEW = ROOT / "交付总览.md"


def sub(text: str, old: str, new: str, where: str, count: int = 1) -> str:
    n = text.count(old)
    assert n == count, f"{where}：期望命中 {count} 次，实际 {n} 次"
    return text.replace(old, new)


def main() -> int:
    c = CHECK.read_text(encoding="utf-8")
    if "EXPECTED_DEFECTS = 35" in c:
        print("_readmecheck.py 已是 35，跳过")
    else:
        c = sub(c, "EXPECTED_DEFECTS = 34", "EXPECTED_DEFECTS = 35", "计数")
        CHECK.write_text(c, encoding="utf-8")
        print("_readmecheck.py 计数 34→35")

    o = OVERVIEW.read_text(encoding="utf-8")
    if "判据的依据由被审对象自己书写" in o:
        print("交付总览.md 已含缺陷 ㉟ 清单行，跳过")
        return 0
    o = sub(o, "#### ② 三十四个真实缺陷", "#### ② 三十五个真实缺陷", "交付总览 缺陷节标题")
    o = sub(
        o,
        "过程探针全都假设 agent 至少查过",
        "过程探针全都假设 agent 至少查过 / 判据的依据由被审对象自己书写",
        "交付总览 缺陷清单",
    )
    OVERVIEW.write_text(o, encoding="utf-8")
    print("交付总览.md 已同步")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
