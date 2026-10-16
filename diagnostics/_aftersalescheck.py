"""售后动作端到端验收：新工具能不能真办事、三道护栏拦不拦得住。

用法（必须用独立库，别碰正在跑的评测库）：

    $env:DATABASE_URL="sqlite:///./eval_out/ascheck.db"
    python _aftersalescheck.py

它逐条断言，不是"跑通就算过"：
  ① 正常受理：补发（缺件）→ 写库、计数器 +1、快照能看见
  ② 政策复核：拿"补发"去请求一个政策结论是 reject 的场景 → 必须被拦
  ③ 账本不变量：赔付超订单金额 → 必须被拦
  ④ 幂等：同参数提交两次 → 第二次 duplicate，不写第二笔
  ⑤ 出钱动作合并计算：已有退款 + 新赔付 > 订单金额 → 必须被拦（这是缺陷 ⑥ 的推广面）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from ticket_agent.db import init_db, session_scope  # noqa: E402
from ticket_agent.db.models import AfterSalesAction, Order  # noqa: E402
from ticket_agent.db.session import TenantRepo  # noqa: E402
from ticket_agent.db.seed import seed_all  # noqa: E402

# ★ 必须 import 子模块，不能写 `from ticket_agent.tools import registry`：
#   包的 __init__ 里 `registry` 这个名字被一个 ToolRegistry **实例**占了，
#   所以那种写法拿到的是实例，`R.set_tool_context` 会 AttributeError。
from ticket_agent.tools import registry as _registry_pkg  # noqa: E402

R = __import__("ticket_agent.tools.registry", fromlist=["*"])

PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(label)
    print(f"  [{'✓' if ok else '✗'}] {label}" + (f" —— {detail}" if detail else ""))


def main() -> int:
    db_url = os.environ.get("DATABASE_URL", "")
    if "ascheck" not in db_url:
        print("!! 拒绝执行：DATABASE_URL 不是 ascheck 专用库，避免污染正在跑的评测库")
        print("   实际值：", db_url or "(未设置，会用默认库)")
        return 2

    print("=" * 72)
    print("售后动作端到端验收")
    print("=" * 72)
    print(f"库：{db_url}\n")

    stopped = init_db(drop=True)
    stats = seed_all(n_customers=30, seed=5)
    print(f"灌数据：{stats}\n")

    # 挑一张 shipped、非生鲜、非不可退类的订单，用来做补发
    with session_scope() as s:
        orders = s.query(Order).filter(Order.tenant_id == "acme").all()
        shipped = [
            o for o in orders
            if o.status == "shipped" and (o.category or "") not in ("生鲜", "内衣", "定制", "数字商品")
        ]
        target = shipped[0]
        t_order_id = target.id
        t_order_amount = float(target.amount)
        t_category = target.category

        # ★ 把一张工单挂到这个订单上：快照是**按工单**导出的，
        #   不挂的话 snapshot 里看不到我们操作的订单，diff 也就验不了。
        from ticket_agent.db.models import Ticket
        tk = s.query(Ticket).filter(Ticket.tenant_id == "acme").first()
        tk.order_id = t_order_id
        t_ticket_id = tk.id
        s.flush()
    print(f"测试订单：#{t_order_id}  状态=shipped  类目={t_category}  金额={t_order_amount}")
    print(f"测试工单：#{t_ticket_id}（已挂到该订单，快照按工单导出）\n")

    R.set_tool_context(tenant_id="acme", ticket_id=t_ticket_id, actor="resolver", run_id="ascheck")

    from ticket_agent.db.snapshot import diff_snapshots, export_snapshot

    def snap_now() -> dict:
        with session_scope() as s:
            return export_snapshot(s, "acme", t_ticket_id)

    snap_before = snap_now()
    check("动作前快照 after_sales 为空", snap_before.get("after_sales") == [],
          f"实际 {snap_before.get('after_sales')}")

    # ── ① 正常受理 ────────────────────────────────────────
    print("① 正常受理：补发（原因=缺件）")
    r1 = R.submit_after_sales_action(order_id=t_order_id, kind="reship", cause="missing")
    check("补发被受理", r1.get("ok") is True, str(r1)[:160])
    check("返回了 action_id", isinstance(r1.get("action_id"), int), f"action_id={r1.get('action_id')}")

    with session_scope() as s:
        n = s.query(AfterSalesAction).filter(AfterSalesAction.order_id == t_order_id).count()
        o = s.query(Order).filter(Order.id == t_order_id).one()
        reship_count = o.reship_count
    check("落库 1 条动作", n == 1, f"实际 {n}")
    check("订单计数器 reship_count=1", reship_count == 1, f"实际 {reship_count}")

    # ★ 这是评测真正依赖的东西：diff_snapshots 必须看得见这次动作
    snap_after = snap_now()
    ok, diffs = diff_snapshots(snap_after, snap_after)
    check("自己和自己 diff 无差异", ok is True, f"diffs={diffs}")
    ok2, diffs2 = diff_snapshots(snap_before, snap_after)
    check("动作前后 diff 报出差异", ok2 is False, f"diffs={diffs2}")
    check("差异里含 after_sales", any("after_sales" in d for d in diffs2), f"diffs={diffs2}")
    check("差异里含 reship 账本", any("reship_count" in d for d in diffs2), f"diffs={diffs2}")
    print(f"    diff 明细：{diffs2}")

    # ── ④ 幂等（缺陷 ㉑ 的正面验收）────────────────────────
    print("\n④ 幂等：同参数再提交一次（★ 必须返回 duplicate，不能是 policy_forbidden）")
    r_dup = R.submit_after_sales_action(order_id=t_order_id, kind="reship", cause="missing")
    check("第二次返回 duplicate", r_dup.get("duplicate") is True, str(r_dup)[:200])
    check("第二次不是 policy_forbidden", r_dup.get("error") != "policy_forbidden",
          f"error={r_dup.get('error')!r}")
    check("duplicate 里带回了原有 action_id", isinstance(r_dup.get("action_id"), int),
          f"action_id={r_dup.get('action_id')}")
    with session_scope() as s:
        n2 = s.query(AfterSalesAction).filter(AfterSalesAction.order_id == t_order_id).count()
    check("库里仍只有 1 条", n2 == 1, f"实际 {n2}")

    # ── ② 政策复核：请求动作与政策结论不一致 ────────────────
    print("\n② 政策复核：对已取消订单请求'开票'（政策结论应为 reject）")
    with session_scope() as s:
        # ★ 不能挑 pending_paid：那个状态太稀有（30 客户里为 0），
        #   而 R12 对**已取消**订单同样给 reject，且 cancelled 一定有。
        closed = s.query(Order).filter(Order.tenant_id == "acme", Order.status == "cancelled").first()
        closed_id = closed.id if closed else None
    if closed_id:
        r2 = R.submit_after_sales_action(order_id=closed_id, kind="invoice")
        check("已取消订单开票被拦", r2.get("error") == "policy_forbidden", str(r2)[:200])
        check("拦下时给出了政策结论", r2.get("policy_action") == "reject", f"policy_action={r2.get('policy_action')}")
        check("拦下时没有写库", "action_id" not in r2)
    else:
        check("已取消订单开票被拦", False, "找不到 cancelled 订单，测试未执行")

    # 未知 kind
    r2b = R.submit_after_sales_action(order_id=t_order_id, kind="bogus")
    check("未知 kind 被拒", r2b.get("error") == "invalid_kind", str(r2b)[:120])

    # ── ③ 账本不变量：赔付超订单金额 ───────────────────────
    # ★ 金额要选在 R10 上限（30 元）**之内**、但在订单金额（39.21）**之外**是不可能的，
    #   因为订单金额 > 上限。所以这里换一张金额较大、且还没被占用的订单来测：
    print("\n③ 账本不变量：赔付金额超过订单金额（选一张金额 > 30×2 的订单）")
    with session_scope() as s:
        big = (
            s.query(Order)
            .filter(Order.tenant_id == "acme", Order.status == "signed", Order.amount > 200)
            .first()
        )
        big_id = big.id if big else None
        big_amount = float(big.amount) if big else 0.0
    if big_id:
        # 用 29 元（在 R10 上限内）连做两笔赔付，使累计超出订单金额
        # 更直接的测法：把订单金额改小到 20，再请求 25 元赔付 → 必须被账本不变量拦
        with session_scope() as s:
            o = s.query(Order).filter(Order.id == big_id).one()
            o.amount = 20.0
            s.flush()
        r3 = R.submit_after_sales_action(
            order_id=big_id, kind="compensate", cause="damaged", amount=25.0,
        )
        check("超额赔付被拦", r3.get("error") == "amount_exceeds_order", str(r3)[:220])
        if r3.get("error") == "amount_exceeds_order":
            check("拦下时报出订单金额", float(r3.get("order_amount", 0)) == 20.0,
                  f"order_amount={r3.get('order_amount')}")
        with session_scope() as s:
            n3 = s.query(AfterSalesAction).filter(
                AfterSalesAction.order_id == big_id,
                AfterSalesAction.kind == "compensate",
            ).count()
        check("超额赔付没有落库", n3 == 0, f"实际 {n3}")
        # 恢复
        with session_scope() as s:
            o = s.query(Order).filter(Order.id == big_id).one()
            o.amount = big_amount
            s.flush()
    else:
        check("超额赔付被拦", False, "找不到金额 > 200 的 signed 订单，测试未执行")

    # ── ⑤ 出钱动作合并计算：已有退款占用额度 ────────────────
    print("\n⑤ 出钱动作合并：先用满订单金额做退款，再请求赔付")
    with session_scope() as s:
        # 直接造一笔已批准退款，占满订单金额
        # ★ Refund 没有 tenant_id 字段（租户通过 order 关联），别传
        from ticket_agent.db.models import Refund
        s.add(Refund(
            order_id=t_order_id, amount=t_order_amount,
            reason="测试占满额度", status="approved", idempotency_key="ascheck-full-refund",
        ))
        s.flush()
    r5 = R.submit_after_sales_action(
        order_id=t_order_id, kind="compensate", cause="damaged", amount=10.0,
    )
    check("额度已满时赔付被拦", r5.get("error") == "amount_exceeds_order", str(r5)[:220])
    if r5.get("error") == "amount_exceeds_order":
        check("拦下时报告了已退款金额", float(r5.get("already_refunded", 0)) > 0,
              f"already_refunded={r5.get('already_refunded')}")

    # ── ⑥ 只读工具 ────────────────────────────────────────
    print("\n⑥ 只读工具")
    r6 = R.list_after_sales_of_order(t_order_id)
    check("能列出已有动作", r6.get("count") == 1, f"count={r6.get('count')}")
    r6b = R.check_after_sales_eligibility(t_order_id, "reship", cause="damaged")
    check("资格工具返回 action 字段", "action" in r6b, str(r6b)[:160])

    # ── 汇总 ──────────────────────────────────────────────
    print("\n" + "=" * 72)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("\n失败项：")
        for f in FAIL:
            print("  ✗", f)
    print("=" * 72)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
