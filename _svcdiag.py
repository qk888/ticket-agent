"""三条售后线真实失败的定向取证：把工具轨迹与 golden 输入并排打出来。

背景
----
两臂新口径报表（`eval_out/resume-scripted-{single,multi}-s7.md`）里，违规动作已归零
（那 530 条是探针假阳性：探针只认 `check_refund_eligibility`，而售后线的合法前置是
`check_after_sales_eligibility`）。`_faildiag.py` 从进度文件里读出剩下三条真实失败：

    R14:urge_logistics   失败 48  run=72   expected_action='refund'  observed='reject'
                          diff: ticket.status 期望 'resolved' 实际 'rejected'
                               after_sales 期望 [('urge','approved',0.0)] 实际 []
    R12:issue_invoice    失败 44  run=66   resolve→reject 40 / refund→reject 4
                          diff 同上，after_sales 期望 [('invoice','approved',0.0)] 实际 []
    R10:compensate       失败  9  run=45   refund→resolve
                          diff: after_sales 期望 [('compensate','approved',5.0)] 实际 []
                          tool_calls=10 write=3 —— 写工具走到过，但没写这一笔

三条的形状不一样，所以根因大概率不止一个：

- R14/R12 是**该办的动作根本没办**（`after_sales` 空、工单还被关成 rejected）；
- R10 是**别的写操作办了、这一笔没办**（write=3 而 after_sales 空）。

`_faildiag.py` 只给了 CaseResult 字段（没有工具轨迹），所以这个脚本补上轨迹：
跑单条 case → 读 `audit_log` 增量 → 把工具名 + 入参 + golden 输入并排打出来。

判据纪律（本项目已连犯四次「没读就下结论」）：
- 重建参数必须与 `_resume.py` 逐项一致：`seed_all(n_customers=1000, seed=7)` +
  `build_eval_cases(size=605, seed=7, split="all")`（`_resume.py:189-190`）。
- 目标 case_id 必须全部命中，否则 exit 1、不给结论。
- 不猜任何字段名：`get_ticket` 返回体的键、`audit_log` 的列名都从代码来。

用法：python _svcdiag.py
"""

from __future__ import annotations

import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///./eval_out/svcdiag.db"

from sqlalchemy import text as _sqltext  # noqa: E402

from ticket_agent.db.models import Ticket  # noqa: E402
from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import session_scope  # noqa: E402
from ticket_agent.db.snapshot import export_snapshot  # noqa: E402
from ticket_agent.eval.harness import run_case  # noqa: E402

#: 每个条件挑 1–2 条失败样本（case_id 取自 _faildiag.py 的失败明细）
TARGETS = (
    ("R14:urge_logistics", "acme-t311"),
    ("R12:issue_invoice", "globex-t403"),
    ("R10:compensate", "acme-t296"),
)

# ── 重建（与 _resume.py 逐项同参）───────────────────────────────────────
seed_all(n_customers=1000, seed=7)
cases = build_eval_cases(size=605, seed=7, split="all")
by_id = {c["case_id"]: c for c in cases}
print(f"评测集 {len(cases)} 条（customers=1000 seed=7 size=605 split=all）")

for cond, cid in TARGETS:
    c = by_id.get(cid)
    if c is None:
        print(f"✗ {cid} 不在这份库里 —— seed/size 参数或 case 集变了，先修这里")
        sys.exit(1)
    if c["condition"] != cond:
        print(f"⚠ {cid} 的条件是 {c['condition']!r}，不是 {cond!r}（仍继续，但要知道）")

print()


def audit_rows(tenant_id: str, ticket_id: int, since_id: int) -> list[dict]:
    """本次运行新增的审计记录（原生 SQL，表结构不符只丢这一项）。"""
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
    except Exception as e:  # pragma: no cover - 诊断脚本，不因它崩
        print(f"   ⚠ 审计读取失败，跳过：{type(e).__name__}: {e}")
        return []


for cond, cid in TARGETS:
    c = by_id[cid]
    tid, tk = c["tenant_id"], c["ticket_id"]

    # ── 输入侧：工单结构化字段 + 订单事实 ────────────────────────────────
    with session_scope() as s:
        t = s.query(Ticket).filter(Ticket.tenant_id == tid, Ticket.id == tk).first()
        if t is None:
            print(f"== {cid}: 工单不存在")
            continue
        fields = {
            "request_kind": t.request_kind,
            "request_cause": t.request_cause or "",
            "request_amount": float(t.request_amount or 0.0),
            "evidence_provided": bool(t.evidence_provided),
            "order_id": t.order_id,
            "status": t.status,
            "intent": t.intent,
        }
        head = s.execute(
            _sqltext("SELECT COALESCE(MAX(id), 0) FROM audit_log")
        ).scalar()

    print(f"== {cid}  {cond}  basis={c.get('order_basis')}  level={c.get('level')}")
    print(f"   工单结构化 : {fields}")
    print(f"   正文（前70）: {(t.body or '')[:70]!r}")
    exp = c["expected_state"]
    print(f"   golden 终态 : ticket.status={(exp.get('ticket') or {}).get('status')!r}")
    print(f"                after_sales={exp.get('after_sales')}")
    print(f"                refunds={exp.get('refunds')}")
    print(f"                order={exp.get('order')}")
    print(f"   两轮?       : {bool(c.get('customer_reply'))}")

    # ── 结果侧：跑一遍 + 工具轨迹 ────────────────────────────────────────
    res = run_case(c, arm="multi", kind="scripted", k_index=0)
    print(f"   --- run_case(multi, scripted) passed={res.passed} ---")
    print(f"   expected_action={res.expected_action!r} observed_action={res.observed_action!r}")
    for d in res.diffs:
        print(f"      diff: {d}")

    trail = audit_rows(tid, tk, (head or 0) + 1)
    print(f"   --- 工具轨迹（{len(trail)} 条，actor | action | payload 摘要）---")
    for r in trail:
        payload = str(r.get("payload"))[:170]
        print(f"      {r.get('actor'):<9} | {r.get('action'):<38} | {payload}")

    with session_scope() as s:
        snap = export_snapshot(s, tid, tk)
    print(
        f"   终态         : ticket={(snap.get('ticket') or {}).get('status')!r} "
        f"after_sales={snap.get('after_sales')} refunds={snap.get('refunds')}"
    )
    print()
