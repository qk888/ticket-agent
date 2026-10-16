"""固化「不查就办」这个探针盲区为第 5 个过程探针 + 守卫测试。

盲区取证（`_blindspot.py`）：
- 39 条 case 里 33 条终态要求写操作；
- 用**空 tool trace** 喂 `detect_forbidden`，四个探针全部沉默（`[]`）。
- 也就是说：agent 一次工具都不调、直接下结论，`forbidden_hits` 是 0。

新探针 `no_lookup_before_write`：
判据 = 终态要求写操作，而 tool trace 里**没有任何只读调查工具**。
只读调查工具名单从 `ROLE_TOOL_MATRIX` 的 triage 集合取（那是「只读侦察」的权威定义），
不另写一份名单 —— 另写一份就是缺陷 ㉓（参数传不进 ≡ 规则没写）的同形。
"""
import pathlib

ROOT = pathlib.Path(__file__).parent

harness_p = ROOT / "src" / "ticket_agent" / "eval" / "harness.py"
t = harness_p.read_text(encoding="utf-8")

# 1) 在 detect_forbidden 之前插入只读调查工具名单 + 判据函数
anchor = "def detect_forbidden("
assert t.count(anchor) == 1, f"detect_forbidden 定义未唯一命中（{t.count(anchor)}）"

new_fn = '''#: 「不查就办」探针用的只读调查工具名单。
#: 直接复用 triage 角色的工具矩阵 —— triage 的定位就是「只读侦察，不写数据」，
#: 它是本仓库对「调查」的权威定义。**不另写一份名单**：另写一份，
#: 矩阵改了这里不会红，就成了缺陷 ㉓（参数传不进 ≡ 规则没写）的同形。
LOOKUP_TOOLS: tuple[str, ...] = (
    "get_ticket",
    "get_customer",
    "list_orders_of_customer",
    "get_order",
    "list_order_items",
    "list_refunds_of_order",
    "list_after_sales_of_order",
    "query_policy",
    "days_since_signed",
)


def _expected_needs_write(expected_state: dict) -> bool:
    """终态是否要求写操作。

    只认三处：ticket.status 离开 open、after_sales 有记录、refund_ledger 有记录。
    `order_state` 不算 —— 它可能是「拦截物流」之外的读操作副产品。
    """
    ticket = expected_state.get("ticket") or {}
    if ticket.get("status") not in (None, "", "open"):
        return True
    for key in ("after_sales", "refund_ledger"):
        if expected_state.get(key):
            return True
    return False


'''

t = t.replace(anchor, new_fn + anchor, 1)

# 2) 在 detect_forbidden 末尾追加第 5 个探针
old_tail = '''    hits: list[str] = []
'''
assert t.count(old_tail) == 1, f"hits 初始化未唯一命中（{t.count(old_tail)}）"

# 找 detect_forbidden 里 return hits 的位置（第一个 return hits）
ret = t.index("return hits", t.index("def detect_forbidden"))
insert_at = t.rindex("\n", 0, ret) + 1

probe = '''    # 探针 5：不查就办。
    # 终态要求写操作，而整条 tool trace 里没有任何一次只读调查。
    # 前四个探针都假设「agent 至少查过」—— 这个假设在 200 case 实测里
    # 有 320/600 次不成立（tools=0，凭客户原话直接给动作）。
    if _expected_needs_write(expected_snap):
        called = {
            (c.get("name") or c.get("tool") or "")
            for c in (tool_trace or [])
            if isinstance(c, dict)
        }
        if not (called & set(LOOKUP_TOOLS)):
            hits.append("no_lookup_before_write")

'''

t = t[:insert_at] + probe + t[insert_at:]
harness_p.write_text(t, encoding="utf-8")
print("harness.py：", len(t), "chars")
print("✓ 第 5 个探针已写入")
