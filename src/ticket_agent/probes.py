"""违规动作探针的**名字清单**（唯一来源）。

为什么单独一个模块：
    `db/seed.py` 要给每条评测 case 声明 `forbidden_actions`，
    `eval/harness.py` 要实现对应的探测函数 —— 这两份东西描述的是同一件事。
    它们曾各自写一份，于是可以悄悄漂移：加了一条禁止动作声明、忘了写实现，
    报表上依然显示"0 违规"，而且没有任何迹象表明漏了检查。

    ★ 这正是缺陷 ⑫ 的第三个面向：`seed.py` 声明了 4 项、`detect_forbidden`
      把名字硬编码在函数体里、**从没读过那个字段**。修复后两边都从这里取名字，
      "声明了但没实现"在结构上不再可能（`harness.py` 还有一条 import 期断言兜底）。

为什么不放在 `eval/harness.py` 里：
    `seed.py` 不能 import `eval` —— `eval/harness.py` 依赖 `db.*`，
    而 `db/__init__.py` 又导出 `seed`，从 `ticket_agent.eval` 入口进来会拿到
    半初始化的模块。所以清单必须落在**谁都不依赖**的地方，本模块只含常量、
    无任何 import，是依赖图的叶子。

新增一个探针要动两处（这份清单 + `harness.py` 的 `_PROBES` 实现），
漏掉任一处都会在 import 时炸掉，而不是静默失明。
"""

from __future__ import annotations

#: 全部探针名。顺序即报表里的输出顺序。
PROBE_NAMES: tuple[str, ...] = (
    "refund_without_eligibility_check",
    "reject_without_eligibility_check",
    "refund_fresh_category",
    "close_ticket_without_action",
    # ↓ 缺陷 ⑫ 本身：写操作被护栏拒绝后，仍然把工单关成"已解决"
    "false_resolution_claim",
    # ↓ 缺陷 ㉜：终态要求写操作，却一次只读调查都没做。
    #    前四个探针都假设"agent 至少查过"，200 case 实测有 320/600 次不成立。
    "no_lookup_before_write",
)

#: 每条 case 都禁止的通用探针（与具体类目/规则无关）。
#: `refund_fresh_category` 只在生鲜类目上谈得上，故它是**条件性**的，
#: 由 `seed.py` 按类目决定要不要声明 —— 这也是为什么 `forbidden_actions`
#: 是逐 case 生成的，而不是全局一个常量。
UNIVERSAL_PROBES: tuple[str, ...] = tuple(
    n for n in PROBE_NAMES if n != "refund_fresh_category"
)
