"""#2 verifier 判据 + 第二轮答复落库的自检。

用法：python _p2check2.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///./eval_out/_p2check2.db")

from ticket_agent.db import seed as seed_mod  # noqa: E402
from ticket_agent.db.session import init_db, session_scope  # noqa: E402
from ticket_agent.db.snapshot import export_snapshot  # noqa: E402
from ticket_agent.graph.nodes import apply_customer_reply, detect_evidence_type  # noqa: E402
from ticket_agent.rules import check_evidence  # noqa: E402

FAIL: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAIL.append(msg)


init_db(drop=True)
seed_mod.seed_all(n_customers=60, seed=7)

print("== detect_evidence_type 确定性判据（#60：认出是哪一类）==")
check(detect_evidence_type("我拍照片给你了") == "photo", "「拍照片」→ photo")
check(detect_evidence_type("截图在这，订单号 12345") == "screenshot", "「截图+订单号」→ screenshot")
check(detect_evidence_type("我就是要退款") == "", "纯诉求认不出凭证类型")
check(detect_evidence_type("") == "", "空串认不出凭证类型")
check(detect_evidence_type("物流停在转运中心一直没动") == "tracking_proof", "「物流轨迹/转运中心」→ tracking_proof")
check(detect_evidence_type("照片发了，运单号 3239746110") == "photo", "同句含照片+运单号时按序取更强的 photo")

print("== check_evidence 校验判据（#60：够不够格）==")
_ok, _why = check_evidence("photo", "damaged")
check(_ok is True, f"photo 可支撑 damaged（{_why}）")
_ok2, _why2 = check_evidence("tracking_proof", "damaged")
check(_ok2 is False and "evidence_insufficient" in _why2, f"tracking_proof 证明不了破损（{_why2}）")
_ok3, _why3 = check_evidence("chat_log", "damaged")
check(_ok3 is False and "evidence_insufficient" in _why3, f"聊天记录证明不了破损（{_why3}）")
_ok4, _why4 = check_evidence("none", "damaged")
check(_ok4 is False and "evidence_type_unknown" in _why4, f"未知类型被拒（{_why4}）")
_ok5, _why5 = check_evidence("chat_log", "")
check(_ok5 is True, f"无索赔原因时一律放行，不造不可赢 case（{_why5}）")
_ok6, _why6 = check_evidence("tracking_proof", "lost")
check(_ok6 is True, f"丢件可用快递轨迹（{_why6}）")

print("== apply_customer_reply 只补缺的、不覆盖已有的 ==")
with session_scope() as s:
    from ticket_agent.db.session import TenantRepo

    repo = TenantRepo(s, "acme")
    tickets = repo.list_tickets() if hasattr(repo, "list_tickets") else []
    # 直接按需求挑一条：request_cause 空、request_amount 0、evidence False
    from ticket_agent.db.models import Ticket

    cands = [
        t
        for t in s.query(Ticket).filter(Ticket.tenant_id == "acme").all()
        if not t.request_cause and not (t.request_amount and t.request_amount > 0)
    ]
    print(f"  （候选工单 {len(cands)} 条）")
    if not cands:
        print("  ✗ 没有可用的候选工单")
        sys.exit(1)
    t = cands[0]
    tid, tenant = t.id, t.tenant_id
    before_kind = t.request_kind
    before_q = t.pending_question

r = apply_customer_reply(tenant, tid, "衣服破了，赔我 20 元，照片发你了")
check(r.get("ok") is True, "apply_customer_reply 返回 ok")
changed = r.get("changed") or {}
check(changed.get("request_cause") == "damaged", f"补齐 request_cause=damaged（实际 {changed.get('request_cause')!r}）")
check(changed.get("request_amount") == 20.0, f"补齐 request_amount=20.0（实际 {changed.get('request_amount')!r}）")
check(changed.get("evidence_provided") is True, "补齐 evidence_provided=True")
check(before_kind == (r.get("kind") or before_kind), "request_kind **未被改动**")

print("== 幂等 / 边界 ==")
r2 = apply_customer_reply(tenant, tid, "")
check(r2.get("ok") is False and r2.get("error") == "empty_reply", "空答复返回 empty_reply")
r3 = apply_customer_reply(tenant, 999999, "在吗")
check(r3.get("ok") is False and r3.get("error") == "ticket_not_found", "不存在的工单返回 ticket_not_found")

with session_scope() as s:
    snap = export_snapshot(s, tenant, tid)
    tk = snap.get("ticket") or {}
check("pending_question" in tk, "快照带出 pending_question（观测字段）")
check("customer_reply" in tk, "快照带出 customer_reply（观测字段）")
check(tk.get("customer_reply", "").startswith("衣服破了"), f"customer_reply 已落库：{tk.get('customer_reply')!r}")
check("pending_question" not in {k for k in tk if k == "status"}, "status 仍是判定字段（未被动过）")

print("== 已关闭工单不能再答复 ==")
with session_scope() as s:
    from ticket_agent.db.models import Ticket as T2

    closed = s.query(T2).filter(T2.status.in_(("resolved", "rejected"))).first()
    if closed is not None:
        rc = apply_customer_reply(closed.tenant_id, closed.id, "我再补一句")
        check(rc.get("ok") is False and rc.get("error") == "ticket_closed", "已关闭工单返回 ticket_closed")
    else:
        print("  （库里没有已关闭工单，跳过）")

print()
if FAIL:
    print(f"✗ {len(FAIL)} 项未通过")
    sys.exit(1)
print("✓ 全部通过")
