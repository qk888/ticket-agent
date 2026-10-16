"""诊断：guesser 的 forbidden 到底落在哪条 case 上，两次造数之间差在哪。

已知：
  - 同一份 cases 连跑三次 → 0/0/0（稳）
  - 每次重新 seed 再跑   → 2/2/1（抖）
  - 但 case_id→condition 层面比对 0 翻转

所以要么 case 的**其他字段**（expected/initial/body）在变，要么抖动来自
case 之外的进程/数据库状态。这个脚本把两件事一起测：
  ① 两次造数的完整 case dict 深比对（逐字段，指出第一个不同的键）
  ② 两次造数上跑 guesser，逐条列出 forbidden 命中的 case_id
"""

import sys

sys.path.insert(0, "src")

from ticket_agent.db import build_eval_cases, init_db, seed_all  # noqa: E402
from ticket_agent.eval import run_suite  # noqa: E402
from ticket_agent.graph.nodes import set_model_kind  # noqa: E402
from ticket_agent import clock  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def build() -> tuple[list[dict], str]:
    init_db(drop=True)
    seed_all(n_customers=60, seed=7)
    return build_eval_cases(size=30, seed=7), str(clock.get_anchor())


def probe(cases: list[dict]) -> list[tuple[str, str, int]]:
    set_model_kind("guesser")
    res = run_suite(cases, arm="single", kind="guesser", k=1, progress=False)
    return [(r.case_id, r.condition, len(r.forbidden_hits or [])) for r in res]


def deep_diff(a, b, path=""):
    """返回前若干处不同，(路径, 值a, 值b)。"""
    out = []
    if type(a) is not type(b):
        return [(path, type(a).__name__, type(b).__name__)]
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append((f"{path}.{k}", "<缺失>" if k not in a else a[k],
                            "<缺失>" if k not in b else b[k]))
            else:
                out += deep_diff(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, (list, tuple)):
        if len(a) != len(b):
            out.append((f"{path}.len", len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            out += deep_diff(x, y, f"{path}[{i}]")
    elif a != b:
        out.append((path, a, b))
    return out


runs = []
for i in range(3):
    cases, anchor = build()
    rows = probe(cases)
    hits = [(cid, cond, n) for cid, cond, n in rows if n]
    runs.append((cases, anchor, rows, hits))
    print(f"第 {i+1} 次  anchor={anchor}  命中违规的 case={hits}")

print("=" * 84)
print("① case 深比对（第 1 次为基准）：")
for idx in (1, 2):
    d = deep_diff(runs[0][0], runs[idx][0])
    print(f"  第 1 vs 第 {idx+1} 次: {len(d)} 处不同")
    for path, x, y in d[:6]:
        print(f"     {path}\n        A={x!r}\n        B={y!r}")

print("=" * 84)
print("② 逐条比对 (case_id, condition, 违规数)：")
base = {cid: (cond, n) for cid, cond, n in runs[0][2]}
for idx in (1, 2):
    other = {cid: (cond, n) for cid, cond, n in runs[idx][2]}
    print(f"\n  ── 第 1 vs 第 {idx+1} 次 ──")
    for cid in sorted(set(base) | set(other)):
        a = base.get(cid, ("<无>", -1))
        b = other.get(cid, ("<无>", -1))
        if a != b:
            print(f"    ✗ {cid}: {a} → {b}")
    print(f"    （共 {sum(1 for c in set(base) | set(other) if base.get(c) != other.get(c))} 条不同）")

print("=" * 84)
print("③ 违规命中的 case 在各次运行中的 condition：")
allhit = sorted({cid for _, _, _, h in runs for cid, _, _ in h})
for cid in allhit:
    line = []
    for i, (cases, _, _, _) in enumerate(runs):
        c = next((x for x in cases if x["case_id"] == cid), None)
        line.append(f"{i+1}:{c['condition'] if c else '<无>'}")
    print(f"    {cid}: " + "  ".join(line))
print("=" * 84)
