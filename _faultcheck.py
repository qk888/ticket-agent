"""故障注入分辨率验证：每种 kind 跑同一批 case，看失败签名是否**各不相同**。

验证目标不是"这些坏模型分数低"（那太容易了），而是：
  - 每个故障抓到的是不是**它自己注入的那个错**
  - 有没有哪个故障被框架漏掉（分数没掉 = 探针有盲区）
  - 过程轴（forbidden）是否独立于终态轴贡献了信息

★ 特别是 lucky：它的**终态准确率可能很高**，但如果 forbidden 计数为 0，
说明 forbidden 探针是摆设 —— 那才是真正的发现。

★ 为什么支持规模参数（`python _faultcheck.py 1000`）：

小样本下有些故障几乎不掉分，看起来"探针抓不住"，实际是**故障本身稀有**：
`guesser` 只在 `basis == ambiguous` 的 case 上有机会发作，30 条里只有 3 条；
它掉 0.067 不是探针钝，是样本里根本没有它犯错的机会。
只报总准确率会把这个事实藏起来 —— 读的人会以为"这个故障危害小"。

在千级样本上，ambiguous 有 53 条，同一个故障掉分立刻变得可见。
**样本量不是让数字好看，是让稀有故障从噪声里浮出来。**
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# Windows 控制台默认 GBK，报告里的 ✓/✗ 会抛 UnicodeEncodeError。
# 评测报告本身是 UTF-8 文件不受影响，这里只管 stdout。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from ticket_agent.agents.scripted import FAULT_MODELS  # noqa: E402
from ticket_agent.db import build_eval_cases, init_db, seed_all  # noqa: E402
from ticket_agent.eval import accuracy, detect_forbidden, run_suite  # noqa: E402


def main() -> int:
    n_customers = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    init_db(drop=True)
    seeded = seed_all(n_customers=n_customers, seed=7)
    cases = build_eval_cases(size=size, seed=7)
    print(f"seed: {seeded}")
    print(f"cases: {len(cases)}  levels={Counter(c['level'] for c in cases)}")
    print(f"basis:  {Counter(c['order_basis'] for c in cases)}")
    print(f"condition: {dict(sorted(Counter(c['condition'] for c in cases).items(), key=lambda x: -x[1]))}")
    print()

    kinds = ["scripted", "guesser", "lenient", "noncompliant", "lucky"]
    rows: list[tuple[str, float, int, int, dict[str, int], str]] = []

    for kind in kinds:
        results = run_suite(cases, arm="single", kind=kind, k=1, progress=False)
        acc = accuracy(results)
        forbidden = sum(len(r.forbidden_hits) for r in results)
        n_hit = sum(1 for r in results if r.forbidden_hits)
        hits = Counter(h for r in results for h in r.forbidden_hits)

        buckets: dict[str, int] = {}
        for r in results:
            if r.passed and not r.error:
                continue
            if r.error:
                key = "runtime_error"
            else:
                keys = set()
                for d in r.diffs:
                    if d.startswith("ticket."):
                        keys.add("ticket_status")
                    elif d.startswith("refunds:"):
                        keys.add("refund_ledger")
                    elif d.startswith("order."):
                        keys.add("order_state")
                    else:
                        keys.add("other")
                key = "+".join(sorted(keys))
            buckets[key] = buckets.get(key, 0) + 1

        sample = ""
        for r in results:
            if not r.passed and r.diffs:
                sample = r.diffs[0][:90]
                break

        rows.append((kind, acc, forbidden, n_hit, buckets, sample))
        print(f"── {kind} ──")
        print(f"   acc={acc:.3f}  forbidden={forbidden}（{n_hit} 条运行有违规）")
        print(f"   探针命中：{dict(hits) if hits else '（无）'}")
        print(f"   失败归因：{buckets if buckets else '（无失败）'}")
        if sample:
            print(f"   样例 diff：{sample}")
        print()

    # ── 判定 ─────────────────────────────────────────────────────────
    print("═" * 74)
    problems: list[str] = []
    by_kind = {r[0]: r for r in rows}

    base_acc = by_kind["scripted"][1]
    if base_acc != 1.0:
        problems.append(f"基线本身不是满分（acc={base_acc}），后续对比失去意义")

    for kind in kinds[1:]:
        _, acc, forbidden, _, buckets, _ = by_kind[kind]
        if acc < 1.0 or forbidden > 0:
            print(f"✓ {kind:<14} 被抓住：acc={acc:.3f}、forbidden={forbidden}")
        else:
            problems.append(f"{kind} **未被任何探针抓住** —— acc 与 forbidden 都没动")

    # lucky 的特殊断言：终态可能对，但 forbidden 必须非零
    lucky = by_kind["lucky"]
    if lucky[2] == 0:
        problems.append(
            f"lucky 的 forbidden=0（acc={lucky[1]:.3f}）—— 绕过规则判定却没被过程探针记录，"
            "说明 forbidden 探针存在盲区"
        )
    else:
        print(f"✓ lucky 过程探针生效：acc={lucky[1]:.3f} 却有 {lucky[2]} 次违规被独立记账")

    print("═" * 74)
    if problems:
        print("发现问题：")
        for p in problems:
            print(f"  ✗ {p}")
        return 1

    print("全部故障均被抓住，且 lucky 证明了过程轴独立于终态轴。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
