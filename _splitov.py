"""修 交付总览.md §2.2 第一组：两批 LLM 读数不可互替，且「按难度层」那段数字是 40 case 那批的。

硬规则：先 assert 命中数，再整段替换。
"""
import pathlib

p = pathlib.Path(__file__).parent / "交付总览.md"
t = p.read_text(encoding="utf-8")

old = """#### ★ 第一组：真实模型（Hy3）—— 这才是"模型能力"

```
命令：python -m ticket_agent.cli eval --size 40 --k 3 --arms single,multi \\
        --kind llm --seed 11 --customers 300
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

**按难度层**（`single` 臂）：L1 72 条 97.2%（工具 6.60）/ L2 18 条 88.9%（7.39）/ L3 30 条 **100.0%**（3.10，最省）。"""

new = """#### ★ 第一组：真实模型（Hy3）—— 这才是"模型能力"

> ⚠️ **两批读数不可互替**：A1 = 40 case / 11 条件纯退款线旧集（2026-10-10）；
> A2 = 200 case / 32 条件全诉求集（2026-10-14，`size=200 customers=1000 seed=7 k=3`）。

**A1 —— 40 case 旧集**

```
命令：python -m ticket_agent.cli eval --size 40 --k 3 --arms single,multi \\
        --kind llm --seed 11 --customers 300
报表：eval_out/eval-single-20261010-113840.md
      eval_out/eval-multi-20261010-142137.md
端点：endpoint_profile=hy3  endpoint_model=hy3  base_url=http://127.0.0.1:8787/v1
```

| 对照臂 | 运行数 | pass^3 | 单次准确率 | 平均模型调用 | 平均工具调用 | 平均写操作 | 违规动作 | 平均延迟 |
|---|---|---|---|---|---|---|---|---|
| `single` | 120 | **90.0%** | 96.7% | 4.30 | 5.84 | 0.88 | **0** | 27.6s |
| `multi` | 120 | **65.0%** | 85.0% | 7.5 | 8.77 | 0.68† | **0** | 81.5s† |

按难度层（A1 的 `single` 臂）：L1 72 条 97.2%（工具 6.60）/ L2 18 条 88.9%（7.39）/ L3 30 条 **100.0%**（3.10，最省）。

**A2 —— 200 case 全诉求集（每臂 600 次运行）**

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

按难度层（A2 的 `single` 臂）：L1 321 条 42.4%（工具 3.45）/ L2 195 条 32.8%（3.20）/ L3 84 条 89.3%（2.35，最省）。
**L3 比 L1 高近 47 个点** —— L3 是需澄清/需转人工的难 case，L1 是直接驳回/退款的简单 case：
agent 在简单 case 上反而更差，因为它会去猜一个动作，而正确动作是「按规则直接办」。"""

assert t.count(old) == 1, f"§2.2 第一组区块未唯一命中（{t.count(old)}）"
p.write_text(t.replace(old, new), encoding="utf-8")
print("交付总览.md：", len(t.replace(old, new)), "chars")
print("✓ §2.2 第一组已拆成 A1/A2")
