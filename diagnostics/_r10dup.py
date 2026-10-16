"""量**重复写入**这一件事：`after_sales` 的幂等键到底管不管用。

上一轮 `_r10diff.py` 量出的形状（24 条 R10 里 6 条失败）：
    after_sales: 期望 [('compensate','approved',5.0)]，
                 实际 [('compensate','approved',5.0), ('compensate','approved',5.0)]
→ 同一条 case 跑一次，库里出现**两条一模一样**的赔付记录。

要回答的问题：
  1. 两次 `submit_after_sales_action` 的 idempotency_key 是否相同？
  2. 若相同而 DB 里仍有两条 → 幂等键没落到唯一约束/没被查；
  3. 若不同 → 键里缺了什么维度（已知台账：键不含 request_cause）。

用法：
    python _r10dup.py
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

print("== 同参重建：seed_all(1000, 90, 7) ==")
seed_all(1000, 90, 7)
cases = build_eval_cases(size=605, seed=7)
r10 = [c for c in cases if c["condition"].startswith("R10:compensate")]
print(f"R10:compensate 共 {len(r10)} 条")


def audit_rows(tenant_id: str, ticket_id: int, since_id: int) -> list[dict]:
    with session_scope() as s:
        rows = s.execute(
            _sqltext(
                "SELECT * FROM audit_log "
                "WHERE tenant_id=:t AND ticket_id=:k AND id>=:i ORDER BY id"
            ),
            {"t": tenant_id, "k": ticket_id, "i": since_id},
        ).mappings().all()
    return [dict(r) for r in rows]


dup = 0
key_same = 0
key_diff = 0
checked = 0

for c in r10:
    cid = c["case_id"]
    tid, tk = c["tenant_id"], c["ticket_id"]
    with session_scope() as s:
        head = s.execute(_sqltext("SELECT COALESCE(MAX(id), 0) FROM audit_log")).scalar()

    res = run_case(c, arm="single", kind="scripted", k_index=0)
    trail = audit_rows(tid, tk, (head or 0) + 1)
    submits = [r for r in trail if str(r.get("action") or "").startswith("submit_after_sales_action")]

    with session_scope() as s:
        snap = export_snapshot(s, tid, tk)
    rows_in_db = snap.get("after_sales") or []
    if len(rows_in_db) > 1:
        dup += 1
        checked += 1
        keys = []
        for r in submits:
            payload = r.get("payload") or r.get("detail") or ""
            if "key" in str(payload):
                # payload 是 JSON 串，抠出 key 字段
                import json as _j

                try:
                    keys.append(_j.loads(str(payload)).get("key"))
                except Exception:
                    keys.append(None)
        same = len(set(keys)) == 1 and keys and keys[0] is not None
        key_same += 1 if same else 0
        key_diff += 0 if same else 1
        print(f"== {cid}  DB 行数={len(rows_in_db)}  submit 次数={len(submits)}")
        print(f"   keys={keys}  {'✅ 相同' if same else '❌ 不同'}")
        print(f"   rows={rows_in_db}")
        print(f"   diffs={res.diffs}")
    elif len(rows_in_db) == 1 and len(submits) > 1:
        checked += 1
        key_same += 1
        print(f"== {cid}  DB 行数=1  submit 次数={len(submits)}  ✅ 幂等生效")

print(f"\n== 汇总 ==")
print(f"   出现重复行的 case：{dup}")
print(f"   其中键相同（幂等失效）：{key_same}  键不同（键缺维度）：{key_diff}")
print(f"   检查过的 case 数：{checked}")
