"""把缺陷 ㉟ 的正文插入 README §4.2 末尾（`_adddefect35.py` 断在找锚点上）。

为什么改写：原脚本假设 §4.2 里有 `<!-- DEFECT-TAIL -->` 锚点或"三个连续换行"
结尾，实际 §4.2 的结尾是两条换行后紧跟 `### 4.3`，`rfind("\n\n\n")` 返回 -1。
**先量清楚再插**：这次先把 §4.2 的边界打出来，按真实边界插。

锚点改用确定的事实：§4.2 的最后一条缺陷是 ㉛，插入点 = ㉛ 段结尾的
`加"只是多一个字段"的东西时，先问一句：**它会不会消耗主随机流？**` 之后。
脚本先 assert 这句只出现一次，再插。

同时做三件事（与原脚本同一批）：
  1. `### 4.2 三十四个真实缺陷` → `三十五个`
  2. `_readmecheck.py`：EXPECTED_DEFECTS 34→35、字符类扩到 ㉝→㉟
  3. `交付总览.md`：缺陷节标题 + 清单追加一行
幂等：README 已含"缺陷 ㉟"就整体退出。

用法：python _adddefect35b.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent
README = ROOT / "README.md"
OVERVIEW = ROOT / "交付总览.md"
CHECK = ROOT / "_readmecheck.py"

MARK = "缺陷 ㉟"
ANCHOR = '加"只是多一个字段"的东西时，先问一句：**它会不会消耗主随机流？**'


def sub(text: str, old: str, new: str, where: str, count: int = 1) -> str:
    n = text.count(old)
    assert n == count, f"{where}：期望命中 {count} 次，实际 {n} 次"
    return text.replace(old, new)


BODY = """
---

**缺陷 ㉟：判据的依据是一个由被审对象自己书写的字段（两半，一个根因）**

**现象**：200 case 真模型那批，`single` 4 条 / `multi` 20 条 run 的
`verifier_passed=True` 而 `observed_action='none'`，且 diffs 明确要求写操作
（`ticket.status` 要离开 `open`，或 `after_sales` 要有记录）。看起来是
"agent 什么都没做却通过了验收"。

**定位（六步，每一步只答一问，判据都可复现）**：

| 脚本 | 量什么 | 结论 |
|---|---|---|
| `_vnone.py` | 怎么定义"可疑 run" | single 4 / multi 20 条 B 类（另有 56 条 A 类是 `ambiguous`，放行正确） |
| `_vwhy5.py` | golden 到底要求什么 | **24/24 条都要求离开 open 或要求写** —— 只看 diffs 期望侧，不重建数据 |
| `_vwhy6.py` | `_summarize_action` 认哪些工具 | 只认 4 个名字；`submit_after_sales_action` **不在列**；multi 有 11 条 `write_calls=1` 而 `after_sales: 实际 []` |
| `_vwhy8.py` | 那一次写到底是什么 | 审计日志里 `submit_after_sales_action.urge ok=True args={…'action_id': 1}`，而标签是 `resolve`（来自 close_ticket） |
| `_vwhy10.py` | 重建是否确定 | `seed_all(1000,11)` + `build_eval_cases(200,7)` 连跑两次**逐位相同** |
| `_vwhy11.py` | 同批 case 上 scripted 基线 | 6 条全部办成、`verifier=True` → **verifier 判据本身没放过它们** |

**第一半（报表汇总）**：七个售后动作（赔付/补发/换货/价保/开票/改址/催单）
走的是**同一个** `submit_after_sales_action`，只有 `args.kind` 不同；而
`_summarize_action` 按工具名映射，只认退款线那四个。后果是 agent 真写了
（审计与库里都有记录），报表写 `observed_action='none'` —— 排查时会去查
agent，而真相是汇总函数少认一个工具。200 case 实测 11 条
`write_calls=1` 而 `after_sales: 实际 []`。

**第二半（verifier 判据）**：

