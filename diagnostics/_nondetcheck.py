"""诊断：缺陷 ⑨ 修复后，`_faultcheck.py` 里 guesser 的 forbidden 为何仍是 1/2/2？

时钟锚点修完后评测集已证明逐条一致（`_driftcheck.py` 正向 0 差异），
但连跑三次 `_faultcheck.py` 仍得到 `guesser forbidden = 2 / 1 / 2`。

于是要回答两个问题：
  ① 是不是**同一进程内**就不稳定？（若稳，则问题在进程级因素）
  ② 是不是 **PYTHONHASHSEED**（Python 每进程随机化字符串 hash）？
     本项目里 `decide()` 拿的是 `set[str]`（类目集合），
     `cats = {i.category for i in items} | {order.category}` —— 如果判定或
     reasons 文案依赖 set 迭代顺序，行为就会随进程变。
"""

import os
import random
import subprocess
import sys

sys.path.insert(0, "src")

from ticket_agent.db import build_eval_cases, init_db, seed_all  # noqa: E402
from ticket_agent.eval import run_suite  # noqa: E402
from ticket_agent.graph.nodes import set_model_kind  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def build() -> list[dict]:
    init_db(drop=True)
    seed_all(n_customers=60, seed=7)
    return build_eval_cases(size=30, seed=7)


def guesser_forbidden(cases: list[dict]) -> tuple[int, list[str], str]:
    set_model_kind("guesser")
    res = run_suite(cases, arm="single", kind="guesser", k=1, progress=False)
    hits: list[str] = []
    for r in res:
        hits += list(r.forbidden_hits or [])
    fp = "".join(sorted(c["case_id"] for c in cases))
    return len(hits), sorted(hits), f"{len(fp)}:{hash(fp) & 0xFFFF:04x}"


print("=" * 80)
print("① 同一进程内连跑三次 guesser（数据只 seed 一次）")
cases = build()
print(f"   case 指纹: {len(cases)} 条 levels="
      f"{ {lv: sum(1 for c in cases if c['level'] == lv) for lv in ('L1','L2','L3')} }")
for i in range(3):
    n, hits, _ = guesser_forbidden(cases)
    print(f"   第 {i+1} 次: forbidden={n}  {hits}")

print()
print("② 同一进程内，重新 seed 后再跑（模拟脚本每次重启）")
for i in range(3):
    cases = build()
    n, hits, fp = guesser_forbidden(cases)
    print(f"   第 {i+1} 次: forbidden={n}  {hits}")

print()
print("③ 子进程：相同数据、不同 PYTHONHASHSEED")
snippet = (
    "import sys; sys.path.insert(0,'src');"
    "from ticket_agent.graph.nodes import set_model_kind;"
    "from ticket_agent.db import build_eval_cases, seed_all;"
    "from ticket_agent.eval import run_suite;"
    "set_model_kind('guesser');"
    "cases=build_eval_cases(size=30, seed=7);"
    "res=run_suite(cases, arm='single', kind='guesser', k=1, progress=False);"
    "h=[x for r in res for x in (r.forbidden_hits or [])];"
    "print('PYTHONHASHSEED='+__import__('os').environ.get('PYTHONHASHSEED','?'),"
    "      'forbidden=', len(h), sorted(h))"
)
for seed_val in ("0", "0", "1", "1", "random", "random"):
    env = {**os.environ, "PYTHONHASHSEED": seed_val, "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", snippet], capture_output=True, text=True,
                         env=env, encoding="utf-8", errors="replace")
    print("   ", (out.stdout or out.stderr).strip().splitlines()[-1] if (out.stdout or out.stderr) else "(空)")

print()
print("④ 类目集合的迭代顺序会不会影响判定？")
from ticket_agent.rules import decide  # noqa: E402

cats = {"内衣", "定制", "数字商品", "服饰"}
for i in range(3):
    d = decide(order_status="signed", amount=100.0, categories=set(cats),
               signed_at=None)
    print(f"   set 顺序 {i}: action={d.action} reasons={d.reasons}")
print("   sorted(cats) =", sorted(cats), " 迭代顺序 =", list(cats))
print("=" * 80)
