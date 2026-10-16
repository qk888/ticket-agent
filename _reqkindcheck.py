"""诉求维度（request_kind）端到端检查：语料 → 工单 → golden → 评测报表。

存在理由：新加的七个非退款动作必须**真的进得了报表**。这件事有三段，
任何一段断了都不会报错 —— 语料认不出诉求、工单没落库、golden 分支缺失，
三者的症状完全一样：报表里那几条条件就是 0 条 case，看起来像"样本少"。

跑法（必须用独立库，不能碰正在跑的评测库）：
    $env:DATABASE_URL="sqlite:///./eval_out/reqkind.db"
    python -X utf8 _reqkindcheck.py
"""

from __future__ import annotations

import os
import sys

if "reqkind" not in os.environ.get("DATABASE_URL", ""):
    print("拒绝运行：DATABASE_URL 不含 reqkind，怕污染别的库。")
    print('请先设 $env:DATABASE_URL="sqlite:///./eval_out/reqkind.db"')
    raise SystemExit(2)

from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import init_db, session_scope  # noqa: E402
from ticket_agent.eval.harness import run_suite  # noqa: E402
from ticket_agent.eval.report import (  # noqa: E402
    EXPECTED_CONDITIONS,
    build_report,
    coverage_gaps,
)

init_db(drop=True)
# 样本量可调：默认 60 客户 / 40 case 是日常快检；扩到 400 客户 / 300 case
# 是为了回答一个特定问题 —— 稀有诉求（补偿 3.3%、改址 2.0%、价保）在小样本里
# 一条都抽不到，报表上表现为"这些规则永远 0 条 case"。
# ★ 而**"没抽到"和"死代码"在报表上完全同形**，所以必须用大样本证伪后者。
n_customers = int(sys.argv[1]) if len(sys.argv) > 1 else 60
n_cases = int(sys.argv[2]) if len(sys.argv) > 2 else 40
stats = seed_all(n_customers, 90, 7)

print("造数统计里的诉求分布（{} 客户）：".format(n_customers))
for k in sorted(stats):
    if k.startswith("request_"):
        print("   {:26s} {}".format(k, stats[k]))

cases = build_eval_cases(size=n_cases, seed=7)
res = run_suite(cases, arm="single", kind="scripted")
rep = build_report(res, k=1)
by_cond = rep["by_arm"]["single"].get("by_condition", {})

total = len(res)
passed = sum(1 for r in res if r.passed)
print("\n总 {} case，通过 {}，准确率 {:.4f}".format(total, passed, passed / total))

print("\n条件准确率（键名见 harness.stratify_by_condition：n/share/accuracy）：")
for cond in sorted(by_cond):
    st = by_cond[cond]
    print(
        "   {:26s} n={:<3d} acc={:.3f} 占比={:.2%}".format(
            cond, int(st["n"]), st["accuracy"], st["share"]
        )
    )

gaps = coverage_gaps(res, EXPECTED_CONDITIONS)
print("\n覆盖缺口：", gaps or "无")

fails = [r for r in res if not r.passed]
if fails:
    print("\n失败明细：")
    for r in fails:
        print("   {:14s} {:22s} {}".format(r.case_id, r.condition, "; ".join(r.diffs)))