```python
needs_clarification = basis == "ambiguous" or bool(ticket_snap.get("pending_question"))
```

`basis` 来自 `classify_order_link` 纯函数，是**输入侧**事实，agent 改不了；
`pending_question` 是 agent 用 `ask_customer` **写**进去的，属于**被审对象
自己的产出**。缺陷 ⑲ 修的是"机器断言不能建立在被审对象的自我陈述上"，
#2 把依据从散文换成了结构化字段 —— 但字段仍由被审对象书写：
agent 问一句（哪怕问的是一个 golden 并不需要的问题）就给自己开一张免检券。
**同一个病根换了件衣服。**

**修法**：

1. `_AFTER_SALES_KIND_LABEL` 提到模块级，八个 kind 一张表；
   `_summarize_action` 与 `_expected_action` 共用它 —— 两处一张表，
   加 kind 时不会只改一边（缺陷 ㉓"参数传进不去 ≡ 规则没写"的同形）。
   `ok=False` 的提交仍然不算动作（缺陷 ⑫ 的修法不丢）。
2. `verifier_node(state, *, should_clarify=None)`：`pending_question` 只在
   **这条 case 的 golden 要求先澄清**时才是有效依据。可复现的判据在 case 层
   （`_case_should_clarify`：`condition` 以 `need_clarification` 结尾，或
   `turn2_state` 非空），agent 没有任何路径能改它。`asked` 但 golden 不要求
   → 单独记一条可读的 problem，不是只有 `passed=False`。
3. 用**闭包**把 `should_clarify` 绑进 verifier 节点，不用全局变量 ——
   全局在本项目已经惹过 ㉛（辅助随机流不按 seed 重置）和 ⑮（`.env` 写进
   `os.environ` 污染进程）两次祸，不再添第三种。

**为什么"问过客户"不能替代"办成"**：政策里有六处会返回
`need_clarification`，那些 case 第一轮的正解确实是"先问清"。但第二轮客户
答复到达时 `apply_customer_reply` 会把缺的字段补上、规则重算，同一个 case
必须能办成 —— 所以"问过"只解释第一轮为什么 open，不解释终态为什么还 open。

**回归**：`tests/test_defect35.py` 26 条（八个 kind 全覆盖 + 阴性对照 +
`ok=False` 不算动作 + 两半同一张表 + case 层判据参数化）；
`test_clarification_loop.py` 的参数化判据测试从"读源码断字面量"升级为
"直接调 `verifier_node` 喂五种组合"（原实现把字面量钉死，㉟ 一收紧就红 ——
红得对，但它测的是字面不是语义）。全量 **263 passed, 1 skipped**。
"""


def main() -> int:
    r = README.read_text(encoding="utf-8")
    if MARK in r:
        print("README 已含缺陷 ㉟，跳过（幂等）")
        return 0

    assert r.count(ANCHOR) == 1, f"插入锚点命中 {r.count(ANCHOR)} 次，应为 1"
    r = sub(r, "### 4.2 三十四个真实缺陷", "### 4.2 三十五个真实缺陷", "README §4.2 标题")
    r = r.replace(ANCHOR, ANCHOR + "\n" + BODY)
    README.write_text(r, encoding="utf-8")
    print(f"README.md 改完（{len(r)} 字符）")

    c = CHECK.read_text(encoding="utf-8")
    c = sub(c, "EXPECTED_DEFECTS = 34", "EXPECTED_DEFECTS = 35", "_readmecheck 计数")
    old_cls = "[①-⑳㉑-㉜]"
    if old_cls not in c:
        old_cls = "[①-⑳㉑-㉝]"
    c = sub(c, old_cls, "[①-⑳㉑-㉟]", "_readmecheck 字符类")
    CHECK.write_text(c, encoding="utf-8")
    print("_readmecheck.py 计数 34→35")

    o = OVERVIEW.read_text(encoding="utf-8")
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
