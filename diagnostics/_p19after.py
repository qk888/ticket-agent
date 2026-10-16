"""⑲ 回归验证：只重跑 multi 臂失败过的那 18 条 case，看 verifier 现在抓不抓得住。

为什么不全量重跑 40 条：
  一次 multi run 平均 81 秒，40 条约 55 分钟；而要回答的问题只有一个 ——
  "修复后 verifier 还会不会对那 21 条说'通过'"。那就只跑那 18 条（约 25 分钟）。

复现口径必须与出问题那一轮完全一致，否则不是同一个实验：
  seed=11 / customers=300 / size=40 / arm=multi / kind=llm，
  先 init_db(drop=True) + seed_all 重置，再从同一批 case 里挑出失败 ID。

输出 eval_out/p19-after.json：case_id, passed, verifier_passed, observed_action, problems。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ticket_agent.config import settings  # noqa: E402
from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import init_db  # noqa: E402
from ticket_agent.eval.harness import run_case  # noqa: E402
from ticket_agent.graph.nodes import set_model_kind  # noqa: E402

FAILED = json.loads(
    (settings.eval_dir / "_failed_cases.json").read_text(encoding="utf-8")
)

init_db(drop=True)
seed_all(n_customers=300, seed=11)
cases = build_eval_cases(size=40, seed=11)
# ★ 注意用 `case_id`（带租户前缀，如 `globex-t155`）而不是 `ticket_id`
#   —— `ticket_id` 是裸数字，"155" 在两个租户下都会命中，会选错 case。
wanted = [c for c in cases if c["case_id"] in set(FAILED)]
print(f"评测集重建 {len(cases)} 条，其中失败过的 {len(wanted)} 条会被重跑", flush=True)

set_model_kind("llm")
rows = []
for i, case in enumerate(wanted, 1):
    res = run_case(case, arm="multi", kind="llm", k_index=0)
    row = {
        "case_id": res.case_id,
        "condition": res.condition,
        "passed": res.passed,
        "verifier_passed": res.verifier_passed,
        "observed_action": res.observed_action,
        "diffs": res.diffs,
        "model_calls": res.model_calls,
        "tool_calls": res.tool_calls,
        "elapsed_ms": res.elapsed_ms,
        "error": res.error,
    }
    rows.append(row)
    print(
        f"[{i}/{len(wanted)}] {res.case_id:12s} {res.condition:18s} "
        f"passed={res.passed} verifier={res.verifier_passed} obs={res.observed_action}",
        flush=True,
    )

out = settings.eval_dir / "p19-after.json"
out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

fails = [r for r in rows if not r["passed"]]
missed = [r for r in fails if r["verifier_passed"]]
print("-" * 60)
print(f"重跑 {len(rows)} 条，仍失败 {len(fails)} 条")
print(f"其中 verifier 依然判'通过'的：{len(missed)} 条  <- 修复前是 21/22")
for r in missed:
    print(f"  {r['case_id']:12s} {r['condition']:18s} {r['diffs']}")
print(f"明细已写入 {out}")
