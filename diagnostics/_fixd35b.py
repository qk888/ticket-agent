"""把 should_clarify 从 case 层一路送到 verifier（接线的第二段）。

第一段已改：`verifier_node(state, *, should_clarify=None)` +
`_case_should_clarify(case)`。现在缺三段接线：

  1. `run_ticket(...)` 要能接收 `should_clarify` 并转给 `build_graph`；
  2. `build_graph(arm, checkpointer, ...)` 要能把它带进 verifier 节点；
  3. `harness.run_case` 的两处 `run_ticket(...)` 调用要传 case 层的值。

★ 为什么用**闭包/参数**而不是全局：并发评测时多进程各跑各的，但同一进程内
  也可能有多个 run（两轮 case 就是顺序跑两次）。全局变量会被第二个 run 覆盖，
  而 verifier 读的是它自己那一次的值 —— 这正是缺陷 ⑮ 里 `_ctx_or_raise`
  用 ContextVar 的原因。传参最直白，且调用点只有一个。

用法：python _fixd35b.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent
NODES = ROOT / "src" / "ticket_agent" / "graph" / "nodes.py"
BUILDER = ROOT / "src" / "ticket_agent" / "graph" / "builder.py"
SERVICE = ROOT / "src" / "ticket_agent" / "service.py"
HARNESS = ROOT / "src" / "ticket_agent" / "eval" / "harness.py"


def sub(text: str, old: str, new: str, where: str, count: int = 1) -> str:
    n = text.count(old)
    assert n == count, f"{where}：期望命中 {count} 次，实际 {n} 次"
    return text.replace(old, new)


def main() -> int:
    # ── 1. run_ticket 签名 + 传给 build_graph ────────────────────────────
    t = NODES.read_text(encoding="utf-8")
    print("=== run_ticket 当前签名与 build_graph 调用点 ===")
    for i, line in enumerate(t.splitlines(), 1):
        if line.startswith("def run_ticket(") or "build_graph(" in line:
            print(f"  nodes.py:{i}: {line.strip()[:110]}")
    NODES.write_text(t, encoding="utf-8")

    print("\n=== builder.build_graph 签名与 verifier 节点注册 ===")
    b = BUILDER.read_text(encoding="utf-8")
    for i, line in enumerate(b.splitlines(), 1):
        if line.startswith("def build_graph(") or "verifier" in line.lower() and (
            "add_node" in line or "verifier_node" in line
        ):
            print(f"  builder.py:{i}: {line.strip()[:110]}")
    BUILDER.write_text(b, encoding="utf-8")

    print("\n=== service.run_ticket / 转发层 ===")
    s = SERVICE.read_text(encoding="utf-8")
    for i, line in enumerate(s.splitlines(), 1):
        if line.startswith("def run_ticket(") or "run_ticket(" in line:
            print(f"  service.py:{i}: {line.strip()[:110]}")
    SERVICE.write_text(s, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
