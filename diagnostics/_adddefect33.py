"""把缺陷 ㉝ 写进 README §4.2 + 交付总览，并同步所有计数。

幂等性：先 assert 每个 old 命中次数，再替换；跑完查残留。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
README = ROOT / "README.md"
OVERVIEW = ROOT / "交付总览.md"
CHECK = ROOT / "_readmecheck.py"


def sub(text: str, old: str, new: str, where: str) -> str:
    n = text.count(old)
    assert n == 1, f"{where}：期望命中 1 次，实际 {n} 次 —— {old[:60]!r}"
    return text.replace(old, new)


def resub(text: str, pattern: str, repl: str, where: str) -> str:
    n = len(re.findall(pattern, text))
    assert n == 1, f"{where}：正则期望命中 1 次，实际 {n} 次 —— {pattern}"
    return re.sub(pattern, repl, text)


readme = README.read_text(encoding="utf-8")

# --- 1. §4.2 标题：三十二 → 三十三 ---
# ★ 幂等：README 可能已被上一次跑改过。标题只认两种合法状态，其余一律报错。
if "### 4.2 三十三个真实缺陷（面试时讲这个比讲架构图有用）" in readme:
    print("README §4.2 标题已是三十三个（幂等跳过）")
elif "### 4.2 三十二个真实缺陷（面试时讲这个比讲架构图有用）" in readme:
    readme = sub(
        readme,
        "### 4.2 三十二个真实缺陷（面试时讲这个比讲架构图有用）",
        "### 4.2 三十三个真实缺陷（面试时讲这个比讲架构图有用）",
        "README §4.2 标题",
    )
else:
    raise AssertionError("README §4.2 标题既不是三十二个也不是三十三个 —— 先查清楚再跑")

# --- 3. 正文计数（命中数先断言）---
# 注意：README 里 "第十七个/第二十个/第三十一个" 这类是**第 N 个缺陷的序号**，不是总数，不能动。
for old, new in [
    ("这是全部三十一个里我花时间最多的一个", "这是全部三十三个里我花时间最多的一个"),
    ("这是三十一个里最应该怕的一个", "这是三十三个里最应该怕的一个"),
]:
    if old in readme:
        readme = sub(readme, old, new, "README 正文计数")
    else:
        assert new in readme, f"README 正文计数：{old!r} 不在，{new!r} 也不在"
readme = readme.replace("#42-三十一个真实缺陷", "#42-三十三个真实缺陷")
readme = readme.replace("#42-十九个真实缺陷", "#42-三十三个真实缺陷")

# --- 4. 追加缺陷 ㉝ 正文（在 ㉜ 段之后、## 5. 之前）---
DEFECT33 = """
---

**缺陷 ㉝：售后记录的"删除维度"与"幂等维度"不是同一个** —— 千级 T1 那 8 条 R10 失败的真正根因。

- **现象**：T1 千级跑批（605 case / 1000 客户 / seed 7）里有 8 条 R10 失败，形态是
  `after_sales: 期望 [('compensate','approved',5.0)]，实际 [('compensate','approved',5.0), ('compensate','approved',5.0)]` ——
  同一条 case 出现两行**键完全相同**的售后记录。而 `submit_after_sales_action` 只被调用了 1 次。
- **取证**（四份诊断合成一条链，每份只回答一个问题）：
  1. `_r10dup.py`：重复行的键逐字符相同、submit 调用计数为 1 → **不是 agent 重复调用**；
  2. `_r10root2.py`：跑完 after=1，手动 `apply_snapshot` 之后 → 0 行 → **重置本身能删干净**；
  3. `_r10dup3.py`：single 跑完 0→1，紧接着 multi 跑同一条，before=1 而 after 仍=1 → **写入侧幂等生效**；
  4. `_r10root3.py`：15 条 R10:compensate 一口气顺序跑，全部 before=0、db_rows≤1、15/15 passed →
     **顺序跑复现不出来**。
  → 结论：重复行只在"同一条 case 被跑**第二次**"时出现。T1 用的是 `_resume.py` 断点续跑，
  同一条 case 重启后被跑第二次，而第一次写入的行还躺在库里。
