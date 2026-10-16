"""诊断：人工审批路径是否绕过了政策护栏（R5/R6）。

背景（一次真实缺陷，README §4.2 缺陷 ⑥）：
`_apicheck.py` 的 HITL 用例批准退款后，快照里出现了 `refund_count: 2`
和两条一模一样的 approved 退款 —— 一单 47.53 元的图书被退成了 95.06 元。

根因不是幂等失效（两次的幂等键不同，唯一约束本就该放行），而是：
`escalate` 这一个动作名底下藏着两种语义 ——

- **推迟（R4：黑名单 / 高风险 / 大额）**：政策允许退，只是要人工把关。
- **禁止（R5：已退过 / R6：生鲜不退）**：政策不允许这笔钱出去。

修法是让 `rules.Decision` 用 `human_override_allowed` 把这两者分开，
写路径 `create_refund` 与 `service.approve_run` 共用 `rules.refund_is_forbidden()`
作为单一判据。

本脚本验证六件事（全部是断言，不是打印后肉眼比对）：
  1. R5 工单人工批准 → 被拒绝，且库里没有任何新退款，工单不被静默关闭
  2. R5 工单显式 override → 放行，返回值带标记，审计里留下 policy_override
  3. R4 工单人工批准 → 正常放行，且**不得**被误标为 override
  4. R6 工单人工批准 → 被拒绝（生鲜完全不退，优先级最高）
  5. 遍历全部 pending_human case，任何路径下已批准退款总额 ≤ 订单金额
  6. `refund_is_forbidden` 纯函数语义表

★ 每个检查前都重新 seed —— 检查 2/3 会真的写退款，共用状态会让
检查 4/5 读到被污染的订单（第一版脚本就是这么坏的：检查 3 退过的单
在检查 4 里因为"已批准 + 本次请求 > 订单额"被拒，看起来像护栏误伤，
其实是脚本自己没有隔离）。
"""

from __future__ import annotations

import datetime as dt
import sys
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")  # 否则 traceback 里的中文在 GBK 控制台变乱码
except Exception:  # pragma: no cover
    pass
sys.path.insert(0, "src")

from sqlalchemy import select  # noqa: E402

from ticket_agent.db import build_eval_cases, init_db, seed_all  # noqa: E402
from ticket_agent.db.models import AuditLog, Refund  # noqa: E402
from ticket_agent.db.session import TenantRepo, session_scope  # noqa: E402
from ticket_agent.db.snapshot import resolve_ticket_order  # noqa: E402
from ticket_agent.rules import PolicyViolation, decide, refund_is_forbidden  # noqa: E402
from ticket_agent.service import approve_run, run_once  # noqa: E402

N_CUSTOMERS, SEED, SIZE = 60, 7, 40
failures: list[str] = []
checks = 0


def fresh() -> tuple[list[dict], list[dict], dict[str, list[dict]]]:
    """重新造一份干净数据，返回 (cases, pending_human_cases, by_condition)。

    每个检查都必须从这里开始：前面的检查会真的写退款表，共用状态会让
    后续检查读到已被污染的订单，把护栏的正常拒绝误读成误伤。
    """
    init_db(drop=True)
    seed_all(n_customers=N_CUSTOMERS, seed=SEED)
    cases = build_eval_cases(size=SIZE, seed=SEED)
    esc = [c for c in cases if c["expected_state"]["ticket"]["status"] == "pending_human"]
    by_cond: dict[str, list[dict]] = {}
    for c in esc:
        by_cond.setdefault(c["condition"], []).append(c)
    return cases, esc, by_cond


def _refunds(snap: dict) -> list[tuple[str, float]]:
    return [(r["status"], r["amount"]) for r in (snap.get("refunds") or [])]


def _approved_total(snap: dict) -> float:
    return round(sum(r["amount"] for r in (snap.get("refunds") or []) if r["status"] == "approved"), 2)


def _escalate(case: dict):
    """跑一遍 agent，只有真的转人工才返回 (rec, snapshot)，否则 (None, None)。"""
    rec = run_once(case["tenant_id"], case["ticket_id"], arm="single")
    if rec.status != "awaiting_human":
        return None, None
    return rec, rec.snapshot or {}


