"""定位 `after_sales` 重复行的真正来源。

`_r10dup.py` 量到的形状：
    submit 只调 1 次、DB 却有 2–3 行、键完全相同、`after_sales` 里
    每行字段一模一样 → **不是 agent 重复调用**，是某处在循环里重复写入。

`run_case` 只做一次 apply_snapshot，所以嫌疑集中在 agent 那一侧
（multi 臂会跑 triage→resolver→verifier 三个角色，但只有 resolver
有写权限），或者 `submit_after_sales_action` 内部写了两遍。

本脚本把「apply_snapshot 之后的初始 after_sales」与「跑完之后」逐行对比，
并数一下 audit 里到底有几次真正的写入。

用法：
    python _r10dup2.py
"""
from __future__ import annotations

import os

DB_URL = "sqlite:///./eval_out/r10diff_reqkind.db"
os.environ["DATABASE_URL"] = DB_URL

from sqlalchemy import text as _sqltext  # noqa: E402

from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import session_scope  # noqa: E402
from ticket_agent.db.snapshot import export_snapshot  # noqa: E402
from ticket_agent.eval.harness import run_case  # noqa: E402

seed_all(1000, 90, 7)
cases = build_eval_cases(size=605, seed=7)
r10 = [c for c in cases if c["condition"].startswith("R10:compensate")]
print(f"R10:compensate 共 {len(r10)} 条")

# 只看第一次跑就有重复的（k_index=0，库刚重建，after_sales 必为空）
for c in r10[:8]:
    cid = c["case_id"]
    tid, tk = c["tenant_id"], c["ticket_id"]

    with session_scope() as s:
        before = export_snapshot(s, tid, tk).get("after_sales") or []
        head = s.execute(_sqltext("SELECT COALESCE(MAX(id), 0) FROM audit_log")).scalar()

    res = run_case(c, arm="single", kind="scripted", k_index=0)

    with session_scope() as s:
        after = export_snapshot(s, tid, tk).get("after_sales") or []
        rows = s.execute(
            _sqltext(
                "SELECT id, actor, action, ok, payload FROM audit_log "
                "WHERE tenant_id=:t AND ticket_id=:k AND id>=:i ORDER BY id"
            ),
            {"t": tid, "k": tk, "i": (head or 0) + 1},
        ).mappings().all()

    print(f"\n== {cid}  {c['condition']}  passed={res.passed}")
    print(f"   before={before}")
    print(f"   after ={after}")
    print(f"   audit（本次新增 {len(rows)} 条）：")
    for r in rows:
        print(f"      id={r['id']} {r['actor']:9s} {r['action']:38s} ok={r['ok']}  {str(r['payload'])[:90]}")
    if len(after) > 1:
        print(f"   ★ 重复：跑之前 {len(before)} 行，跑之后 {len(after)} 行")
