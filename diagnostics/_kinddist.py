"""量一量强制抽样（缺陷 ㉙）对**既有关键条件**的影响。

问题：`_KIND_FORCE_SHARE = 0.35` 会把 35% 的工单从自然分布（refund 占 52.8%）
拉走，于是依赖退款线的条件（尤其 R6:escalate = 生鲜 + 退款）机会减少。

本脚本对同一个 seed 分别用"开/关强制抽样"造数，对比条件分布，
用一个能扫的阈值找出**既保住覆盖、又不伤既有关键条件**的比例。
"""
from __future__ import annotations

import collections
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'eval_out' / 'kinddist.db'}")

from ticket_agent.db import seed as seed_mod  # noqa: E402
from ticket_agent.db.session import init_db  # noqa: E402
from ticket_agent.db.seed import build_eval_cases  # noqa: E402

KEY_CONDS = ["R6:escalate", "R4:escalate", "R5:escalate", "R3:block_logistics", "no_order", "ambiguous"]


def run_once(share: float, size: int = 40, customers: int = 60) -> collections.Counter:
    seed_mod._KIND_FORCE_SHARE = share
    init_db(drop=True)
    seed_mod.seed_all(n_customers=customers, seed=7)
    cases = build_eval_cases(size=size, seed=7)
    return collections.Counter(c.get("condition") or "?" for c in cases)


print(f"{'share':>6} {'case数':>6} {'条件数':>6}  关键条件命中")
print("-" * 78)
results = {}
for share in (0.0, 0.10, 0.15, 0.20, 0.25, 0.35):
    dist = run_once(share)
    results[share] = dist
    key = "  ".join(f"{c.split(':')[0]}={dist.get(c, 0)}" for c in KEY_CONDS[:4])
    print(f"{share:>6.2f} {sum(dist.values()):>6} {len(dist):>6}  {key}")
    print(f"{'':>22}缺：{[c for c in ['R6:escalate','R10:compensate','R11:price_protect','R13:change_address'] if dist.get(c, 0) == 0]}")

print()
print("=" * 78)
print("放宽到 300 case 时，各比例下的目标缺口是否补上")
print("=" * 78)
for share in (0.0, 0.20):
    dist = run_once(share, size=300, customers=400)
    print(f"\nshare={share}: 共 {sum(dist.values())} 条 case、{len(dist)} 种条件")
    for c in ["R10:compensate", "R11:price_protect", "R13:change_address", "R6:escalate"]:
        print(f"    {c:<24} {dist.get(c, 0)}")
