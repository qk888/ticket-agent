"""为什么三次重建三批 case（`_vwhy9.py` 的后续）。

静态扫描结论：随机源只有三个（`_KIND_RNG` / `_PRICE_RNG` / 函数内 `rng`），
且 `seed_all` 开头就 `_reseed_aux_rngs(seed)`，**没有漏 seed 的流**。
所以"参数相同却重建出不同 case"的原因不在随机数种子。

剩下的可能只有两类：
  A. `_decide_golden` 用了 `rng`（build_eval_cases 传进去的那个）——
     它在 `seed_all` 之后才被调用，消耗的是 build 自己的 rng，与 seed_all 无关。
     但 build 的 rng 由 `seed` 播种，同样参数应该同样结果……
  B. **库内容不同**。`init_db(drop=True)` + `seed_all(1000, 11)` 应该幂等，
     但 `_vwhy3/4/8` 三次之间，`seed_all` 的第三位参数我从没写对过：
     `_reqkindcheck.py:38` 是 `seed_all(n_customers, 90, 7)` —— **第三位才是 seed**。
     而我在诊断脚本里写的是 `seed_all(n_customers=1000, seed=11)`，
     这是 `n_customers` + **`seed=11`**……看起来对，但 `seed_all` 的签名是
     `def seed_all(...)` 带默认值，第三位参数名未必叫 `seed`。

本脚本只做一件事：**打印 `seed_all` 与 `build_eval_cases` 的真实签名**，
再连跑两次同样的调用，逐位比较两批 case 的 case_id 列表。
如果两次相同 → 重建是确定性的，那"三次不同"是我参数写错了；
如果两次不同 → 真有非 seed 输入，继续查。

用法：python _vwhy10.py
"""
from __future__ import annotations

import inspect
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./eval_out/vwhy10.db")

from ticket_agent.db import build_eval_cases, init_db, seed_all  # noqa: E402
from ticket_agent.db import seed as seed_mod  # noqa: E402


def one_round() -> list[str]:
    init_db(drop=True)
    seed_all(n_customers=1000, seed=11)
    return [c["case_id"] for c in build_eval_cases(size=200, seed=7)]


def main() -> int:
    print("=== 签名（认准参数名与顺序） ===")
    print(f"  seed_all{inspect.signature(seed_all)}")
    print(f"  build_eval_cases{inspect.signature(build_eval_cases)}")
    print(f"  _reseed_aux_rngs{inspect.signature(seed_mod._reseed_aux_rngs)}")
    print()

    a = one_round()
    b = one_round()
    print(f"第一次 {len(a)} 条：前 8 {a[:8]}")
    print(f"第二次 {len(b)} 条：前 8 {b[:8]}")
    print(f"\n两次完全相同？ {a == b}")
    if a != b:
        sa, sb = set(a), set(b)
        print(f"  只在第一次：{len(sa - sb)} 条；只在第二次：{len(sb - sa)} 条")
        print(f"  交集：{len(sa & sb)} 条")
        # 顺序不同也算不同
        if sa == sb:
            print("  → 集合相同、顺序不同：`rng.shuffle(tickets)` 之后还有别的消费点")
        else:
            print("  → 集合本身就不同：库内容或条件判定有非 seed 输入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
