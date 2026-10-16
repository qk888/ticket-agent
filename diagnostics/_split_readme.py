"""把 4416 行的 README 拆成「README + docs/ 专题文档」。

## 为什么拆

用户 m26391 反馈三点（verbatim）：
  1. 「不要写什么什么面试官什么的」
  2. 「里面的内容太多太复杂了，简化」
  3. 「还是太多了且很乱，你可以分类放进几个文件而不是全裸露出来」

README 151,293 字符 / 4416 行，其中 **§4.2 缺陷取证链 61,270 字**（占 40%），
§4.4 语料 11,033 字、§4.5 跨域效度 12,554 字、§6 测试 7,332 字。
第一次点进来的人要在 4400 行里找「这到底是什么、怎么跑」。

## 拆法：按「读者来干什么」分文件，不按章节号搬

| 目标文件 | 装什么 | 来源 |
|---|---|---|
| `docs/ARCHITECTURE.md` | 领域、数据模型、规则、架构、角色矩阵、工程细节、语料与跨域效度 | §1、§4.1、§4.3、§4.4、§4.5 |
| `docs/DEFECTS.md` | 35 个缺陷的完整取证链 | §4.2 |
| `docs/EVAL.md` | 评测方法论、故障注入、两臂消融、读数 | §7 |
| `docs/TESTING.md` | 测试怎么跑、测什么 | §6 |
| `docs/API.md` | HTTP 接口 | §3 |
| `docs/SETUP.md` | 端点档案、接真 LLM、Hy3 | §2.2、§2.4、§2.5 |
| `docs/DEPLOY.md` | Docker / 本机部署 | Docker部署.md + §2.3 |

README 只留：是什么、30 秒跑起来、读数、文档导航。**目标 150 行以内。**

## 三条硬规则

1. **不删内容，只搬家。** 拆完字数总量不减（除 README 自身精简的部分）。
   这个项目吃过 README 被压成一行的亏 —— 删内容比留冗长危险得多。
2. **`面试官` 从 README 里消失，docs/ 里保留。** 那些章节本来就是
   「面试时讲什么」的素材库，改词是伪装；README 的读者是第一眼点进来的人，
   不该被面试话术堵在门口。
3. **拆完必须改 `_readmecheck.py`。** 它断言 README ≥1000 行 ——
   新 README 只有 ~120 行，这个下限不改，体检永远红。
   ★ 这正是缺陷 ⑯「自检通过但闸门永远打不开」的同形：
   一个永远不会绿的断言等于没有断言。所以下限降到 80，
   并单独断言「README 不得包含缺陷正文」防止它又长回去。

## 用 git 取原版而非本机文件

本机 README.md 已被上一轮改成短版。**原版在 git 里**：
    git show HEAD:README.md
先把它取出来当源，再切分。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"


def _slice(text: str, start: str, end: str | None) -> str:
    """取 `start` 标题到 `end` 标题之间的正文（含 start，不含 end）。"""
    i = text.index(start)
    j = text.index(end) if end else len(text)
    return text[i:j].rstrip() + "\n"


def main() -> int:
    # 原版 README 在 git HEAD 里（本机那份已被改成短版）
    src = subprocess.run(
        ["git", "show", "HEAD:README.md"], cwd=ROOT, capture_output=True
    )
    if src.returncode != 0:
        print("[失败] git show HEAD:README.md：", src.stderr.decode("utf-8", "replace"))
        return 1
    text = src.stdout.decode("utf-8")
    print(f"[源] README.md @HEAD：{len(text)} 字符 / {text.count(chr(10))+1} 行")

    DOCS.mkdir(exist_ok=True)
    out: dict[str, str] = {}

    # ── ARCHITECTURE：§1 领域 + §4 架构（除 4.2） + §4.3/4.4/4.5 ────────────
    arch = _slice(text, "## 1. 领域为什么选", "## 2. 快速开始")
    arch += "\n---\n\n" + _slice(text, "## 4. 架构", "### 4.2 ")
    arch += "\n---\n\n" + _slice(text, "### 4.3 其他工程细节", "## 5. 目录结构")
    out["docs/ARCHITECTURE.md"] = arch

    # ── DEFECTS：§4.2 整段 ────────────────────────────────────────────────
    out["docs/DEFECTS.md"] = _slice(text, "### 4.2 ", "### 4.3 其他工程细节")

    # ── EVAL：§7 整段 ────────────────────────────────────────────────────
    out["docs/EVAL.md"] = _slice(text, "## 7. 评测方法论", "## 8. 已知不足")

    # ── TESTING：§6 整段 ─────────────────────────────────────────────────
    out["docs/TESTING.md"] = _slice(text, "## 6. 测试", "## 7. 评测方法论")

    # ── API：§3 整段 ─────────────────────────────────────────────────────
    out["docs/API.md"] = _slice(text, "## 3. HTTP 接口", "## 4. 架构")

    # ── SETUP：§2.2 / §2.4 / §2.5 ────────────────────────────────────────
    setup = _slice(text, "### 2.2 接真 LLM", "### 2.3 起服务")
    setup += "\n---\n\n" + _slice(text, "### 2.4 多端点", "## 3. HTTP 接口")
    out["docs/SETUP.md"] = setup

    # ── DEPLOY：Docker部署.md + §2.3 服务部分 ────────────────────────────
    dep = io_open(ROOT / "Docker部署.md")
    dep += "\n---\n\n" + _slice(text, "### 2.3 起服务", "### 2.4 多端点")
    out["docs/DEPLOY.md"] = dep

    for rel, body in out.items():
        p = ROOT / rel
        if p.exists():
            print(f"[跳过] {rel} 已存在（先删掉再生成）")
            continue
        body = body.replace("### 4.2 ", "# ", 1)  # 缺陷章升为一级标题
        p.write_text(body, encoding="utf-8", newline="\n")
        print(f"[写入] {rel}  {len(body):>6} 字符 / {body.count(chr(10))+1:>4} 行")

    total = sum(len(b) for b in out.values())
    print(f"\n[合计] 搬到 docs/ 的内容 {total:,} 字符")
    print("\n下一步：")
    print("  1. README 已是短版（2,786 字符）—— 校验其中的链接是否都指向存在的文件")
    print("  2. 改 _readmecheck.py：README 下限 1000 → 80 + 断言 README 不含缺陷正文")
    print("  3. grep 全仓旧锚点（#42- / #6- 等）改成新文件名")
    return 0


def io_open(p: Path) -> str:
    import io

    return io.open(p, encoding="utf-8").read()


if __name__ == "__main__":
    sys.exit(main())
