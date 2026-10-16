"""README 等文档的结构体检 —— 自己写坏了自己看得见。

起因是那次把 README 压成一行的 Set-Content 事故：
文件字节数没变、内容也不缺，但换行全没了，靠人眼看不出。
所以每次改完文档都跑一遍这个，比"我记得没写坏"可靠。

★ 被检文档清单在 `DOCS`。加一份新文档就加一行 ——
  那次事故对任何一份 md 都成立，不是 README 独有的。
"""

from __future__ import annotations

import io
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ★ 缺陷数只在这一处声明：断言用不到硬编码的数字，标题字符串也由它拼出来。
#   否则加一个缺陷就要改三个地方，而漏改一个的表现是"体检通过但 README 自相矛盾"。
#   ★ 这条原则我一开始自己就没守住：数字写在这里，标题字符串却另抄了一份
#   （"### 4.2 十二个真实缺陷"），于是加第十三个缺陷时，体检报的是
#   "§4.2 标题与缺陷数不一致" —— 而两处都是我手写的。现在标题由数字拼出来，
#   改一个数就够；改错一个数会当场红，而不是留到下一次加缺陷时才发现。
EXPECTED_DEFECTS = 35


def _cn_num(n: int) -> str:
    """1–99 的汉字写法。只覆盖标题用得上的范围，不做通用转换。"""
    digits = "零一二三四五六七八九"
    if n < 10:
        return digits[n]
    if n < 20:
        return "十" + (digits[n % 10] if n % 10 else "")
    return digits[n // 10] + "十" + (digits[n % 10] if n % 10 else "")


DEFECT_SECTION_TITLE = f"### 4.2 {_cn_num(EXPECTED_DEFECTS)}个真实缺陷"


# ★ 文档清单：文件名 → 最少行数（低于它就怀疑换行被压平了）。
#   最少行数是"这份文档正常状态下该有的量级"，不是精确值 —— 它只用来抓
#   "整份被压成一行"这种灾难，不参与日常的内容校核。
DOCS: dict[str, int] = {
    "README.md": 1000,
    "交付总览.md": 300,
    "手动启动.md": 200,
    "接哪个模型跑.md": 100,
    "web/index.html": 300,   # 控制台：单文件前端，压平了同样看不出来
}


def check_doc(path: str, min_lines: int, *, is_readme: bool) -> list[str]:
    """检查一份 markdown 的结构完整性，返回问题列表。"""
    import os

    if not os.path.exists(path):
        return [f"文档不存在：{path}"]

    text = io.open(path, encoding="utf-8").read()
    n_chars = len(text)
    n_lines = text.count("\n") + 1
    headings = re.findall(r"(?m)^#{2,5} (.*)$", text)
    fences = text.count("```")
    crlf = text.count("\r\n")

    tag = "" if is_readme else "  "
    print(f"{tag}{path}：字符 {n_chars} / 行 {n_lines} / 标题 {len(headings)} / "
          f"围栏 {fences}（{'偶数' if fences % 2 == 0 else '★奇数'}）/ CRLF {crlf}")

    out: list[str] = []
    if fences % 2 != 0:
        out.append(f"{path}：代码围栏未配对（{fences} 个，应为偶数）")
    if n_lines < min_lines:
        out.append(f"{path}：行数 {n_lines} 低于下限 {min_lines}，怀疑换行丢失")
    if crlf:
        out.append(f"{path}：有 {crlf} 个 CRLF 行，统一用 LF")
    if is_readme:
        defects = sorted(set(re.findall(r"\*\*缺陷 ([①-⑳㉑-㉟]+)：", text)))
        print(f"  缺陷标记：{''.join(defects)}（{len(defects)} 个）")
        if len(defects) != EXPECTED_DEFECTS:
            out.append(
                f"缺陷标记应有 {EXPECTED_DEFECTS} 个，实际 {len(defects)} 个 —— "
                "标题说几个就要真有几个"
            )
        if len(headings) < 30:
            out.append(f"标题数 {len(headings)} 偏少，怀疑结构被压平")
        if DEFECT_SECTION_TITLE not in text:
            out.append("§4.2 标题与缺陷数不一致")
        # 标题里不该夹着标题标记（曾经把 `### 4.3` 吞掉过一次）
        for h in headings:
            if h.strip().startswith("#"):
                out.append(f"疑似被吞的标题：{h!r}")
    return out


def main() -> int:
    out: list[str] = []
    for path, min_lines in DOCS.items():
        out += check_doc(path, min_lines, is_readme=(path == "README.md"))

    if out:
        print("\n★ 体检未通过：")
        for item in out:
            print(f"  - {item}")
        return 1
    print("\n✓ 结构体检通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
