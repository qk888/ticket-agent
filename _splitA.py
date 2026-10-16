"""修 README 表 A 的标题与口径行 —— 两批 LLM 读数必须分开写，不能挤在同一张表同一句结论下。

三处硬伤：
1. 标题写「40 条 case」但表里已有 600 次运行的行；
2. 「两臂是同一批 case」后面跟的是 --seed 11 --size 40，与 200 case 那批混；
3. 一句话结论只讲 40 case 那批的 25 个点，没讲 200 case 那批的 3.5 个点。

改法：表 A 拆成「旧集 40 case」与「新集 200 case」两张子表，各自带自己的命令与结论；
再补一段「两批不可互替」。
"""
import pathlib

ROOT = pathlib.Path(__file__).parent

old = """#### 表 A：真实模型（`kind=llm`，Hy3，40 条 case × k=3 = 120 次运行）

```
命令：python -m ticket_agent.cli eval --size 40 --k 3 --arms single,multi --kind llm \\
        --seed 11 --customers 300
报表：eval_out/eval-single-20261010-113840.md
      eval_out/eval-multi-20261010-142137.md
端点：endpoint_profile=hy3  endpoint_model=hy3  base_url=http://127.0.0.1:8787/v1
```

| 对照臂 | 运行数 | pass^3 | 单次准确率 | 平均模型调用 | 平均工具调用 | 平均写操作 | 违规动作 | 平均延迟 |
|---|---|---|---|---|---|---|---|---|
| `single` | 120 | **90.0%** | 96.7% | 4.30 | 5.84 | 0.88 | **0** | 27.6s |
| `single` | 600 | **40.5%** | 45.8% | 2.77 | 3.21 | 0.53 | **0** | — |
| `multi` | 120 | **65.0%** | 85.0% | 7.5 | 8.77 | 0.68† | **0** | 81.5s† |
| `multi` | 600 | **27.5%** | 42.3% | 5.22 | 4.10 | 0.45 | **0** | 55.2s |

**两臂是同一批 case**（`--seed 11 --customers 300 --size 40`），逐条件直接对照见 [§7.8](#78-两臂对照-multi-是净负面的)。"""

new = """#### 表 A：真实模型（`kind=llm`，Hy3）

> ⚠️ **下面两批读数不可互替**：A1 是 40 case / 11 条件的**纯退款线旧集**（2026-10-10）；
> A2 是 200 case / 32 条件的**全诉求集**（2026-10-14，`size=200 customers=1000 seed=7 k=3`）。
> 样本从 40 扩到 200 后 **single 单次准确率从 96.7% 掉到 45.8%** —— 不是模型变差，
> 是 40 条里多数是 happy path，猜错的部分在 200 条里才露出来（见 [§7.8](#78-两臂对照-multi-是净负面的)）。

**A1 —— 40 case 旧集（`--seed 11 --customers 300 --size 40`，每臂 120 次运行）**

```
命令：python -m ticket_agent.cli eval --size 40 --k 3 --arms single,multi --kind llm \\
        --seed 11 --customers 300
报表：eval_out/eval-single-20261010-113840.md
      eval_out/eval-multi-20261010-142137.md
端点：endpoint_profile=hy3  endpoint_model=hy3  base_url=http://127.0.0.1:8787/v1
```

| 对照臂 | 运行数 | pass^3 | 单次准确率 | 平均模型调用 | 平均工具调用 | 平均写操作 | 违规动作 | 平均延迟 |
|---|---|---|---|---|---|---|---|---|
| `single` | 120 | **90.0%** | 96.7% | 4.30 | 5.84 | 0.88 | **0** | 27.6s |
| `multi` | 120 | **65.0%** | 85.0% | 7.5 | 8.77 | 0.68† | **0** | 81.5s† |

一句话结论：在这批纯退款线小样本上 **`multi` 是净负面的** —— 模型调用约 1.7×，
而 pass^3 从 90.0% 掉到 65.0%、单次准确率从 96.7% 掉到 85.0%（成本两臂都是 ¥0，Hy3 限时免费）。
而它多花的钱几乎全花在**重复的只读复核**上（写操作两臂分别是 0.88 与 0.68，multi 甚至更低）。

**A2 —— 200 case 全诉求集（`--seed 7 --customers 1000 --size 200 --k 3`，每臂 600 次运行）**

```
命令：python _prun.py --shards 4 --arms single,multi --kind llm --size 200 \\
        --customers 1000 --seed 7 --k 3
报表：eval_out/eval-single-llm-200c.md
      eval_out/eval-multi-llm-200c.md
```

| 对照臂 | 运行数 | pass^3 | 单次准确率 | 平均模型调用 | 平均工具调用 | 平均写操作 | 违规动作 | 平均延迟 |
|---|---|---|---|---|---|---|---|---|
| `single` | 600 | **40.5%** | 45.8% | 2.77 | 3.21 | 0.53 | **0** | — |
| `multi` | 600 | **27.5%** | 42.3% | 5.22 | 4.10 | 0.45 | **0** | 55.2s |

一句话结论：扩样本后 **`multi` 的净负面从 25 个点缩到 3.5 个点**（单次 45.8% vs 42.3%）。
按条件拆开后能看到 A1 上看不见的结构：`multi` 在「该驳回」类条件净胜
（`R1:reject` +13、`R2:reject` +9、`R13:reject` +5），
在「该转人工 / 该补发 / 无订单」类净负（`no_order` −11、`R4:escalate` −9、
`R9:reship` −9、`R8:reject` −9）。
**两臂是同一批 case**（`--seed 7 --customers 1000 --size 200`），逐条件直接对照见 [§7.8](#78-两臂对照-multi-是净负面的)。"""

p = ROOT / "README.md"
t = p.read_text(encoding="utf-8")
assert t.count(old) == 1, f"表 A 区块未唯一命中（{t.count(old)} 次）"
p.write_text(t.replace(old, new), encoding="utf-8")
print("README.md：", len(t.replace(old, new)), "chars")
print("✓ 表 A 已拆成 A1/A2")
