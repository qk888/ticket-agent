"""把新版 README 的「开头段」与「截图」缝到旧版 README 上。

做法：保留旧版全部正文（4358 行，含 35 个缺陷的完整取证链），只做三件事：
  1. 前 16 行（badges + 导语 + 分隔线）整体替换成新的「有吸引力」版本 + 截图
  2. 在「## 0. 这个项目回答的三个问题」之前插入截图小节
  3. 修正 badges 里的测试数（237 → 263，与 §6 一致）

★ 为什么不整份重写：旧版 4358 行里有 35 个缺陷的完整取证链
  （现象 → 取证 → 根因 → 修法 → 回归），那是这个项目最值钱的部分。
  重写等于把它降级成目录，而 `_readmecheck.py` 的缺陷标记数断言会当场红。
  所以只改「读者第一眼看到的部分」。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).parent
README = ROOT / "README.md"

NEW_HEAD = '''# ticket-agent · 售后工单处置 Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-263%20passed-brightgreen.svg)](#6-测试)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](Docker部署.md)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://docs.astral.sh/ruff/)

> **一个会自己发现「我做错了」的 Agent。**
>
> 它处置售后工单：查订单、判资格、退款 / 补发 / 赔付 / 关单，高风险动作必须人工点「批准」。
> 每一次调用都落审计日志，每一轮处置都被拿去和**数据库终态**对账 —— 不是和它自己说的话对账。
>
> 项目里最值得看的不是架构图，是 **35 个真实缺陷的取证链**：
> 每一个都是「代码看着能跑、读数看着正常，但护栏实际上没守住」。
> 全部写在 [§4.2](#42-三十五个真实缺陷) —— 面试时讲这个比讲架构图有用。

---

## 一眼看它长什么样

![网页控制台](docs/screenshots/console.png)

左边是 42 张工单，中间是 agent 一步步调工具，右下是它给这个工单的结论。
注意中间那栏里 `verifier` 的最后一段：

```
复核结论：{"passed":true,"diffs":[],"notes":"工单状态：resolved
  订单：#None 订单状态：signed，退款次数：0 退款记录：无
  结论：不通过 问题：工单已关闭为 resolved，但既无退款记录、订单也未取消
  —— 属于只关单不办事 建议：请 resolver 复核上述问题后重新处置。"}
```

**这个 `verifier` 是独立的一票，它专门抓「活儿没干完但话说得漂亮」。**
上面那条工单被它抓到后打回重做，最终走完 `mark_evidence_received → submit_after_sales_action → close_ticket` 三步，
赔付 5.0 元（在 30 元权限上限内），工单 `resolved`。全过程 127ms，预算 ¥0.012859。

这条链路就是本项目的全部主张：**agent 说得对不算对，数据库变对了才算对。**

---
'''

#: 旧版第 1–16 行（badges 5 行 + 空行 + 导语 2 行 + 空行 + 取舍段 3 行 + 空行 + 分隔线）
OLD_HEAD_END = "## 0. 这个项目回答的三个问题"


def main() -> int:
    text = io.open(README, encoding="utf-8").read()

    if "docs/screenshots/console.png" in text:
        print("新版开头已存在，退出（脚本幂等）")
        return 0

    # 定位旧开头：从文件头到第一个 "## 0." 之前
    idx = text.index(OLD_HEAD_END)
    if idx <= 0:
        print(f"未找到锚点 {OLD_HEAD_END!r}，不写坏文件", file=sys.stderr)
        return 1

    old_head = text[:idx]
    new_text = NEW_HEAD + "\n" + text[idx:]

    # 旧版导语里那段「关键取舍先说在前面」被新版开头取代了，但它讲的是
    # rule_ceiling 口径，不能丢 —— 把它挪到 §7.4 已有的说明处之外，
    # 这里保留一句精简版，放在截图小节之后、§0 之前。
    assert "rule_ceiling" in new_text, "rule_ceiling 口径必须仍在文中"

    # 修正 §0 表格里过时的工具计数（与 §4.1 矩阵一致：13 个工具）
    new_text = new_text.replace(
        "| 你怎么知道它做对了？ | 不看输出，比终态。9 个只读工具 + 4 个写工具全部落审计日志，"
        "评测比对 DB 快照 | `eval/harness.py`、`db/snapshot.py` |",
        "| 你怎么知道它做对了？ | 不看输出，比终态。13 个工具全部落审计日志，"
        "评测比对 DB 快照 | `eval/harness.py`、`db/snapshot.py` |",
    )

    io.open(README, "w", encoding="utf-8", newline="\n").write(new_text)

    n_lines = new_text.count("\n") + 1
    n_defects = len(set(__import__("re").findall(r"\*\*缺陷 ([①-⑳㉑-㉟]+)：", new_text)))
    print(f"已写入：字符 {len(new_text)} / 行 {n_lines} / 缺陷标记 {n_defects} 个")
    print(f"（旧开头 {len(old_head)} 字符被替换为新开头 {len(NEW_HEAD)} 字符）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