def _order_refunds(tenant_id: str, ticket_id: int) -> tuple[int, int | None]:
    """(该订单的退款记录条数, 订单的 refund_count 字段)。"""
    with session_scope() as s:
        repo = TenantRepo(s, tenant_id)
        o, _ = resolve_ticket_order(repo, repo.get_ticket(ticket_id))
        n = len(list(s.execute(select(Refund).where(Refund.order_id == (o.id if o else -1))).scalars()))
        return n, (o.refund_count if o else None)


def _ticket_status(tenant_id: str, ticket_id: int) -> str:
    with session_scope() as s:
        return TenantRepo(s, tenant_id).get_ticket(ticket_id).status


def head(n: int, title: str) -> None:
    print()
    print("=" * 72)
    print(f"检查 {n}：{title}")
    print("=" * 72)


# ══════════════════════════════════════════════════════════════
head(1, "R5（政策禁止）人工批准 → 应被拒绝，不得产生退款，不得关闭工单")
_, esc, by_cond = fresh()
print(f"pending_human cases: {len(esc)}  条件分布: {dict(Counter(c['condition'] for c in esc))}")
r5 = by_cond.get("R5:escalate") or []
if not r5:
    print("⚠️ 本次 seed 没造出 R5:escalate 的 case —— 这个检查失去意义")
    failures.append("检查1：seed 未造出 R5 case")
else:
    c = r5[0]
    order = c["initial_state"].get("order") or {}
    print(f"case={c['case_id']}  订单 amount={order.get('amount')} refund_count={order.get('refund_count')}")
    print(f"初始退款: {[(r['status'], r['amount']) for r in (c['initial_state'].get('refunds') or [])]}")
    rec, before = _escalate(c)
    checks += 1
    if rec is None:
        print("⚠️ agent 未转人工，检查无法进行")
        failures.append("检查1：agent 未转人工")
    else:
        print(f"agent 处置后: refunds={_refunds(before)} 合计={_approved_total(before)}（agent 未动钱，正确）")
        try:
            out = approve_run(rec.run_id, c["tenant_id"], "approve_refund", note="检查1：应当被拒")
            print(f"✗ 人工批准**没有**被拒绝，返回：{out.get('performed')}")
            failures.append("检查1：R5 工单的人工批准未被护栏拒绝")
        except PolicyViolation as exc:
            print(f"✓ 被 PolicyViolation 拒绝：{str(exc)[:96]}…")
        n_after, rc_after = _order_refunds(c["tenant_id"], c["ticket_id"])
        n_before = len(c["initial_state"].get("refunds") or [])
        if n_after != n_before:
            print(f"✗ 退款条数从 {n_before} 变成了 {n_after} —— 被拒了，钱还是出去了")
            failures.append("检查1：被拒后仍产生了退款记录")
        else:
            print(f"✓ 库里退款条数仍为 {n_after}，refund_count={rc_after} —— 无副作用")
        tstat = _ticket_status(c["tenant_id"], c["ticket_id"])
        if tstat != "pending_human":
            print(f"✗ 工单状态变成 {tstat} —— 被拒的操作却把工单关掉了")
            failures.append("检查1：被拒后工单状态被改变")
        else:
            print("✓ 工单仍为 pending_human，未被静默关闭")

# ══════════════════════════════════════════════════════════════
head(2, "R5 显式 override=True → 应放行，且审计里留下 policy_override")
_, _, by_cond = fresh()  # 干净数据
r5 = by_cond.get("R5:escalate") or []
if not r5:
    print("⚠️ 无 R5 case，跳过")
