"""量**结果侧**：R10 赔付线到底断在哪一环。

原 `_r10diff.py` 硬编码 4 个 case_id，命中 1 条就 exit 1 —— 那是**上一轮**
的数据形状。本轮改为：不猜 case_id，直接从库里按条件抓，
凡是 `R10:*` 的都跑，命中多少报多少（命中数也是结论的一部分）。

要回答的问题只有一个：
    diff 是 `after_sales: 期望 [...] 实际 []`，
    这到底是 ① 基线没调写工具 / ② 工具层拒了 / ③ 写了没落库 / ④ golden 判错？

用法：
    python _r10diff.py
"""
from __future__ import annotations

import os

DB_URL = "sqlite:///./eval_out/r10diff_reqkind.db"
os.environ["DATABASE_URL"] = DB_URL

from sqlalchemy import text as _sqltext  # noqa: E402

from ticket_agent.config import settings  # noqa: E402
from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import session_scope  # noqa: E402
from ticket_agent.db.snapshot import export_snapshot  # noqa: E402
from ticket_agent.eval.harness import run_case  # noqa: E402

print("== 重建与 _reqkindcheck.py 逐项同参：seed_all(1000, 90, 7) ==")
seed_all(1000, 90, 7)
cases = build_eval_cases(size=605, seed=7)
r10 = [c for c in cases if c["condition"].startswith("R10")]
print(f"评测集 {len(cases)} 条，其中 R10:* 共 {len(r10)} 条")
by_cond: dict[str, int] = {}
for c in r10:
    by_cond[c["condition"]] = by_cond.get(c["condition"], 0) + 1
print("  条件分布：", by_cond)
if not r10:
    print("⚠ 一条 R10 都没有 —— 先查覆盖度，不要看下面的数字")
    raise SystemExit(1)


def audit_rows(tenant_id: str, ticket_id: int, since_id: int) -> list[dict]:
    try:
        with session_scope() as s:
            rows = s.execute(
                _sqltext(
                    "SELECT * FROM audit_log "
                    "WHERE tenant_id=:t AND ticket_id=:k AND id>=:i ORDER BY id"
                ),
                {"t": tenant_id, "k": ticket_id, "i": since_id},
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:  # pragma: no cover
        print(f"   ⚠ 审计读取失败：{type(e).__name__}: {e}")
        return []


tally: dict[str, int] = {}
detail: list[dict] = []

for c in r10:
    cid = c["case_id"]
    tid, tk = c["tenant_id"], c["ticket_id"]
    with session_scope() as s:
        head = s.execute(_sqltext("SELECT COALESCE(MAX(id), 0) FROM audit_log")).scalar()

    res = run_case(c, arm="single", kind="scripted", k_index=0)
    trail = audit_rows(tid, tk, (head or 0) + 1)
    submits = [r for r in trail if str(r.get("action") or "").startswith("submit_after_sales_action")]
    marks = [r for r in trail if str(r.get("action") or "").startswith("mark_evidence_received")]

    with session_scope() as s:
        snap = export_snapshot(s, tid, tk)

    wrote = bool(snap.get("after_sales"))
    print()
    print(f"== {cid}  {c['condition']}  passed={res.passed}")
    print(f"   observed_action={res.observed_action!r}  expected={res.expected_action!r}")
    print(f"   diffs     : {res.diffs}")
    print(f"   writes    : submit={len(submits)} evidence={len(marks)}")
    print(f"   after_sales in DB: {snap.get('after_sales')}")
    if marks:
        payload = marks[0].get("payload") or marks[0].get("detail") or ""
        print(f"   evidence  : {str(payload)[:160]}")
    if submits:
        payload = submits[0].get("payload") or submits[0].get("detail") or ""
        print(f"   submit    : {str(payload)[:200]}")

    # ★ 单臂（arm="single"）跑完**不保留** verifier 的只读复核轮，
    #   DB 里混着同一条 case 上一轮 multi 留下的 after_sales 也是可能的。
    #   所以"调了写工具但 after_sales 空"必须再分一层：
    #   工具返回 ok=False 才是真被拒，返回 ok=True 而库里没有才是"没落库"。
    submit_ok = False
    for r in submits:
        payload = r.get("payload") or r.get("detail") or ""
        if "already" not in str(payload) and '"ok"' in str(payload):
            submit_ok = True
    if not submits:
        v = "① 基线没调 submit_after_sales_action"
    elif not wrote and not submit_ok:
        v = "② 工具层拒了（返回 reason，没写库）"
    elif not wrote:
        v = "③ 返回 ok 却没落库"
    else:
        v = "④ 写成功了仍判失败（golden/收尾）"
    tally[v] = tally.get(v, 0) + 1
    detail.append(
        {"cid": cid, "cond": c["condition"], "passed": res.passed, "v": v,
         "diffs": res.diffs, "submits": len(submits), "wrote": wrote}
    )

print("\n== 汇总 ==")
for k in sorted(tally):
    print(f"   {k}: {tally[k]}/{len(r10)}")
fails = [d for d in detail if not d["passed"]]
print(f"   失败 {len(fails)}/{len(r10)}")
for d in fails:
    print(f"      {d['cid']} {d['cond']}  writes={d['submits']} db={d['wrote']}  diffs={d['diffs']}")
print(f"   赔付上限 settings.max_compensation_cny = {settings.max_compensation_cny}")
print(f"   库：{DB_URL}")
