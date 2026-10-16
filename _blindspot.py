"""把「不查就办」这个探针盲区固化成一条可跑的守卫测试（缺陷 ㉜ 的取证脚本）。

设计：
- 只读，跑在现有 `tests/` 的 fixture 上，不新造数据。
- 判据：一次工具都没调的运行（`tool_calls == 0`）但**终态需要写操作**（expected_state 里有非 open 的 ticket.status，或有 after_sales/refund_ledger/order_state 变更）→ 记一次「零调用却要求写」。
- 现成的 `detect_forbidden` 有四个探针，都不覆盖这个形状。这里证明它确实不覆盖。
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

from ticket_agent.db.session import init_db  # noqa: E402
from ticket_agent.db.seed import seed_all, build_eval_cases  # noqa: E402


def needs_write(expected_state: dict) -> bool:
    """终态是否要求写操作。"""
    t = expected_state.get("ticket") or {}
    if t.get("status") not in (None, "", "open"):
        return True
    for key in ("after_sales", "refund_ledger"):
        if expected_state.get(key):
            return True
    return False


def main():
    init_db(drop=True)
    seed_all(n_customers=60, seed=7)
    cases = build_eval_cases(size=40, seed=11)
    print(f"case 数：{len(cases)}")

    want_write = [c for c in cases if needs_write(c.get("expected_state") or {})]
    print(f"其中终态要求写操作的：{len(want_write)}")

    # 四个现成探针能否覆盖？用一个人造 trace 试
    from ticket_agent.eval.harness import detect_forbidden

    c = want_write[0]
    empty_trace = []  # 零工具调用
    hits = detect_forbidden(c, empty_trace, {}, c.get("expected_state") or {})
    print(f"\n零工具调用时 detect_forbidden 命中：{hits!r}")
    if not hits:
        print("⇒ **盲区确认**：一次工具都不调，四个探针全部沉默。")

    print("\n按条件看「要求写」的 case 数：")
    from collections import Counter

    cnt = Counter(c["condition"] for c in want_write)
    for cond, n in cnt.most_common():
        print(f"  {cond:26s} {n}")


if __name__ == "__main__":
    main()