- **根因**：两个维度用了不同的主语。
  - **删**：`src/ticket_agent/db/snapshot.py:377` 的 `apply_snapshot` 按 **order** 删
    （`repo.list_after_sales_of_order(order.id)`）；
  - **键**：`src/ticket_agent/tools/registry.py` 的 `submit_after_sales_action` 幂等键 =
    `md5(tenant:ticket:order:kind:amount)` —— 维度是 ticket+order，**且不含 cause**。
  两个后果，方向相反：① 同订单下**另一张工单**刚写的行会被 reset 顺手删掉 →
  那张工单的终态少一条本该有的记录；② 同工单两笔"同 kind 同金额、**原因不同**"的售后
  算出同一个键 → 第二笔被幂等挡下，而业务上它们是两笔独立赔付。
  这是缺陷 ㉓（"参数传不进 ≡ 规则没写"）的同形：**同一个概念在两个地方用了两个名字/两个维度**。
- **修法**：
  - `db/session.py` 新增 `list_after_sales_of_ticket(ticket_id)`，`apply_snapshot` 的
    after_sales 重建块改按 **ticket** 删 —— 删除维度与幂等维度对齐；
  - `tools/registry.py` 抽出 `_after_sales_key(tenant_id, ticket_id, order_id, kind, amount, cause)`，
    cause 进键；
  - `tests/test_defect33.py` 三条钉住性质（A 按 ticket 删不碰同订单其他工单 /
    B 幂等键含 cause / C reset 后 after_sales 与快照逐条相等）→ 2 passed, 1 skipped。
- **那条 skipped 比通过的更值得说**：A 用例要"同一订单被两张工单共用"这个形状，
  而实测千级库里**被多张工单共用的订单数 = 0**（orders 2353 / tickets 597 / tickets with order_id 438）——
  当前造数策略根本不产这个形状，所以 `_d33.py` 正向复现脚本也只能打印"触发条件不成立"。
  **触发条件不成立 ≠ 缺陷不存在**：代码路径在那儿，只要哪天造数策略一变（比如支持
  "一单多诉求"）它就会开始咬人，而那时报表上看到的是"某张工单少了一条售后记录"，
  没有任何东西会指向 reset 按 order 删。
  这条与"0 条 case 的规则和从不犯错长得一模一样"是同一件事的两面。

## 5. 目录结构
"""
if "**缺陷 ㉝：" in readme:
    print("缺陷 ㉝ 正文已在（幂等跳过）")
else:
    readme = sub(
        readme,
        "\n## 5. 目录结构\n",
        DEFECT33,
        "README §4.2 追加缺陷 ㉝",
    )

# --- 5. 测试文件数 + passed 数 ---
if "# 234 passed" not in readme:
    readme = sub(readme, "# 231 passed", "# 234 passed", "README 测试 passed 数")
readme = readme.replace("十二个测试文件", "十三个测试文件")

README.write_text(readme, encoding="utf-8")
print(f"README.md {len(readme)} 字符")

# --- 6. _readmecheck.py（已单独同步；字符类实测是 [①-⑳㉑-㉜] 不是 ㉙-㉜）---
chk = CHECK.read_text(encoding="utf-8")
assert "EXPECTED_DEFECTS = 33" in chk, "_readmecheck.py 缺陷数未同步"
assert "[①-⑳㉑-㉝]" in chk, "_readmecheck.py 字符类未同步"
print("_readmecheck.py 已同步")

# --- 7. 交付总览.md ---
ov = OVERVIEW.read_text(encoding="utf-8")
if "#### ② 三十三个真实缺陷" not in ov:
    ov = sub(
        ov,
        "#### ② 三十二个真实缺陷（面试时讲这个比讲架构图有用）",
        "#### ② 三十三个真实缺陷（面试时讲这个比讲架构图有用）",
        "交付总览 §② 标题",
    )
ov = ov.replace("完整三十二个：", "完整三十三个：")
if "售后记录的删除维度与幂等维度不是同一个" not in ov:
    ov = sub(
        ov,
        "/ 过程探针全都假设 agent 至少查过",
        "/ 过程探针全都假设 agent 至少查过 / 售后记录的删除维度与幂等维度不是同一个",
        "交付总览 缺陷全名单",
    )
ov = ov.replace("| 缺陷取证链 | 32 条 |", "| 缺陷取证链 | 33 条 |")
ov = ov.replace("**32 条缺陷取证链**", "**33 条缺陷取证链**")
ov = ov.replace("231 passed", "234 passed")
OVERVIEW.write_text(ov, encoding="utf-8")
print(f"交付总览.md {len(ov)} 字符")

# --- 8. 自查残留 ---
for name, text in (("README.md", readme), ("交付总览.md", ov), ("_readmecheck.py", chk)):
    for bad in ("三十二个真实缺陷", "32 条缺陷取证链", "# 231 passed"):
        if bad in text:
            print(f"✗ {name} 仍有残留：{bad}")
            sys.exit(1)
print("✓ 无残留")
