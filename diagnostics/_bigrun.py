"""千级规模评测 —— 回答"就那么点 case 跑"的问题。

用户原话（m04618）：「数据集请从网上下载大量的数据集而不是就那么点跑」。

这个脚本做三件事：
  ① 用真实语料 + 大规模造数（默认 1000 客户）跑两臂评测；
  ② 报出**每个条件、每种风格**的准确率（大样本下这些桶才有统计意义）；
  ③ 报出语料**复用率** —— 862 条真实话术铺到上千条工单上必然重复，
     这个数字必须说清楚，而不是只报"我们用了真实语料"。

用法：
    python _bigrun.py [客户数] [k]
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from ticket_agent.db import build_eval_cases, init_db, seed_all  # noqa: E402
from ticket_agent.db import corpus  # noqa: E402
from ticket_agent.eval import run_suite  # noqa: E402


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    t0 = time.time()
    init_db(drop=True)
    st = seed_all(n_customers=n, seed=7)
    cases = build_eval_cases(size=10**6, seed=7)
    print(f"造数：{st}")
    print(f"评测集：{len(cases)} 条（{time.time() - t0:.1f}s）")

    # ── 语料复用率：真实话术条数 vs 工单条数 ──────────────────
    cs = corpus.corpus_stats()
    n_tickets = st.get("tickets", 0)
    uniq = cs["unique_phrasings"]
    print(f"\n语料：{cs['file']}  sha256={cs['sha256'][:16]}")
    print(f"  唯一话术 {uniq} 条  →  工单 {n_tickets} 条")
    print(f"  平均复用 {n_tickets / max(uniq, 1):.2f} 次/句"
          f"（原先 9 条模板时是 {n_tickets / 9:.1f} 次/句）")
    print(f"  按业务层的可选池：{cs['by_business_level']}")

    # ── 分布 ────────────────────────────────────────────────
    for dim in ("level", "condition", "style"):
        d: dict[str, int] = {}
        for c in cases:
            d[c[dim]] = d.get(c[dim], 0) + 1
        top = dict(sorted(d.items(), key=lambda x: -x[1]))
        print(f"\n{dim} 分布（{len(top)} 种）：{top}")

    # ── 两臂评测 ────────────────────────────────────────────
    summary: dict[str, dict] = {}
    for arm in ("single", "multi"):
        t1 = time.time()
        try:
            res = run_suite(cases, arm=arm, kind="scripted", k=k, progress=False)
        except TypeError:
            # 老签名没有 progress 参数
            res = run_suite(cases, arm=arm, kind="scripted", k=k)
        dt = time.time() - t1
        n_ok = sum(1 for r in res if getattr(r, "passed", False))
        acc = n_ok / max(len(res), 1)
        summary[arm] = {"acc": acc, "n": len(res), "sec": dt, "res": res}

        print(f"\n{'=' * 62}\narm={arm}  accuracy={acc:.4f} ({n_ok}/{len(res)})  {dt:.1f}s"
              f"\n{'=' * 62}")

        # 分层 / 分条件 / 分风格
        for dim, label in (("level", "难度"), ("condition", "条件"), ("style", "风格")):
            buckets: dict[str, list] = {}
            for r in res:
                key = getattr(r, dim, "?") or "?"
                buckets.setdefault(key, []).append(r)
            rows = []
            for key, rs in buckets.items():
                ok = sum(1 for x in rs if getattr(x, "passed", False))
                rows.append((key, len(rs), ok / len(rs), ok))
            rows.sort(key=lambda x: -x[1])
            print(f"\n-- 按{label} --")
            for key, cnt, a, ok in rows:
                flag = "" if a >= 1.0 else "   ← 掉分"
                print(f"   {key:<20} n={cnt:<5} acc={a:.3f} ({ok}/{cnt}){flag}")

        # 成本与违规
        tot_cost = sum(getattr(r, "cost_cny", 0.0) or 0.0 for r in res)
        forb = sum(len(getattr(r, "forbidden_hits", []) or []) for r in res)
        tools = [getattr(r, "tool_calls", 0) or 0 for r in res]
        calls = [getattr(r, "model_calls", 0) or 0 for r in res]
        print(f"\n-- 成本 --\n   总成本 {tot_cost:.4f} 元；均成本 {tot_cost / len(res):.5f}")
        print(f"   平均工具调用 {sum(tools) / len(tools):.2f}；"
              f"平均模型调用 {sum(calls) / len(calls):.2f}")
        print(f"   违规动作合计 {forb}")

    # ── 两臂对照 ────────────────────────────────────────────
    print(f"\n{'=' * 62}\n两臂对照\n{'=' * 62}")
    a, b = summary["single"], summary["multi"]
    print(f"  准确率：single {a['acc']:.4f}  vs  multi {b['acc']:.4f}")
    print(f"  耗时：  single {a['sec']:.1f}s  vs  multi {b['sec']:.1f}s"
          f"（{b['sec'] / max(a['sec'], 0.001):.1f}×）")


if __name__ == "__main__":
    main()
