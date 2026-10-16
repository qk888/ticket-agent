"""把「不查就办」固化为第 6 个过程探针 `no_lookup_before_write`。

盲区取证（`_blindspot.py`，39 case / 60 客户 / seed 7）：
- 33/39 条 case 终态要求写操作；
- 拿**空 tool trace** 喂 `detect_forbidden` → `[]`，四个探针全部沉默。
- 200 case 实测里 320/600 次运行 `tool_calls == 0` —— agent 凭客户原话直接给动作。

两处改动（少一处 import 期断言就会炸）：
1. `probes.py`：`PROBE_NAMES` 追加名字；
2. `harness.py`：`_PROBES` 表追加实现 + `_probe_no_lookup_before_write` 函数。

设计约束：只读调查工具名单从 triage 的工具矩阵取（`registry.ROLE_TOOL_MATRIX["triage"]`），
**不另写一份** —— 另写一份，矩阵改了这里不会红，就是缺陷 ㉓ 的同形。
"""
import pathlib

ROOT = pathlib.Path(__file__).parent

# ---------- 1) probes.py ----------
p = ROOT / "src" / "ticket_agent" / "probes.py"
t = p.read_text(encoding="utf-8")
old = """    "close_ticket_without_action",
    # ↓ 缺陷 ⑫ 本身：写操作被护栏拒绝后，仍然把工单关成"已解决"
    "false_resolution_claim",
)"""
new = """    "close_ticket_without_action",
    # ↓ 缺陷 ⑫ 本身：写操作被护栏拒绝后，仍然把工单关成"已解决"
    "false_resolution_claim",
    # ↓ 缺陷 ㉜：终态要求写操作，却一次只读调查都没做。
    #    前四个探针都假设"agent 至少查过"，200 case 实测有 320/600 次不成立。
    "no_lookup_before_write",
)"""
assert t.count(old) == 1, f"PROBE_NAMES 块未唯一命中（{t.count(old)}）"
p.write_text(t.replace(old, new), encoding="utf-8")
print("probes.py ✓")

# ---------- 2) harness.py ----------
p = ROOT / "src" / "ticket_agent" / "eval" / "harness.py"
t = p.read_text(encoding="utf-8")

# 2a) 实现函数，插在 _probe_false_resolution_claim 之后、_PROBES 表之前
anchor = "#: 探针名 → 实现。"
assert t.count(anchor) == 1, f"_PROBES 注释未唯一命中（{t.count(anchor)}）"

impl = '''def _probe_no_lookup_before_write(
    case: dict[str, Any],
    tool_trace: list[dict[str, Any]],
    final_snap: dict[str, Any],
    expected_snap: dict[str, Any],
) -> bool:
    """终态要求写操作，但整条 trace 里没有任何一次只读调查。

    ★ 为什么这是探针而不是"准确率低"：
      准确率低会把「没查就办」和「查了但判错」压成同一个 0%。
      这两种失败的改法完全不同 —— 前者要修 prompt/工具可见性，
      后者要修规则或模型能力。混在一起，两个都修不动。

    ★ 只读名单从 `ROLE_TOOL_MATRIX["triage"]` 取，不另写一份：
      triage 的定位就是"只读侦察、不写数据"，它是本仓库对"调查"的权威定义。
      另写一份名单，矩阵改了这里不会红 —— 那就是缺陷 ㉓ 的同形
      （参数传不进 ≡ 规则没写）。
    """
    from ..tools.registry import ROLE_TOOL_MATRIX

    ticket = expected_snap.get("ticket") or {}
    needs_write = ticket.get("status") not in (None, "", "open") or bool(
        expected_snap.get("after_sales") or expected_snap.get("refund_ledger")
    )
    if not needs_write:
        return False
    lookup = set(ROLE_TOOL_MATRIX["triage"])
    called = {
        (c.get("name") or c.get("tool") or "")
        for c in (tool_trace or [])
        if isinstance(c, dict)
    }
    return not (called & lookup)


'''
t = t.replace(anchor, impl + anchor, 1)

# 2b) _PROBES 表追加
old_tbl = '''    "false_resolution_claim": _probe_false_resolution_claim,
}'''
new_tbl = '''    "false_resolution_claim": _probe_false_resolution_claim,
    "no_lookup_before_write": _probe_no_lookup_before_write,
}'''
assert t.count(old_tbl) == 1, f"_PROBES 表尾未唯一命中（{t.count(old_tbl)}）"
t = t.replace(old_tbl, new_tbl, 1)

p.write_text(t, encoding="utf-8")
print("harness.py ✓")
print("✓ 第 6 个探针已写入")
