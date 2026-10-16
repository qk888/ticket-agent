"""README / docs 等文档的结构体检 —— 自己写坏了自己看得见。

起因是那次把 README 压成一行的 Set-Content 事故：
文件字节数没变、内容也不缺，但换行全没了，靠人眼看不出。
所以每次改完文档都跑一遍这个，比"我记得没写坏"可靠。

## 2026-10 拆分后的结构变化

用户反馈「内容太多太复杂、很乱、分类放进几个文件」。原 README 4416 行 / 151,293 字
拆成「README 短版 + docs/ 七个专题文档」。随之而来的三处调整：

1. **README 的下限从 1000 行改成 80 行。** 原下限是为抓「整份压成一行」设的，
   拆完之后 README 只剩 ~120 行，不改这个下限，体检会**永远红** ——
   ★ 这正是缺陷 ⑯「自检通过但闸门永远打不开」的同形：
   一个永远不会绿的断言等于没有断言。
2. **新增反向断言：README 不得包含缺陷取证正文。** 防止它又长回去。
   判据不是「不许出现『缺陷』两个字」，而是**不许出现缺陷条目的正文标记**
   （`**缺陷 ㉟：` 这种带冒号的标题行）—— README 只该在导航表里提一句链接。
3. **缺陷数断言移到 docs/DEFECTS.md。** 35 个缺陷的取证链现在住在那儿，
   在 README 里数缺陷标记只会永远得到 0。
   ★ 这是缺陷 ⑧「能力只存在于声明里」的镜像：
   断言检查的文件已经不是承载那个内容的文件了。

DOCS 里每个文件都有各自的下限；加一份新文档就加一行。
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

DEFECTS_DOC = "docs/DEFECTS.md"


def _cn_num(n: int) -> str:
    """1–99 的汉字写法。只覆盖标题用得上的范围，不做通用转换。"""
    digits = "零一二三四五六七八九"
    if n < 10:
        return digits[n]
    if n < 20:
        return "十" + (digits[n % 10] if n % 10 else "")
    return digits[n // 10] + "十" + (digits[n % 10] if n % 10 else "")


DEFECT_SECTION_TITLE = f"# {_cn_num(EXPECTED_DEFECTS)}个真实缺陷"


# ★ 文档清单：文件名 → 最少行数（低于它就怀疑换行被压平了）。
#   最少行数是"这份文档正常状态下该有的量级"，不是精确值 —— 它只用来抓
#   "整份被压成一行"这种灾难，不参与日常的内容校核。
#   ★ 下限值本身要跟着内容走：拆文件那一轮我把四份专题文档重写了，
#     下限还按拆之前的行数设，于是四份同时报红 —— 又是一个
#     "断言检查的文件已经不是承载那个内容的文件了"（缺陷 ⑧ 的镜像）。
#     现在下限按重写后的实际行数下浮一档设，并留了 20% 余量。
DOCS: dict[str, int] = {
    "README.md": 180,         # 导航版但该有的都在：是什么/架构/评测/读数/缺陷精选/不足/目录
    "docs/DEFECTS.md": 1500,  # 35 个缺陷的完整取证链，本项目最值钱的部分
    "docs/ARCHITECTURE.md": 150,
    "docs/EVAL.md": 120,
    "docs/TESTING.md": 45,
    "docs/API.md": 45,
    "docs/SETUP.md": 100,
    "docs/DEPLOY.md": 115,
    "docs/OVERVIEW.md": 200,     # 交接说明（原 交付总览.md，已移入 docs/）
    "docs/RUNBOOK.md": 150,      # 手动启动（原 手动启动.md）
    "docs/ENDPOINTS.md": 80,     # 接哪个模型跑（原 接哪个模型跑.md）
    "docs/BENCHMARKS.md": 60,    # 跑批结果汇总（原 数据集跑批结果汇总.md）
    "web/index.html": 300,   # 控制台：单文件前端，压平了同样看不出来
    "diagnostics/README.md": 30,   # 一次性排查脚本的目录说明：短，但同样怕被压平
}


# README 里出现这个形态 = 缺陷取证正文又长回 README 了。
# 判据用"**缺陷 X："这种带序号的正文标题行，不用"缺陷"两个字 ——
# README 的导航表里本来就要提这个词。
DEFECT_BODY_IN_README = re.compile(r"(?m)^#{1,6}\s*\*\*缺陷 [①-⑳㉑-㉟]+：")
DEFECT_MARK = re.compile(r"\*\*缺陷 ([①-⑳㉑-㉟]+)：")


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
        # 反向断言：README 是导航版，不该再长出缺陷正文
        leak = DEFECT_BODY_IN_README.findall(text)
        if leak:
            out.append(
                f"README 里出现了 {len(leak)} 处缺陷正文标题（如 {leak[0][:30]}…）—— "
                "取证链该在 docs/DEFECTS.md，README 只留链接"
            )
        # 死链：README 里的相对链接必须真的存在
        base = os.path.dirname(path) or "."
        for m in re.findall(r"\]\(([^)#]+\.md)\)", text):
            if m.startswith("http"):
                continue
            if not os.path.exists(os.path.normpath(os.path.join(base, m))):
                out.append(f"README 死链：{m}")
        # 标题里不该夹着标题标记（曾经把 `### 4.3` 吞掉过一次）
        for h in headings:
            if h.strip().startswith("#"):
                out.append(f"疑似被吞的标题：{h!r}")

    if path == DEFECTS_DOC:
        defects = sorted(set(DEFECT_MARK.findall(text)))
        print(f"  缺陷标记：{''.join(defects)}（{len(defects)} 个）")
        if len(defects) != EXPECTED_DEFECTS:
            out.append(
                f"缺陷标记应有 {EXPECTED_DEFECTS} 个，实际 {len(defects)} 个 —— "
                "标题说几个就要真有几个"
            )
        if DEFECT_SECTION_TITLE not in text:
            out.append(f"{DEFECTS_DOC} 一级标题与缺陷数不一致（期望 {DEFECT_SECTION_TITLE}）")
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
