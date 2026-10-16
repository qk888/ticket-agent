"""验收：缺陷 ⑨ 修复后，固定 seed 的评测集还会随墙上时钟漂移吗？

## 为什么要重写这个脚本

前两版用的是"把 `rules.dt` 换成偏移版"来模拟数据老化。修复后 `rules._days_since`
读的是 `clock.now()`（锚点），**平移 `rules.dt` 已经不起作用了** —— 一个测不出
任何东西的诊断脚本，和一个从不失败的测试一样可疑。

所以新版测的是修复本身要成立的那句话：

  **锚点在库里，墙上时钟怎么走都不影响判定。**

## 两组对照（缺一不可）

- ① **正向**：锚点已在库（修复后的正常状态）→ 墙上时钟前进 +12 小时 / +1 天 /
  +3 天 / +7 天 / +30 天，评测集必须**逐条一致**。
- ② **阴性对照**：把锚点清掉（模拟"真实业务库，没有锚点"= 修复前的行为），
  同样的墙上时钟偏移 → 评测集**必须翻转**。

  这一组是关键：没有它，① 通过也可能只是因为诊断脚本根本没接线到判定路径。
  ② 复现的正是 `_faultcheck.py` 数字抖动那次的病 —— 修复的目的不是让数字好看，
  是让基准不再移动；② 证明它以前确实在移动。
"""

import datetime as dt
import sys

sys.path.insert(0, "src")

from ticket_agent import clock, rules as rules_mod  # noqa: E402
from ticket_agent.db import build_eval_cases, init_db, seed_all  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OFFSETS = ((0.5, "+12 小时"), (1, "+1 天"), (3, "+3 天"), (7, "+7 天"), (30, "+30 天"))


class _ShiftedDT:
    """`datetime` 属性被换成"偏移版"：`now()` 返回 delta 之后的时间。

    模拟"墙上时钟往前走"。锚点在库时这个偏移**不应**产生任何影响 ——
    那正是修复要保证的事；锚点被清掉时它应该立刻显形。

    ★ 必须替换 `datetime` 属性本身并返回 datetime 的**子类**：
      `clock.now()` 的回落分支写的是 `dt.datetime.now(tz)`，
      属性链断在哪一层都白改（第一版就踩了这个坑）。
    """

    def __init__(self, delta: dt.timedelta) -> None:
        self._delta = delta

    def __getattr__(self, name: str):
        if name == "datetime":
            delta = self._delta

            class _DT(dt.datetime):
                @classmethod
                def now(cls, tz=None):
                    return dt.datetime.now(tz) + delta

            return _DT
        return getattr(dt, name)


def by_id(cases: list[dict]) -> dict[str, str]:
    return {c["case_id"]: c.get("condition", "?") for c in cases}


def dist(cases: list[dict]) -> str:
    d: dict[str, int] = {}
    for c in cases:
        d[c.get("condition", "?")] = d.get(c.get("condition", "?"), 0) + 1
    return " ".join(f"{k}:{v}" for k, v in sorted(d.items()))


def compare(label: str, base: dict[str, str], cur: dict[str, str]) -> int:
    """按 case_id 比对（★ 不能用 zip：指纹是排序后的列表，condition 一变顺序就错位）。"""
    only_base = sorted(set(base) - set(cur))
    only_cur = sorted(set(cur) - set(base))
    flipped = [(k, base[k], cur[k]) for k in sorted(set(base) & set(cur)) if base[k] != cur[k]]
    same = len(set(base) & set(cur)) - len(flipped)
    tag = "✗ 变了" if (flipped or only_base or only_cur) else "✓ 一致"
    print(f"  {label:<12} {tag:<8} 相同 {same:<3} 条件翻转 {len(flipped):<3} "
          f"仅在基准 {len(only_base):<3} 仅在偏移后 {len(only_cur)}")
    for k, a, b in flipped[:6]:
        print(f"                 {k}: {a} → {b}")
    if only_base:
        print(f"                 仅基准有: {', '.join(only_base[:6])}")
    if only_cur:
        print(f"                 仅偏移后有: {', '.join(only_cur[:6])}")
    return len(flipped) + len(only_base) + len(only_cur)


def scan(anchor_mode: str) -> tuple[int, int]:
    """在指定的锚点状态下扫一遍所有偏移，返回 (全量差异总数, 抽样差异总数)。"""
    if anchor_mode == "anchored":
        clock.set_anchor(seed_anchor)   # 修复后的正常状态
    else:
        clock.set_anchor(None)          # 阴性对照：等于修复前（墙上时钟）

    base_all = build_eval_cases(size=10000, seed=7)
    base_30 = build_eval_cases(size=30, seed=7)
    print(f"A 层 全量基准   n={len(base_all):<4} {dist(base_all)}")
    print(f"B 层 size=30    n={len(base_30):<4} {dist(base_30)}")
    print("-" * 82)

    total_a = total_b = 0
    for days, label in OFFSETS:
        clock.dt = _ShiftedDT(dt.timedelta(days=days))
        try:
            # ★ 只重建评测集，不重新 seed —— 数据是"同一份，只是墙上时钟走远了"
            cur_all = build_eval_cases(size=10000, seed=7)
            cur_30 = build_eval_cases(size=30, seed=7)
        finally:
            clock.dt = dt
        print(f"{label}：")
        print(f"  A 全量      n={len(cur_all):<4} {dist(cur_all)}")
        n_a = compare("全量比对", by_id(base_all), by_id(cur_all))
        n_b = compare("抽样比对", by_id(base_30), by_id(cur_30))
        total_a += n_a
        total_b += n_b
        print("-" * 82)
    return total_a, total_b


print("=" * 82)
init_db(drop=True)
stats = seed_all(n_customers=60, seed=7)
seed_anchor = clock.get_anchor()
print(f"seed: {stats}")
print(f"锚点（落库）：{seed_anchor}")
print("=" * 82)

print("【① 正向】锚点已在库 —— 墙上时钟前进不应影响判定")
print("=" * 82)
pos_a, pos_b = scan("anchored")

print("=" * 82)
print("【② 阴性对照】清掉锚点 —— 复现修复前的行为，偏移必须显形")
print("=" * 82)
neg_a, neg_b = scan("wallclock")

clock.set_anchor(seed_anchor)  # 恢复，别把库里的锚点状态改坏
print("=" * 82)
print(f"① 锚点在库：  全量差异 {pos_a} 条，抽样差异 {pos_b} 条")
print(f"② 无锚点对照：全量差异 {neg_a} 条，抽样差异 {neg_b} 条")
print("-" * 82)

ok = True
if pos_a or pos_b:
    print("✗ 正向失败：锚点在库时评测集仍然漂移，修复没生效")
    ok = False
else:
    print("✓ 正向通过：锚点在库时，墙上时钟走 30 天，评测集一条都不变")

if neg_a == 0 and neg_b == 0:
    print("✗ 阴性对照失败：清掉锚点也不漂 —— 说明本诊断根本没接线到判定路径，① 的通过不算数")
    ok = False
else:
    print(f"✓ 阴性对照通过：清掉锚点后漂移立刻显形（全量 {neg_a} / 抽样 {neg_b}）")
    print("  → 漂移是真实存在过的，修复确实把它按住了，而不是诊断看不见")

print("=" * 82)
print("✓✓ 缺陷 ⑨ 验收通过" if ok else "✗✗ 缺陷 ⑨ 验收未通过")
sys.exit(0 if ok else 1)
