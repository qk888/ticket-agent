"""量报表真正对应的数据规模（报表命令用什么种子，这里就用什么种子）。

★ 为什么必须这么做：`cli.py` 的 report 命令在跑之前会 `init_db(drop=True)` +
   `seed_all(n_customers=customers, seed=seed)` —— 它**用自己的 seed 重新造了一遍库**。
   所以先前手工用 seed=42 造出的那批数字（2,245 订单 / 583 工单）**不是报表所依据的数据**，
   尽管它们看起来很接近。把两个种子的数字混着写进文档，读者拿任一个都复现不出报表。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from sqlalchemy import func, select  # noqa: E402

from ticket_agent.db import session as db_session  # noqa: E402
from ticket_agent.db.models import Customer, Order, OrderItem, Refund, Ticket  # noqa: E402
from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.textnorm import normalize  # noqa: E402

# 报表命令实际用的参数：report --size 605 --k 3 --seed 7 --customers 1000
REPORT_SEED = 7
REPORT_CUSTOMERS = 1000
REPORT_SIZE = 605


def main() -> None:
    db_session.init_db(drop=True)
    summary = seed_all(n_customers=REPORT_CUSTOMERS, seed=REPORT_SEED)
    print(f"seed_all(n_customers={REPORT_CUSTOMERS}, seed={REPORT_SEED}) → {summary}")
    print()

    with db_session.session_scope() as db:
        for model in (Customer, Order, OrderItem, Ticket, Refund):
            n = db.execute(select(func.count()).select_from(model)).scalar_one()
            print(f"{model.__name__:<12} {n}")

        bodies = [t.body for t in db.query(Ticket).all()]

    changed = [b for b in bodies if normalize(b) != b]
    print()
    print(f"工单正文含表内繁体（会被归一化改变）：{len(changed)}"
          f" / {len(bodies)}  ({(len(changed) / len(bodies) * 100) if bodies else 0:.2f}%)")
    for b in changed[:10]:
        print(f"   {b[:70]}")

    cases = build_eval_cases(size=REPORT_SIZE, seed=REPORT_SEED)
    print()
    print(f"build_eval_cases(size={REPORT_SIZE}, seed={REPORT_SEED}) → {len(cases)} 条 case")


if __name__ == "__main__":
    main()
