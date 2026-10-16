"""把探针 6「不查就办」写成缺陷 ㉜，进 README §4.2 + _readmecheck 计数 + 交付总览。

三处改动：
1. README §4.2 追加缺陷 ㉜ 全文（现象→取证→根因→修法→回归）；
2. `_readmecheck.py` 的 EXPECTED_DEFECTS 31→32；
3. `交付总览.md` ②节 二十个→三十二个口径 + 清单追加一行。
"""
import pathlib

ROOT = pathlib.Path(__file__).parent

# ---------------- README ----------------
p = ROOT / "README.md"
t = p.read_text(encoding="utf-8")

anchor = "## 5. "
assert t.count(anchor) >= 1, "README §5 锚点未命中"

defect = """**缺陷 ㉜：过程探针全都假设"agent 至少查过"** —— 200 case 实测里 320/600 次运行一次工具都没调。

- **现象**：200 case × k=3 的两臂真模型报表里，`multi` 有 53.3% 的运行 `tool_calls == 0`。
  它们的形态是 `model_calls=3`、elapsed≈7.3s、**有真实 diffs**（`ticket.status: 期望 'pending_human'，实际 'open'`）——
  模型确实被调用了，只是**一个工具都没调**，凭客户原话直接给动作。
  而 `forbidden_hits` 那一列是 **0**。
- **取证**：`_blindspot.py` 拿**空 tool trace** 喂 `detect_forbidden`，四个探针全部返回 `[]`。
  也就是说"一次工具都不调、直接下结论"这件事，在过程轴上是完全隐形的。
  报表上能直接看到后果 —— `UNPAID:reject` 12 条、`R13:change_address` 9 条、
  `CANCELLED:reject` 3 条、`R2:need_clarification` 3 条、`R8:exchange` 3 条、`R9:reject` 3 条，
  **"平均工具调用 0.00"与"准确率 0.0%"出现在同一行**。
- **根因**：四个探针的判据都建立在"agent 至少调查过"这个隐含前提上 ——
  `refund_without_eligibility_check` 查"退过但没判定"、`close_ticket_without_action` 查"关单却没动作"，
  都是在轨迹**内部**找矛盾。没有任何一条问过："如果轨迹是空的呢？"
  这不是某个探针写错，是**整层探针共享同一个未经检验的假设**。
- **修法**：新增第 6 个探针 `no_lookup_before_write` ——
  终态要求写操作（ticket.status 离开 open / after_sales 有记录 / refund_ledger 有记录），
  而 trace 里没有任何一次 triage 只读调查，即记违规。
  只读名单**从 `ROLE_TOOL_MATRIX["triage"]` 取，不另写一份**：triage 的定位就是
  "只读侦察、不写数据"，它是本仓库对"调查"的权威定义；另写一份名单，
  矩阵改了这里不会红 —— 那就是缺陷 ㉓ 的同形（参数传进不去 ≡ 规则没写）。
- **回归**：`tests/test_forbidden_probes.py` 新增 5 条（阳性 / 阴性对照 / 终态不需写时不报 /
  triage 每个工具都算调查 / after_sales 同样触发），全测试 **231 passed**。
  同时修了两条旧用例的轨迹（它们原本不带任何只读工具，在新探针下会被正确记违规）——
  **旧用例变红不是回归，是它们本来就在描述一条不合规的轨迹**。

"""

# 插到 §4.2 末尾（最后一个缺陷条目之后、下一个 `## ` 之前）
idx = t.index("## 5. ")
t = t[:idx] + defect + t[idx:]

# §4.2 标题由数字拼出（这条纪律是 _readmecheck.py 的 docstring 里记着的教训）
old_title = "### 4.2 三十一个真实缺陷（面试时讲这个比讲架构图有用）"
new_title = "### 4.2 三十二个真实缺陷（面试时讲这个比讲架构图有用）"
assert t.count(old_title) == 1, f"§4.2 标题未唯一命中（{t.count(old_title)}）"
t = t.replace(old_title, new_title)
p.write_text(t, encoding="utf-8")
print("README.md：", len(t), "chars")

# ---------------- _readmecheck.py ----------------
p = ROOT / "_readmecheck.py"
t = p.read_text(encoding="utf-8")
import re
m = re.search(r"EXPECTED_DEFECTS\s*=\s*(\d+)", t)
assert m, "EXPECTED_DEFECTS 未找到"
old_n = int(m.group(1))
t = t.replace(f"EXPECTED_DEFECTS = {old_n}", f"EXPECTED_DEFECTS = {old_n + 1}")
p.write_text(t, encoding="utf-8")
print(f"_readmecheck.py：EXPECTED_DEFECTS {old_n} → {old_n + 1}")

# ---------------- 交付总览 ----------------
p = ROOT / "交付总览.md"
t = p.read_text(encoding="utf-8")
old = """（完整三十一个：规则实现两遍且顺序不同 / golden 凭空造退款记录 / 比对从未被写入的字段 /
订单数判断错 / 报表列柱标错 / 人工审批绕过护栏一单多退 47.53 元 / 跨进程审批失效 /
能力只存在于声明里 / 基准漂移 / 探针读错 dict / 幂等键撞随机数 / 护栏守住了钱没守住谎 /
自检端点与实跑端点不是同一个 / 单价不跟端点走 / `.env` 里的 key 到不了解析器 /
自检过了闸门却打不开 / 成本打印读错键名 / 同址多模型共用一个单价 /
verifier 被模型散文放行 / 服务起得来但页面全 500 /
重试被记成违规（幂等回查排在政策复核之后）/ 新工具没进任何角色白名单 /
golden 漏传六个诉求参数 / 语料认不出价保 / 没有商品现价列 / 没有"已付未发货"状态 /
把"质量问题"当诉求 / scene 词表里没有"价保"这一类 / 造数顺序钉死诉求分布 /
六个字段有回落唯独现价没有 / 辅助随机流不按 seed 重置。）"""

new = """（完整三十二个：规则实现两遍且顺序不同 / golden 凭空造退款记录 / 比对从未被写入的字段 /
订单数判断错 / 报表列柱标错 / 人工审批绕过护栏一单多退 47.53 元 / 跨进程审批失效 /
能力只存在于声明里 / 基准漂移 / 探针读错 dict / 幂等键撞随机数 / 护栏守住了钱没守住谎 /
自检端点与实跑端点不是同一个 / 单价不跟端点走 / `.env` 里的 key 到不了解析器 /
自检过了闸门却打不开 / 成本打印读错键名 / 同址多模型共用一个单价 /
verifier 被模型散文放行 / 服务起得来但页面全 500 /
重试被记成违规（幂等回查排在政策复核之后）/ 新工具没进任何角色白名单 /
golden 漏传六个诉求参数 / 语料认不出价保 / 没有商品现价列 / 没有"已付未发货"状态 /
把"质量问题"当诉求 / scene 词表里没有"价保"这一类 / 造数顺序钉死诉求分布 /
六个字段有回落唯独现价没有 / 辅助随机流不按 seed 重置 /
过程探针全都假设"agent 至少查过"。）"""

if t.count(old) == 1:
    t = t.replace(old, new)
    p.write_text(t, encoding="utf-8")
    print("交付总览.md：缺陷清单已追加")
else:
    print(f"⚠ 交付总览清单句未唯一命中（{t.count(old)}），跳过")
