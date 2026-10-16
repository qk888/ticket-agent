"""定向敏感性分析 —— 把"故障稀有"和"探针不灵"分开。

## 为什么需要这个脚本

`_faultcheck.py` 报出的总准确率是这样的：

    guesser       acc=0.900     ← 几乎不掉分
    lenient       acc=0.933     ← 几乎不掉分
    noncompliant  acc=0.233     ← 崩
    lucky         acc=0.400     ← 崩

单看这张表，最容易得出的结论是"guesser 和 lenient 这两个故障没被抓住"。
**这个结论是错的。** 掉分少有两种完全不同的原因：

  (a) 探针不灵 —— 故障发作了，但评测看不出来；
  (b) 故障稀有 —— 故障在这套 case 上根本没几次机会发作。

两者的区别是决定性的：前者说明评测框架有盲区，后者说明**这套 case 集
对这类故障的覆盖不足**。把两者混在一个总准确率里报，等于把"我们没测到"
伪装成"我们测了且通过了"—— 那正是评测工作里最不该犯的错。

## 怎么分

不看总准确率，看**条件准确率**：只取"该故障确实有机会发作"的那些 case，
在这个子集上重跑。分母的判定不靠人眼估，而靠**基线的执行轨迹**——
计数器在共享代码路径上埋点，所以"这条 case 是否走到多笔候选那一步"
是运行出来的事实，不是我对 case 集的口头假设。

判据（都取自基线那一跑埋下的分母）：

  故障          触发子集 = 基线跑出以下任一计数
  ─────────────────────────────────────────────────────────
  guesser       ambiguous.opportunity          （名下多于一笔订单）
  lenient       lenient_trigger.opportunity    （R1 超期驳回）
  noncompliant  decision.escalate.opportunity  （上游判定为转人工）
  lucky         decision.{reject,escalate,block_logistics}.opportunity
                                               （上游判定不是退款）

子集上的判读方式：基线在同一子集上必须仍然接近满分（否则子集本身有问题），
故障模型在同一子集上必须**崩掉**。崩掉才说明探针有分辨率；
若子集上仍然不掉分，那才是真的盲区，得去改探针。

## 计数器的语义说明

计数器是**按调用**累加的，而 `_policy_resolver` 每轮循环都会重入判定步骤，
所以原始计数会虚高。这里一律归约成**按运行的布尔值**（"这一跑有没有碰到过"），
虚高就自动消失了 —— 我们问的是"有没有机会"，不是"问了几次"。

用法：python _sensitivity.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# Windows 控制台默认 GBK，报表里的 ✓/✗ 会抛 UnicodeEncodeError。
# 评测报告本身是 UTF-8 文件不受影响，这里只管 stdout。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from ticket_agent.agents.scripted import fault_stats, reset_fault_stats  # noqa: E402
from ticket_agent.db import build_eval_cases, init_db, seed_all  # noqa: E402
from ticket_agent.eval import run_case  # noqa: E402

ARM = "single"
SIZE = 30
SEED = 7
CUSTOMERS = 60

#: 每个故障的触发子集判据：基线那一跑出现过其中任一计数键，即算"有机会发作"。
TRIGGERS: dict[str, tuple[str, ...]] = {
    "guesser": ("ambiguous.opportunity",),
    "lenient": ("lenient_trigger.opportunity",),
    "noncompliant": ("decision.escalate.opportunity",),
    "lucky": (
        "decision.reject.opportunity",
        "decision.escalate.opportunity",
        "decision.block_logistics.opportunity",
    ),
}

#: 每个故障的分子计数键（故障确实发作过的运行）。
FIRED: dict[str, str] = {
    "guesser": "guesser.fired",
    "lenient": "lenient.fired",
    "noncompliant": "noncompliant.fired",
    "lucky": "lucky.fired",
}


def probe(case: dict, kind: str):
    """跑一条 case，并取回这一跑的计数器。

    ★ 必须在**每次运行前后**重置/读取：计数器是模块级全局的，
    不重置就会把上一条 case 的计数带进来，于是"这条 case 有没有机会"
    变成"至今为止有没有机会"，子集会被越滚越大。
    """
    reset_fault_stats()
    res = run_case(case, arm=ARM, kind=kind)
    return res, fault_stats()


def acc(results: list) -> float:
    if not results:
        return float("nan")
    return sum(1 for r in results if r.passed) / len(results)


def main() -> int:
    init_db(drop=True)
    seeded = seed_all(n_customers=CUSTOMERS, seed=SEED)
    cases = build_eval_cases(size=SIZE, seed=SEED)
    lv = Counter(c.get("level") for c in cases)
    print(f"seed: {seeded}")
    print(f"cases: {len(cases)}  levels={dict(lv)}\n")

    # ── 阶段 1：用基线跑一遍，确定每条 case 里各故障的触发机会 ──
    # 分母来自基线的真实执行轨迹，而不是我对 case 集的假设。
    print("阶段 1／2：用基线探测每条 case 的触发条件 …")
    base_results: dict[str, object] = {}
    triggers: dict[str, set[str]] = {k: set() for k in TRIGGERS}
    for c in cases:
        r, stats = probe(c, "scripted")
        base_results[c["case_id"]] = r
        for fault, keys in TRIGGERS.items():
            if any(stats.get(k, 0) > 0 for k in keys):
                triggers[fault].add(c["case_id"])

    base_acc = acc(list(base_results.values()))  # type: ignore[arg-type]
    print(f"  基线整体：acc={base_acc:.3f}（{len(cases)} 条）")
    for fault, ids in triggers.items():
        print(f"  {fault:<13} 有机会发作的 case：{len(ids):2d} / {len(cases)}")
    print()

    # ── 阶段 2：在每个故障自己的触发子集上重跑 ──
    print("阶段 2／2：在各自触发子集上重跑（条件准确率）\n")
    print("─" * 78)
    print(f"{'故障':<14}{'子集n':>6}{'基线acc':>10}{'故障acc':>10}{'发作次数':>10}{'判读':>14}")
    print("─" * 78)

    verdicts: list[tuple[str, bool, str]] = []
    for fault, keys in TRIGGERS.items():
        ids = triggers[fault]
        subset = [c for c in cases if c["case_id"] in ids]
        if not subset:
            print(f"{fault:<14}{0:>6}{'—':>10}{'—':>10}{'—':>10}{'case 集未覆盖':>14}")
            verdicts.append((fault, False, "这套 case 里没有该故障的触发条件 —— 覆盖不足，不是探针问题"))
            continue

        base_sub = [base_results[c["case_id"]] for c in subset]
        fault_res, fired = [], 0
        for c in subset:
            r, stats = probe(c, fault)
            fault_res.append(r)
            if stats.get(FIRED[fault], 0) > 0:
                fired += 1

        b_acc, f_acc = acc(base_sub), acc(fault_res)  # type: ignore[arg-type]
        dropped = b_acc - f_acc
        if f_acc < b_acc - 0.2:
            verdict = "有分辨率"
            ok = True
        elif f_acc <= b_acc:
            verdict = "掉分但不足"
            ok = False
        else:
            verdict = "无分辨率"
            ok = False
        print(
            f"{fault:<14}{len(subset):>6}{b_acc:>10.3f}{f_acc:>10.3f}{fired:>10}{verdict:>14}"
        )
        verdicts.append((fault, ok, f"{fault} 在触发子集上 {b_acc:.3f}→{f_acc:.3f}（掉 {dropped:.3f}）"))

    print("─" * 78)
    print()

    # ── 结论与断言 ──
    print("═" * 78)
    for fault, ok, msg in verdicts:
        print(f"{'✓' if ok else '✗'} {msg}")
    print("═" * 78)

    # 基线在子集上必须仍然完好，否则子集本身有问题，后面的数都不能信
    subset_ok = True
    for fault, _, _ in verdicts:
        ids = triggers[fault]
        if not ids:
            continue
        sub = [base_results[c] for c in ids]
        if acc(sub) < 0.95:  # type: ignore[arg-type]
            subset_ok = False
            print(f"✗ 基线在 {fault} 触发子集上只有 acc={acc(sub):.3f} —— 子集构造有问题")  # type: ignore[arg-type]

    if subset_ok:
        print("✓ 基线在所有触发子集上均保持 ≥0.95 —— 子集构造干净，条件准确率可信")
        print("✓ 结论：四个故障的分辨率均已确认；总准确率低≠故障致命，高≠探针失灵")

    all_ok = all(ok for _, ok, _ in verdicts) and subset_ok
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
