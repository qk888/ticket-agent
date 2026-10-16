"""#2/#60 的定向诊断：千级数据上那 8 条 R10 失败到底是哪一种。

用法（必须先建好千级库）：
    $env:DATABASE_URL="sqlite:///./eval_out/t1_1000_reqkind.db"
    python _r10diag.py

要回答的问题只有一个：
  `check_evidence("photo", request_cause)` 返回 False
 → #60 的回归：scripted 基线传 evidence_type="photo" 被拒收，
      凭证永远补不上 → submit_after_sales_action 不执行 → after_sales 空。
  `check_evidence` 返回 True 但答复没补上金额/凭证
    → #2 两轮链路的缺口。

两种修法完全不同，所以必须先定性，不能直接改代码让它变绿。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///./eval_out/t1_1000_reqkind.db")

from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import session_scope  # noqa: E402
from ticket_agent.db.models import Ticket  # noqa: E402
from ticket_agent.graph.nodes import apply_customer_reply, detect_evidence_type  # noqa: E402
from ticket_agent.rules import check_evidence  # noqa: E402
from ticket_agent.config import settings  # noqa: E402

TARGETS = {
    "acme-t296", "globex-t448", "globex-t612",
    "globex-t461", "acme-t167", "globex-t543", "acme-t102", "acme-t178",
}

# ★ 必须与 `_reqkindcheck.py 1000 605` **逐项同参**，否则量的是另一份数据。
#   它的调用是 `seed_all(n_customers, 90, 7)` —— 位置参数，**第三位才是 seed**。
#   我第一版写成了 `seed_all(n_customers=1000, seed=90)`，seed 是 90 不是 7，
#   于是重建出的库与跑出那 8 条失败的那份**不是同一份**：8 个目标只命中 5 个，
#   命中那 5 条还全是 R10:compensate（失败清单里是 R10:need_clarification）。
#   → 那一轮「check_evidence 被拒 0/5」的结论**作废**：它是另一份数据上
#     同名 case 的巧合，不能用来排除 #60 回归。
#   教训：诊断脚本跑出了结论 ≠ 那个结论指的是出问题的那份数据。
#   判据第一步必须先证明作用在同一个对象上（参数逐项相等 + case_id 全命中）。
seed_all(1000, 90, 7)
cases = build_eval_cases(size=605, seed=7)
hits = [c for c in cases if c["case_id"] in TARGETS]
print(f"评测集 {len(cases)} 条，命中目标 {len(hits)} 条")

if not hits:
    print("没有命中 —— seed 参数或 case 集变了，需要重新定位")
    sys.exit(1)

print()
for c in hits:
    with session_scope() as s:
        t = s.query(Ticket).filter(
            Ticket.tenant_id == c["tenant_id"], Ticket.id == c["ticket_id"]
        ).first()
        if t is None:
            print(f"{c['case_id']}: 工单不存在")
            continue
        kind, cause = t.request_kind, t.request_cause or ""
        amount = float(t.request_amount or 0.0)
        ev = bool(t.evidence_provided)

    print(f"== {c['case_id']}  {c['condition']}  basis={c.get('order_basis')}")
    print(f"   turn1 任务输入 : kind={kind!r} cause={cause!r} amount={amount} evidence={ev}")
    print(f"   turn1 golden   : ticket={c['expected_state']['ticket']['status']}")
    print(f"   两轮?          : {bool(c.get('customer_reply'))}")

    reply = (c.get("customer_reply") or "").strip()
    if reply:
        print(f"   客户答复       : {reply[:90]}")
        ev_t = detect_evidence_type(reply)
        print(f"   detect 凭证类型: {ev_t!r}")
        ok, why = check_evidence(ev_t, cause)
        print(f"   check_evidence : ok={ok} | {why}")
        t2 = c.get("turn2_state") or {}
        print(f"   turn2 golden   : ticket={(t2.get('ticket') or {}).get('status')!r} "
              f"after_sales={t2.get('after_sales')}")

    # 判据 ①：基线用的 photo 能不能过
    ok_photo, why_photo = check_evidence("photo", cause)
    verdict = "#60 回归" if not ok_photo else "#2 两轮链路缺口"
    print(f"   photo 能过吗 : {ok_photo} | {why_photo}")
    print(f"   ⇒ 倾向 : {verdict}")
    print()

print("== 汇总 ==")
n_refuse = 0
for c in hits:
    with session_scope() as s:
        t = s.query(Ticket).filter(
            Ticket.tenant_id == c["tenant_id"], Ticket.id == c["ticket_id"]
        ).first()
        if t is None:
            continue
        ok, _ = check_evidence("photo", t.request_cause or "")
    n_refuse += 0 if ok else 1
print(f"check_evidence('photo', cause) 被拒的 case：{n_refuse}/{len(hits)}")
print(f"赔付上限 settings.max_compensation_cny = {settings.max_compensation_cny}")
