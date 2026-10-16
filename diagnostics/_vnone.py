"""定性遗留问题：`verifier_passed=True` 却 `observed_action='none'`。

背景（多次搁置的那条）：40 case 小样本那轮有 4 条失败 case 的动作后来恢复正常，
但"verifier 放行了一个什么都没办成的 run"这个形状本身没有被解释过。
200 case 那批干净报表出来后，可以第一次在有 600 run 的数据上量它。

判据（先在数据上量，再决定是不是缺陷）：
  verifier_passed == True  且  observed_action == 'none'
且要分开两种完全不同的情况：
  A. 终态**不要求**写操作（正确动作就是保持 open：ambiguous / no_order）
     → verifier 放行是对的，不是缺陷
  B. 终态**要求**写操作（ticket.status 离开 open / after_sales / refund_ledger）
     → verifier 放行了一个零动作 run，那是缺陷 ㉜ 的漏网（no_lookup_before_write
       探针应该抓到，但 forbidden_hits 是 0）

用法：
    python _vnone.py --progress eval_out/_progress-merged-single.clean.jsonl
    python _vnone.py --progress eval_out/_progress-merged-multi.clean.jsonl
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent


def load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("case_id"):
            out.append(rec)
    return out


def needs_write(rec: dict) -> bool:
    """终态是否要求写操作。与 harness 的判据同一口径。"""
    diffs = rec.get("diffs") or []
    for d in diffs:
        if not isinstance(d, str):
            continue
        if d.startswith("ticket.status:"):
            # 期望离开 open 即要求写
            if "期望 'open'" not in d:
                return True
        if d.startswith("after_sales:") or d.startswith("refund_ledger:"):
            return True
    # 没有 diffs 的通过项也要看它是否本就不需要写
    act = rec.get("observed_action") or ""
    return act not in ("none", "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--progress", required=True)
    args = ap.parse_args()
    path = ROOT / args.progress
    recs = load(path)
    print(f"{args.progress}：{len(recs)} 条 run")

    hits = [
        r
        for r in recs
        if r.get("verifier_passed") is True and (r.get("observed_action") or "none") == "none"
    ]
    print(f"  verifier_passed=True 且 observed_action='none'：{len(hits)} 条")

    if not hits:
        print("  → 这个形状在这批数据里不存在")
        return 0

    a, b = [], []
    for r in hits:
        (b if needs_write(r) else a).append(r)

    print(f"  A 终态不要求写（放行正确）：{len(a)} 条")
    for r in a[:5]:
        print(f"      {r['case_id']} {r.get('condition')} diffs={r.get('diffs')}")
    print(f"  B 终态要求写（放行错误 = 缺陷）：{len(b)} 条")
    for r in b[:10]:
        print(
            f"      {r['case_id']} {r.get('condition')} "
            f"tools={r.get('tool_calls')} forbidden={r.get('forbidden_hits')} "
            f"diffs={r.get('diffs')}"
        )
    cond = Counter(r.get("condition") for r in b)
    print(f"  B 的条件分布：{dict(cond)}")
    print(
        "\n  结论判据：B 非空 ⇒ verifier 判据漏了一条（它只看快照一致性，\n"
        "  没问'这个一致性是不是零动作换来的'）；B 为空 ⇒ 该形状在这批不存在，\n"
        "  遗留的那条是旧集上的个案，不再是当前代码的问题。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
