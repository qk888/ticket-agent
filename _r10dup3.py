"""复现 `after_sales` 重复行，并定位它是不是 `run_case` 的重复调用造成的。

关键观察（`_r10root.py` 第一轮）：
    连续跑 3 次，before 依次是 0 / 1 / 1 —— **第 2 轮的 before 是 1**，
    说明第 1 轮写进去的那行**没有被 apply_snapshot 清掉**；
    但 after 一直是 1，说明 apply_snapshot 之后的写入是幂等的。

所以要分辨两种可能：
  (a) `apply_snapshot` 没删掉第 1 轮写入的行（before=1 就是证据）；
  (b) 快照重建时把上一轮的行**又插了一遍**（before=1 但 after=2 才是证据）。

第 1 轮 after=1、第 2 轮 after=1 → 是 (a)：**重置漏删**，
且写入侧幂等生效（同一键重复提交返回 duplicate）。

但 `_r10dup.py` 里量到的是 after=2/3，那种形状要求"同一轮里写了两遍"。
差别在 arm：`_r10dup.py` 用的是 arm="single"，
而 multi 臂 triage→resolver→verifier 会不会让同一个写工具跑两遍？
本脚本把 single 与 multi 并排跑，直接对照。

用法：
    python _r10dup3.py
"""
from __future__ import annotations

import os

DB_URL = "sqlite:///./eval_out/r10dup3_reqkind.db"
os.environ["DATABASE_URL"] = DB_URL

from sqlalchemy import text as _sqltext  # noqa: E402

from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import session_scope  # noqa: E402
from ticket_agent.db.snapshot import export_snapshot  # noqa: E402
from ticket_agent.eval.harness import run_case  # noqa: E402

seed_all(1000, 90, 7)
cases = build_eval_cases(size=605, seed=7)
r10 = [c for c in cases if c["condition"] == "R10:compensate"]
print(f"R10:compensate 共 {len(r10)} 条")


def audit(tenant_id: str, ticket_id: int, since_id: int) -> list[dict]:
    with session_scope() as s:
        return [
            dict(r)
            for r in s.execute(
                _sqltext(
                    "SELECT id, actor, action, ok, payload FROM audit_log "
                    "WHERE tenant_id=:t AND ticket_id=:k AND id>=:i ORDER BY id"
                ),
                {"t": tenant_id, "k": ticket_id, "i": since_id},
            ).mappings().all()
        ]


for c in r10[:6]:
    tid, tk = c["tenant_id"], c["ticket_id"]
    print(f"\n== {c['case_id']}  {c['condition']}")
    for arm in ("single", "multi"):
        with session_scope() as s:
            before = export_snapshot(s, tid, tk).get("after_sales") or []
            head = s.execute(_sqltext("SELECT COALESCE(MAX(id), 0) FROM audit_log")).scalar()
        res = run_case(c, arm=arm, kind="scripted", k_index=0)
        with session_scope() as s:
            after = export_snapshot(s, tid, tk).get("after_sales") or []
        rows = audit(tid, tk, (head or 0) + 1)
        submits = [r for r in rows if str(r["action"]).startswith("submit_after_sales_action")]
        dups = [r for r in rows if str(r["action"]).startswith("submit_after_sales_action.duplicate")]
        print(f"   {arm:7s}: before={len(before)} after={len(after)} "
              f"submit={len(submits)} dup={len(dups)} passed={res.passed}")
        for r in submits:
            print(f"            {r['actor']:9s} ok={r['ok']} {str(r['payload'])[:100]}")
