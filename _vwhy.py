"""量 verifier 为什么放行零动作 run（缺陷 ㉟ 定位第一步）。

`_vnone.py` 已经量出形状：600 run 里 single 4 条 / multi 20 条是
`verifier_passed=True` 而 `observed_action='none'`，且**终态要求写操作**。

但"verifier 通过"有两个不同来源，必须先分开：
  1. verifier 节点**没跑**（被打断/提前收尾）→ state 里 verifier_passed 是默认值
  2. verifier 节点跑了，但它的判据认为"没问题"

判据：`verifier_passed` 在 AgentState 里的默认值是什么？如果默认 True，
那么"没跑"和"跑了认为没问题"在读数上**完全同形** —— 那本身就是缺陷。

本脚本只读代码，打印：
  - AgentState.verifier_passed 的声明与默认值
  - verifier 节点的所有 return 路径（哪些会写 verifier_passed）
  - 提前收尾的路径（MAX_AGENT_STEPS / budget trip）会不会跳过 verifier
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent
STATE = ROOT / "src" / "ticket_agent" / "graph" / "state.py"
NODES = ROOT / "src" / "ticket_agent" / "graph" / "nodes.py"
BUILDER = ROOT / "src" / "ticket_agent" / "graph" / "builder.py"


def main() -> int:
    print("=== 1. AgentState 里 verifier_passed 的声明 ===")
    st = STATE.read_text(encoding="utf-8")
    for m in re.finditer(r"^\s*(verifier_passed\w*)\s*:.*$", st, re.M):
        print("  state.py:", m.group(0).strip())

    print("\n=== 2. verifier 节点里哪些路径写 verifier_passed ===")
    nd = NODES.read_text(encoding="utf-8")
    lines = nd.splitlines()
    # 找 verifier 函数体范围
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^def .*verifier|^async def .*verifier", ln):
            start = i
            print(f"  nodes.py:{i+1}: {ln}")
    if start is None:
        print("  （没找到独立 verifier 函数，改搜所有赋值点）")
    for i, ln in enumerate(lines):
        if "verifier_passed" in ln:
            print(f"  nodes.py:{i+1}: {ln.strip()[:110]}")

    print("\n=== 3. 提前收尾路径（是否会跳过 verifier）===")
    for i, ln in enumerate(lines):
        if re.search(r"MAX_AGENT_STEPS|_escalate_on_trip|BudgetExceeded|tripped", ln):
            print(f"  nodes.py:{i+1}: {ln.strip()[:110]}")

    print("\n=== 4. builder 里的边（verifier 是否必经）===")
    bd = BUILDER.read_text(encoding="utf-8")
    for i, ln in enumerate(bd.splitlines()):
        if re.search(r"add_edge|add_conditional_edges|verifier|END", ln):
            print(f"  builder.py:{i+1}: {ln.strip()[:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