else:
    c = r5[0]
    rec, _ = _escalate(c)
    checks += 1
    if rec is None:
        print("⚠️ agent 未转人工，跳过")
        failures.append("检查2：agent 未转人工")
    else:
        out = approve_run(rec.run_id, c["tenant_id"], "approve_refund", note="检查2：显式破例", override=True)
        snap = out["snapshot"]
        print(f"case={c['case_id']}  policy_override={out.get('policy_override')}")
        print(f"performed={[p.get('action') for p in out.get('performed')]}")
        print(f"退款后: refunds={_refunds(snap)} 合计={_approved_total(snap)} 订单额={(snap.get('order') or {}).get('amount')}")
        if not out.get("policy_override"):
            print("✗ override=True 却没有在返回值里标记 policy_override")
            failures.append("检查2：override 未标记")
        else:
            print("✓ 返回值带 policy_override=True（破例被显式记录）")
        with session_scope() as s:
            rows = list(
                s.execute(
                    select(AuditLog).where(
                        AuditLog.ticket_id == c["ticket_id"], AuditLog.action == "policy_override"
                    )
                ).scalars()
            )
        if not rows:
            print("✗ 审计日志里没有 policy_override 记录 —— 破例没有留痕")
            failures.append("检查2：override 未写审计")
        else:
            print(f"✓ 审计有 {len(rows)} 条 policy_override，规则={rows[0].payload.get('rule_hits')}"
                  f"，金额={rows[0].payload.get('amount')}")

# ══════════════════════════════════════════════════════════════
head(3, "R4（政策允许、需人工把关）人工批准 → 应正常放行，不得误标 override")
_, _, by_cond = fresh()
r4 = by_cond.get("R4:escalate") or []
if not r4:
    print("⚠️ 本次 seed 没造出 R4:escalate 的 case")
    failures.append("检查3：seed 未造出 R4 case")
else:
    c = r4[0]
    rec, _ = _escalate(c)
    checks += 1
    if rec is None:
        print("⚠️ agent 未转人工，跳过")
        failures.append("检查3：agent 未转人工")
    else:
        amt = (c["initial_state"].get("order") or {}).get("amount")
        print(f"case={c['case_id']}  订单 amount={amt}")
        try:
            out = approve_run(rec.run_id, c["tenant_id"], "approve_refund", note="检查3：R4 正常审批")
            snap = out["snapshot"]
            print(f"✓ 放行：performed={[p.get('action') for p in out.get('performed')]}")
            print(f"  refunds={_refunds(snap)} 合计={_approved_total(snap)}")
            if out.get("policy_override"):
                print("✗ R4 被标记为 override —— 它本就是允许退的，不该记破例")
                failures.append("检查3：R4 被误判为 override")
            else:
                print("✓ 未被标记 override（符合预期：R4 是推迟不是禁止）")
        except PolicyViolation as exc:
            print(f"✗ R4 被错误拒绝：{exc}")
            failures.append("检查3：R4 推迟被护栏错误拦下")

# ══════════════════════════════════════════════════════════════
head(4, "R6（生鲜完全不退，优先级最高）人工批准 → 应被拒绝")
_, _, by_cond = fresh()
r6 = by_cond.get("R6:escalate") or []
if not r6:
    print("⚠️ 本次 seed 没造出 R6:escalate 的 case")
    failures.append("检查4：seed 未造出 R6 case")
else:
    c = r6[0]
    rec, _ = _escalate(c)
    checks += 1
    if rec is None:
        print("⚠️ agent 未转人工，跳过")
        failures.append("检查4：agent 未转人工")
    else:
        cat = (c["initial_state"].get("order") or {}).get("category")
        print(f"case={c['case_id']}  订单 category={cat}")
        try:
            out = approve_run(rec.run_id, c["tenant_id"], "approve_refund", note="检查4：生鲜应被拒")
            print(f"✗ R6 生鲜的人工批准**没有**被拒绝：{out.get('performed')}")
            failures.append("检查4：R6 人工批准未被拒绝")
        except PolicyViolation as exc:
            print(f"✓ 被 PolicyViolation 拒绝：{str(exc)[:96]}…")
        n_after, _ = _order_refunds(c["tenant_id"], c["ticket_id"])
        if n_after != len(c["initial_state"].get("refunds") or []):
            print(f"✗ 生鲜订单产生了退款：{n_after} 条")
            failures.append("检查4：R6 被拒后仍产生退款")

