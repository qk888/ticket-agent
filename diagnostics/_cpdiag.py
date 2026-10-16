"""精确定位"续跑"到底是哪种语义 —— 我原先写的注释对不对。

待验证的三个可能：
  A. 第二遍从 checkpoint **恢复旧状态当输入**，再叠加新输入 → 消息累积（已观察到 11→22）
  B. 第二遍**跳过**已执行过的节点/工具调用 → 会有副作用丢失或重复
  C. 两者的组合

关键要回答的问题（决定 README 怎么写）：
  第二遍运行时，工具**实际执行了几次**？会不会把钱退了两遍？
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "src")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from sqlalchemy import select  # noqa: E402

from ticket_agent.config import get_settings  # noqa: E402
from ticket_agent.db.models import AuditLog, Refund  # noqa: E402
from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import init_db, session_scope  # noqa: E402
from ticket_agent.graph.builder import run_ticket  # noqa: E402
from ticket_agent.graph.checkpoint import _CACHE  # noqa: E402

os.environ["CHECKPOINTER"] = "sqlite"
get_settings.cache_clear()
import ticket_agent.config as _cfg  # noqa: E402
import ticket_agent.graph.checkpoint as _ckpt  # noqa: E402

_cfg.settings = get_settings()
_ckpt.settings = _cfg.settings
_CACHE.clear()

init_db(drop=True)
seed_all(n_customers=60, seed=7)
cases = build_eval_cases(size=40, seed=7)

# 挑一条**会真的写退款**的 case（R1:refund），这样副作用可观测
c = next(x for x in cases if x["condition"] == "R1:refund")
print(f"case: {c['case_id']} / {c['condition']} / ticket {c['ticket_id']}")
print(f"期望终态: {c['expected_state']['ticket']['status']}")
print()

THREAD = "probe-dup-side-effect"


def side_effects(tag: str) -> None:
    with session_scope() as s:
        refunds = s.execute(select(Refund)).scalars().all()
        logs = s.execute(select(AuditLog).where(AuditLog.ticket_id == c["ticket_id"])).scalars().all()
    print(f"  [{tag}] 退款记录数={len(refunds)} 审计条数={len(logs)}")
    print(f"  [{tag}] 审计动作: {[l.action for l in logs]}")


def run(tag: str) -> dict:
    st = run_ticket(c["tenant_id"], c["ticket_id"], arm="single",
                    run_id=THREAD, checkpointer=None)
    msgs = st.get("messages") or []
    tc = st.get("tool_calls") or []
    print(f"  [{tag}] 消息 {len(msgs)} 条 / tool_calls 累计 {len(tc)}")
    print(f"  [{tag}] 本次消息里出现的 tool 名: "
          f"{[getattr(m, 'name', None) for m in msgs if getattr(m, 'name', None)]}")
    return st


print("=" * 72)
print("第一遍（干净库）")
print("=" * 72)
side_effects("前")
s1 = run("第1遍")
side_effects("后")

print()
print("=" * 72)
print("第二遍（同一 thread_id，库已被第一遍改过）")
print("=" * 72)
side_effects("前")
s2 = run("第2遍")
side_effects("后")

print()
print("=" * 72)
print("诊断")
print("=" * 72)
n1 = len(s1.get("messages") or [])
n2 = len(s2.get("messages") or [])
print(f"  消息数：第1遍 {n1} → 第2遍 {n2}")
if n2 == 2 * n1:
    print("  → 累积（第2遍的输入里带着第1遍的全部历史）")
elif n2 == n1:
    print("  → 完全复用旧状态，第2遍什么也没做")
else:
    print(f"  → 既非纯累积也非纯复用，增量 {n2 - n1} 条")

tc1 = len(s1.get("tool_calls") or [])
tc2 = len(s2.get("tool_calls") or [])
print(f"  tool_calls：第1遍 {tc1} → 第2遍 {tc2}")

os.environ.pop("CHECKPOINTER", None)
get_settings.cache_clear()
_CACHE.clear()
