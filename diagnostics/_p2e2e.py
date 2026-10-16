"""#2 收尾体检：两轮 case 真的存在、且端到端能跑通。

保留为正式脚本：以后改 seed / snapshot / harness 都可以拿它对账。

用法：
    python _p2e2e.py
"""

from __future__ import annotations

import sys

from ticket_agent.db.seed import build_eval_cases, seed_all
from ticket_agent.db.session import init_db
from ticket_agent.eval.harness import run_case


def main() -> int:
    # ★ drop=True 不是偷懒：Ticket 新加了 pending_question / customer_reply 两列，
    #   而 init_db 走 create_all —— 它只建缺的表，绝不给已存在的表加列。
    #   旧库继续用会报 "table tickets has no column named pending_question"。
    #   库里的一切都能由 seed_all 复现，重建没有信息损失。
    init_db(drop=True)
    seed_all(n_customers=60, seed=7)
    cases = build_eval_cases(size=40, seed=11)
    two_turn = [c for c in cases if c.get("customer_reply") and c.get("turn2_state")]
    print(f"总 case {len(cases)}，两轮 case {len(two_turn)}")
    if not two_turn:
        print("没有两轮 case —— 覆盖缺口，需要修 seed")
        return 1
    for c in two_turn:
        res = run_case(c, arm="multi", kind="scripted", k_index=0)
        print(
            f"  {c['case_id']} {c['condition']} -> turns={res.turns} "
            f"passed={res.passed} tool_calls={res.tool_calls} "
            f"write={res.write_calls} verifier={res.verifier_passed} "
            f"action={res.observed_action} err={res.error!r}"
        )
        if res.error:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