# ══════════════════════════════════════════════════════════════
head(5, "遍历全部 pending_human case：已批准退款总额不得超过订单金额")
_, esc, _ = fresh()
overdrafts = []
for c in esc:
    rec, _snap = _escalate(c)
    if rec is None:
        continue
    checks += 1
    try:
        out = approve_run(rec.run_id, c["tenant_id"], "approve_refund", note="检查5")
    except PolicyViolation as exc:
        # 政策禁止 → 被拒是正确结局，但仍然要确认它没留下退款
        n_after, _ = _order_refunds(c["tenant_id"], c["ticket_id"])
        n_before = len(c["initial_state"].get("refunds") or [])
        ok = "✓" if n_after == n_before else "✗"
        print(f"  {ok} {c['case_id']:<14} {c['condition']:<16} 被拒绝（正确）：{str(exc)[:34]}…")
        if n_after != n_before:
            failures.append(f"检查5：{c['case_id']} 被拒后仍写入了退款")
        continue
    except ValueError as exc:
        # 例：no_order 的工单没有可处置订单 —— 也是正确的拒绝，但形态不同
        print(f"  ✓ {c['case_id']:<14} {c['condition']:<16} 被拒绝（无订单可裁决）：{str(exc)[:26]}…")
        continue
    snap = out["snapshot"]
    total, amt = _approved_total(snap), float((snap.get("order") or {}).get("amount") or 0.0)
    flag = "✓" if total <= amt + 0.01 else "✗"
    print(f"  {flag} {c['case_id']:<14} {c['condition']:<16} 退款合计 {total:>9.2f} / 订单额 {amt:>9.2f}")
    if total > amt + 0.01:
        overdrafts.append((c["case_id"], c["condition"], total, amt))
        failures.append(f"检查5：{c['case_id']} 超退 {total - amt:.2f} 元")

if overdrafts:
    print(f"\n✗ 发现 {len(overdrafts)} 例超退：")
    for v in overdrafts:
        print("   ", v)
else:
    print("\n✓ 没有任何 case 出现超退（这正是缺陷 ⑥ 当初的现象）")

# ══════════════════════════════════════════════════════════════
head(6, "refund_is_forbidden 的语义：推迟（可批） vs 禁止（不可批）")
now = dt.datetime.now(dt.timezone.utc)
semantics = (
    ("R6 生鲜", decide(order_status="signed", amount=100.0, categories={"生鲜"}, signed_at=now), True),
    ("R5 已退过", decide(order_status="signed", amount=100.0, categories={"图书"}, signed_at=now, refund_count=1), True),
    ("R4 大额", decide(order_status="signed", amount=900.0, categories={"图书"}, signed_at=now), False),
    ("R4 黑名单", decide(order_status="signed", amount=100.0, categories={"图书"}, signed_at=now, is_blacklisted=True), False),
    ("R4 高风险", decide(order_status="signed", amount=100.0, categories={"图书"}, signed_at=now, risk_score=0.95), False),
    ("R1 正常可退", decide(order_status="signed", amount=100.0, categories={"图书"}, signed_at=now), False),
    ("R2 内衣", decide(order_status="signed", amount=100.0, categories={"内衣"}, signed_at=now), True),
    ("R3 未签收", decide(order_status="shipped", amount=100.0, categories={"图书"}, signed_at=None), True),
)
for label, d, want in semantics:
    checks += 1
    got = refund_is_forbidden(d)
    mark = "✓" if got == want else "✗"
    if got != want:
        failures.append(f"检查6：{label} 期望 forbidden={want} 实得 {got}")
    print(f"  {mark} {label:<12} action={d.action:<16} override_allowed={str(d.human_override_allowed):<5} forbidden={got}")

# ══════════════════════════════════════════════════════════════
print()
print("=" * 72)
print(f"共 {checks} 项检查，失败 {len(failures)} 项")
if failures:
    print("✗✗ 失败明细：")
    for f in failures:
        print("   -", f)
    sys.exit(1)
print("✓✓ HITL 政策护栏全部通过：禁止的被拒绝、推迟的放行、破例有留痕、无超退")
