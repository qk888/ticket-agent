# ticket-agent · 售后工单处置 Agent

> 一个能经得起面试官追问 30 分钟的 Agent 项目：**LangGraph 编排 + 角色权限物理裁剪 + HITL 人工审批 + τ-bench 式终态评测**。
> 评测不看 agent 说了什么，只看它做完之后**数据库状态对不对**；一致性用 `pass^k` 而不是单次成功率衡量。

**关键取舍先说在前面**：本仓库的 golden 由规则引擎推导，所以规则基线在终态准确率上是**上界参考**，
不是被 LLM 超越的对手。这一点在报表里显式标注为 `rule_ceiling`。要衡量模型能力得接真 LLM
（`kind=llm`，需要 `OPENAI_API_KEY`）—— 详见 [§7 评测方法论](#7-评测方法论)。

---

## 0. 这个项目回答的三个问题

| 面试官的问题 | 本项目的答案 | 代码位置 |
|---|---|---|
| 能不能把 LLM 接到真实系统上？ | 工具幂等键 + 唯一约束兜底、写工具不自动重试、租户隔离、HITL 审批、token 预算熔断 | `tools/registry.py`、`graph/middleware.py` |
| 你怎么知道它做对了？ | 不看输出，比终态。9 个只读工具 + 4 个写工具全部落审计日志，评测比对 DB 快照 | `eval/harness.py`、`db/snapshot.py` |
| 它稳不稳？贵不贵？ | `pass^k` 一致性 + 单任务成本 + 分层报表；两臂消融给出"多花的钱买到了什么" | `eval/report.py` |

### 实测指标

> 表分两张，**别混着读**：
> - **表 A（真实模型）** 是接上真 LLM（本机 WorkBuddy 的 Hy3，见 [§2.5](#25-接本机-workbuddy-的-hy3档案-hy3)）跑出来的，
>   样本小（40 条 × k=3），但它是**模型能力**的读数 —— 这 40 条 case 模型从没见过，
>   golden 也不是它自己算的。
> - **表 B（规则基线）** 是 `kind=scripted` 的脚本化基线，样本大（561 条 × k=3 = 1,683 次），
>   但它直调 `rules.decide()`，而 golden 由同一套规则推导 —— 它的 100% 是
>   **这套评分标准下的上界参考**，证明的是**评测链路与编排链路是通的**，不是模型成绩。
>   ★ **口径变更**：下面表 B 的读数取自 2026-10-10，是**改口径之前**的基线。
>     `agents/scripted.py` 的 `_request_amount_of` 原先从**工单正文自由文本**
>     重抽赔付/价保金额（`_amount_in_text(body)`），现已改读**结构化字段
>     `tickets.request_amount`**，并把该键补进 `get_ticket` 的返回体。
>     实测依据 `_r10diff.py`：千级库上 4/4 条 `R10:compensate` 由
>     「终态卡 `open`、`after_sales=[]`、写调用 0」转为 `passed=True`。
>     ⇒ 表 B 那 1,683 次运行**尚未按新口径重跑**，属旧口径读数。

#### 表 A：真实模型（`kind=llm`，Hy3）

> ⚠️ **下面两批读数不可互替**：A1 是 40 case / 11 条件的**纯退款线旧集**（2026-10-10）；
> A2 是 200 case / 32 条件的**全诉求集**（2026-10-14，`size=200 customers=1000 seed=7 k=3`）。
> 样本从 40 扩到 200 后 **single 单次准确率从 96.7% 掉到 45.8%** —— 不是模型变差，
> 是 40 条里多数是 happy path，猜错的部分在 200 条里才露出来（见 [§7.8](#78-两臂对照-multi-是净负面的)）。

**A1 —— 40 case 旧集（`--seed 11 --customers 300 --size 40`，每臂 120 次运行）**

```
命令：python -m ticket_agent.cli eval --size 40 --k 3 --arms single,multi --kind llm \
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
命令：python _prun.py --shards 4 --arms single,multi --kind llm --size 200 \
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
**两臂是同一批 case**（`--seed 7 --customers 1000 --size 200`），逐条件直接对照见 [§7.8](#78-两臂对照-multi-是净负面的)。
一句话结论：**`multi` 是净负面的** —— 模型调用约 1.7×，而 pass^3 从 90.0% 掉到 65.0%、单次准确率从 96.7% 掉到 85.0%（成本两臂都是 ¥0，Hy3 限时免费）。
而它多花的钱几乎全花在**重复的只读复核**上（写操作两臂分别是 0.88 与 0.68，multi 甚至更低）。

> `multi` 行的 pass^3 / 单次准确率 / 平均模型调用是 **⑲ 修复后重跑**的读数
> （`eval_out/rerun-multi-full.log`：终态 0.850、pass^3 0.650、均 7.5 次调用）；
> 平均工具调用 8.77 由该日志的分层值加权得出 ——（9.94×72 + 9.89×18 + 5.27×30）÷ 120。
> † 写操作数与平均延迟**日志没有重新报告**，这两列沿用修复前读数，引用时请注意。
> ★ 更重要的口径：这轮跑在 `request_kind` 引入**之前**的 40 条纯退款线评测集上，
> 与现在 241 条 / 24 条件的评测集**不可比**；它证明的是"⑲ 修复有效"，
> 不是当前代码的能力上限。

**按难度层**：L1 72 条 97.2%（工具 6.60）/ L2 18 条 88.9%（7.39）/ L3 30 条 **100.0%**（3.10）。

**按条件**（模型真正会做哪条规则）：`R1:reject` 33 条 97.0% / `ambiguous` 24 条 100% /
`R2:reject` 18 条 100% / `R4:escalate` 9 条 100% / `R6:escalate` 9 条 100% /
`R1:refund` 6 条 83.3% / `R3:block_logistics` 6 条 83.3% / `no_order` 6 条 100% /
`CANCELLED:reject` 3 条 100% / `R5:escalate` 3 条 100% / `UNPAID:reject` 3 条 66.7%。

**按话术风格**：`explicit` 90 条 95.6%（工具 6.76）/ `vague` 30 条 **100.0%**（工具 3.10）——
和规则基线同一个形状：客户要的动作系统办不到时，正解是转人工，反而**更省钱**。

> ★ **pass^3 90.0% vs 单次 96.7% 的 6.7 个点差额，是这张表里最有信息量的数字。**
> 它说明失败**不是"某类题不会做"，而是"同一道题三次里有一次没收好尾"**。
> 逐条取证（`eval_out/eval-single-20261010-113840.json` 的 `cases` 数组）：
> 4 条失败**全部是"判对了但没执行完"** ——
> `acme-t66`（R3 拦截：一次 write_calls=1、另两次 2，少的那次停在 `ticket.status=open`）、
> `acme-t88`（R1 该退款，一次改判成 `escalate`，`refund_ledger` 为空）、
> `acme-t24`（R1 该驳回，一次转人工）、
> `acme-t81`（未付款该驳回，一次 `write_calls=0`，单子留在 `open`）。
> **四条全是"结论对了、动作少做一步"，没有一条是把钱退错、也没有一条触发 `forbidden`。**
>
> 这正是 **τ-bench 式终态评测**要抓的东西：如果只看 agent 的回复文本，
> 这四次里它说的话全都对（"已为您驳回""不符合退款条件"），是**数据库没变**才暴露了它没办。
> 同源的真实缺陷见 [§4.2 缺陷 ⑫](#42-三十四个真实缺陷)（护栏守住了钱，没守住谎）。
>
> ★ **这条读数是小样本，别当能力上限**：40 条 case 里 4 条失败经验证全落在
> `explicit` 风格、L1/L2 层，且都是"收尾"环节。要更稳的结论需要 `--size 605`（成本见 §2.2）。

> **两批 LLM 读数不可互替**：表 A 前两行是 40 case / 11 条件的纯退款线旧集（2026-10-10 跑，只有 `refund` 诉求）；后两行是 200 case / 32 条件的全诉求集（2026-10-14 跑，`size=200 customers=1000 seed=7 k=3`，每臂 600 次运行）。
> 样本从 40 到 200 后 **single 从 96.7% 掉到 45.8%** —— 不是模型变差，是 40 条里多数是 happy path，猜错的部分在 200 条里才露出来。**multi 的净负面幅度同时从 25 个点缩到 3.5 个点**，且按条件拆开后能看到结构：multi 在「该驳回」类条件净胜（R1:reject +13、R2:reject +9、R13:reject +5），在「该转人工/该补发/无订单」类净负（no_order −11、R4:escalate −9、R9:reship −9、R8:reject −9）。小样本上看不到这个结构。

#### 表 B：规则基线（`kind=scripted`，**561 条 case × k=3 = 1,683 次运行**）

数据：1,000 客户 / 2,375 订单 / 561 工单，正文取自 **11,079 条真实语料**（见 [§4.4](#44-语料层从-9-条手写模板到-11079-条真实话术)）。
命令：`python -m ticket_agent.cli report --size 605 --k 3 --seed 7 --customers 1000`
报表：`eval_out/final-scripted-multi-k3-20261010-090158.md`
      （头部实测：`arm=multi；kind=scripted；size=561；seed=7；
        corpus_sha256=1bb070b09c354582；corpus_unique=11079`）

> ⚠️ **两个维度都变了**：① 这是**旧基线口径**（`_request_amount_of`
>   改数据源之后未重跑）；② 它覆盖的是 `request_kind` 引入**之前**的
>   **纯退款线 11 条件**评测集（条件表前五条全是 R1–R3，R8–R14 一条没有），
>   与下文 241 条 / 24 条件的新集**不可比**。

> ★ 上面这行数据规模就是 `report` 命令自己造出来的：它跑之前会 `init_db(drop=True)` + `seed_all(seed=seed)`，
> **用的是 `--seed 7`，不是任何别处手工造数用的种子**。核对这张表时别用另一个种子重造库
> 再去比工单数 —— 两个种子的数字不同（seed=42 下是 2,245 订单 / 583 工单），
> 拿它去对会得出"报表数据对不上"的错误结论。（这个坑真实发生过：一份交付文档里
> 写的规模是另一个种子的，复现不出来。）

| 对照臂 | 运行数 | pass^3 | 准确率 | 平均成本 | 平均模型调用 | 平均工具调用 | 平均写操作 | 违规动作 |
|---|---|---|---|---|---|---|---|---|
| `single` | 1683 | 100.0% | 100.0% | ¥0.003600 | 5.1 | 4.1 | 1.02 | 0 |
| `multi` | 1683 | 100.0% | 100.0% | ¥0.009592 | 10.4 | 7.4 | 1.02 | 0 |

> 模型调用与工具调用**分列**：一次模型调用可以并发发出多个工具调用，两者实测既不等值也不成比例。
> 写操作两臂相同（各 1.02）—— 客户诉求并没有变多，多出来的工具调用全花在**只读的核查与复核**上。

**按难度层**（分层报，避免总分掩盖"简单题全对难题全错"）：

| 层 | 运行数 | pass^3 | 平均工具调用 | 说明 |
|---|---|---|---|---|
| L1 | 963 | 100.0% | 8.09 | 规则直判（签收态、类目、次数） |
| L2 | 375 | 100.0% | 8.25 | 要算时效（在途拦截、超期判定） |
| L3 | 345 | 100.0% | **4.54** | 需澄清/转人工 —— **更省钱**，因为不关单 |

**按条件**（**11 种**条件全覆盖 —— 这是 `request_kind` 引入**之前**的纯退款线评测集，与下文 241 条 / 24 条件的新集**不可比**）、**按话术风格**：

```
R1:reject 489 (29.1%)   ambiguous 252 (15.0%)   R2:reject 162 (9.6%)
R1:refund 153 (9.1%)    R3:block_logistics 126 (7.5%)  UNPAID:reject 114 (6.8%)
R4:escalate 96 (5.7%)   R5:escalate 96 (5.7%)   no_order 93 (5.5%)
R6:escalate 69 (4.1%)   CANCELLED:reject 33 (2.0%)  —— 全部 100%

explicit 1278 (75.9%) 100.0% 工具 8.14
vague     345 (20.5%) 100.0% 工具 4.54
defect     60  (3.6%) 100.0% 工具 8.15
```

> `vague`（客户要的动作系统办不到，如催件/查件）的工具调用数**只有其他两类的一半** ——
> 因为正解是转人工或反问，不需要走核查链。这个数字本身就是"系统知道自己办不到"的证据。

> **这个表里的数字不是"模型成绩"。** 见文首的 rule_ceiling 说明。
> 它证明的是评测链路与编排链路是通的，真正有价值的读法是成本列：
> **multi 拿到同样的终态准确率，代价是 2.7× 成本、2.0× 模型调用、1.8× 工具调用。**
>
> 另外请注意：这个 100% 建立在**评测集真的考了该考的东西**之上。
> 这一点是查出来的，不是假设的 —— 见 [§7.5.4 覆盖度审计](#754-覆盖度审计0-条-case-的规则和从不犯错长得一模一样)。
> 语料换过两轮之后，这个审计也重跑过：**风格失真放大倍数 18.7x → 2.1x → 2.2x**
> （见 [§4.4](#44-语料层从-9-条手写模板到-11079-条真实话术)）。★ 第二次换语料后这个数字
> **几乎没动**，这本身是个证据：那次修复是**结构性的**（改造数顺序），不是靠对着某批语料调参数调出来的 ——
> 若它是调出来的，换一批语料就该打回原形。


---

## 1. 领域为什么选"售后工单"

不是因为它常见，而是它天然满足评测的三个硬条件：

| 条件 | 本项目的体现 |
|---|---|
| **有 ground truth** | 工单该停在哪个状态、该不该产生退款记录，由政策唯一决定，不需要人工判分 |
| **有动作空间** | 13 个工具，其中 4 个写工具真实改动订单/退款/工单表 |
| **有真实规则约束** | 7 天无理由、类目例外、风控阈值、退款次数上限、生鲜硬拒 |

### 1.1 数据模型

```
customers ──┬── orders ──── order_items
            │      │
            │      └────── refunds          （idempotency_key 唯一约束）
            └── tickets ──── audit_log      （agent 每个动作都落这里）
policy_articles                              （R1–R7 被切块入表，供 RAG 检索）
```

### 1.2 业务规则（R1–R7，完整文本见 `prompt/policies.md`）

| # | 规则 | 难点在哪 |
|---|---|---|
| R1 | 签收后 7 个自然日内可无理由退货 | 起算点是**签收时间**，且必须调 `days_since_signed` 而不能心算 |
| R2 | 内衣 / 定制 / 数字商品不支持无理由 | 要查类目，且**规则在文档里不在代码里** |
| R3 | 已发货未签收 → 只能拦截物流不能退款 | 动作选择依赖订单状态机 |
| R4 | 金额 > 500 或风险分 > 0.7 或黑名单 → 转人工 | HITL 触发；转人工后工单要留在可被接手的终态 |
| R5 | 同一订单最多退一次 | 幂等 + 历史查询 |
| R6 | 生鲜不支持任何形式退款 | **优先级高于 R4**（生鲜不问金额直接转人工） |
| R7 | 工单关闭规则 | 四种结果对应四种状态；**意图不清时必须保持 open 反问，不得擅自关单** |

### 1.3 任务分层（评测按层报，避免总分掩盖"简单题全对难题全错"）

| 层级 | 定义 | 例子 |
|---|---|---|
| **L1** | 单动作，订单明确 | "我的退款到哪了" → 查订单 → 应答 |
| **L2** | 多步依赖 / 在途 | "我要退货" → 查时效 → 查类目 → 查风控 → 建退款 → 关单 |
| **L3** | 意图不清，需澄清 | "我要退款。"（客户名下有 4 笔订单）→ **必须先反问是哪一笔** |

L3 的构造方式：seed 时让 70% 的 L3 工单**不关联订单**。

---

## 2. 快速开始

不需要 API Key 就能跑通全流程（内置脚本化模型），装完依赖即可验证。

> ★ **想要一份"从零照着敲"的完整说明**（含每一步的期望输出、怎么判断成功、
> 七个端点档案怎么选、常见故障怎么查）——看 **[手动启动.md](手动启动.md)**。
> 下面这一节是压缩版。

```bash
python -m venv .venv
.venv\Scripts\activate                 # Windows
# source .venv/bin/activate            # macOS / Linux

pip install -e ".[dev]"
```

### 2.1 跑一遍完整链路

```bash
# 1. 生成合成业务数据（60 客户 / 149 订单 / 35 工单，全部落在本地 SQLite）
ticket-agent seed --customers 60 --seed 7 --drop

# 2. 看当前配置、模型种类与角色工具矩阵
ticket-agent info

# 3. 列出工单（注意 order_basis 列：linked / sole_order / ambiguous / no_order）
ticket-agent tickets --tenant acme --limit 10

# 4. 跑一张工单，逐节点播报工具调用
ticket-agent run 11 --tenant acme --stream

# 5. 人工待办队列 → 裁决（★ 用 --ticket：跨进程可用，见缺陷 ⑦）
ticket-agent pending --tenant acme
ticket-agent approve --ticket 17 --tenant acme --decision approve_refund
#   政策禁止的（R5 已退过 / R6 生鲜）会以退出码 1 拒绝；确需破例才加 --override（留审计）
ticket-agent approve --ticket 17 --tenant acme --decision approve_refund --override
#   也支持按 run 裁决，但只限同一进程（先 run 再 approve 的脚本 / HTTP 服务）：
ticket-agent approve <run_id> --tenant acme --decision approve_refund

# 6. 全量评测 + 出正式报告（两臂、pass^3、分层、成本轴）
ticket-agent report --size 40 --k 3 --seed 7 --customers 60
```

实测输出（`seed`）：
```
{'customers': 60, 'orders': 149, 'items': 295, 'tickets': 35, 'policies': 9}
```

`run --stream` 的实测轨迹（一条 R4 转人工的工单）：
```
▶ run 3f2a1b8c9d0e ticket=11 arm=multi
  → triage          ✓ get_ticket / get_customer / list_orders_of_customer
  → supervisor
  → resolver        ✓ get_order / days_since_signed / check_refund_eligibility
                    ✓ escalate_to_human({"reason": "R4：金额 779.65 元超过 500.0 元，必须转人工审批"})
  → supervisor
  → verifier        ✓ get_ticket / get_order / list_refunds_of_order / get_snapshot
                    结论：通过 问题：无
状态：awaiting_human ｜ 需要人工：True ｜ 用量：8978→402 tok / ¥0.0098
```

### 2.2 接真 LLM

复制 `.env.example` 为 `.env` 并填写：

```
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com/v1     # 或任意 OpenAI 兼容端点
MODEL_NAME=deepseek-chat
CHEAP_MODEL_NAME=deepseek-chat                  # 预算降级后切到它
```

然后 `ticket-agent model-kind llm` 切过去，或起服务后 `POST /admin/model-kind`。
也可以指向本地 Ollama（`http://localhost:11434/v1`）把成本压到 0。

★ **换端点之前先跑自检**（理由见 §2.4）：

```bash
ticket-agent endpoint --check          # 3 次请求，约 5 秒
ticket-agent smoke                     # 跑一张真工单，看轨迹里有没有工具调用
```

### 2.3 起服务 + 网页控制台

```bash
ticket-agent serve --host 127.0.0.1 --port 8000            # 起服务（顺带建表）
ticket-agent serve --seed 60                               # 库里没数据时，连数据一起灌
```

服务起来后浏览器打开 <http://127.0.0.1:8000/> —— 那是一个**网页控制台**：

- 左栏：待人工裁决 / 工单列表 / 运行记录，点一张工单就选它
- 中间：「处置该工单」→ 走 SSE，**实时**打出每个节点、每次工具调用和耗时
- 终态卡：工单状态、实际写了哪些数据、终态快照、独立复核结论、**过程探针违规**
- 还没有人工裁决的工单，会出现审批面板 —— 金额、备注、`批准退款|驳回诉求|维持挂起` + override 复选框

★ 想看到"护栏真的挡住了人"，随便挑一张已退过款的工单跑一遍，然后**不勾 override** 点「批准退款」：
服务会返回 `409` 并给出政策理由。人工审批也绕不过写路径上的硬校验。

控制台是 `web/index.html`，**单文件、零依赖、无构建步骤**，由 FastAPI 同源挂出去
（`GET /`，见 `api/app.py`）。不引 Vue/React 的理由很实际：本项目的卖点是"clone 下来
就能验"，而 `npm install` 会把这一条变成五分钟的等待。它需要的数据接口一页就能列完，
手写 DOM 比配一套构建工具链更短。

> **缺陷 ⑳：服务起得来，但页面上每个列表都是 500。**（第 20 个真实缺陷，详见 §4.2）
> `serve` 原先只跑 `uvicorn.run(...)`，**从不建表**。新克隆的仓库照文档起服务：
> `GET /health` 正常、`GET /` 也返回 200，但 `GET /tickets` 抛
> `sqlite3.OperationalError: no such table: tickets`。
> 修法是 `serve` 起服务前先 `init_db(drop=False)`，库里没工单就打印人话提示
> （告诉你该跑 `seed`，或者直接 `serve --seed 60`）。
> 这和 ⑬⑭⑮⑯⑰ 是同一个形状：**一段流程被拆成两半，一半做了另一半没做，
> 中间没有任何东西会变红。** 起服务的人假设库已建好，建库的人假设你会先跑 seed。

### 2.4 多端点：真 API Key / 本地模型 / 第三方中转（§4.2 缺陷 ⑬ + ⑭）

一个开关不够用：同一份评测要能在"云端真 API"和"本地 Ollama"之间来回跑，
而这两者的地址、模型名、**单价**都不一样。所以端点做成**档案 + 覆盖**：

```bash
ticket-agent endpoint --list                                  # 看所有档案的默认值与单价
ticket-agent endpoint                                         # 看当前实际生效的是哪个
ticket-agent endpoint --profile hunyuan --check                # 自检某个档案
ticket-agent endpoint --profile relay --base-url https://x/v1  # 临时指一个中转站
ticket-agent endpoint --profile ollama --model qwen2.5:14b     # 临时换本地模型
```

`.env` 里对应的是 `LLM_PROFILE` + 五个覆盖键（`LLM_BASE_URL` / `LLM_MODEL` /
`LLM_CHEAP_MODEL` / `LLM_API_KEY` / `LLM_PRICE_*_PER_1K_CNY`）。

七个内置档案：

| 档案 | 地址 | 默认模型 | 单价（元/千 token） | 适用 |
|---|---|---|---|---|
| `deepseek` | api.deepseek.com | deepseek-chat | 0.001 / 0.002 | 默认，跑评测用这个 |
| `hunyuan` | api.hunyuan.cloud.tencent.com | hunyuan-a13b | 0.0005 / 0.002 | 腾讯混元官方，OpenAI 兼容且支持 Function Calling |
| `hy3` | **127.0.0.1:8787**（本机 sidecar） | hy3 | 0 / 0 | WorkBuddy 桌面端登录态桥接，见 §2.5 |
| `ollama` | localhost:11434 | qwen2.5:7b-instruct | 0 / 0 | 本地，见下面"值不值得跑" |
| `openai` | api.openai.com | gpt-4o-mini | **未声明** | 官方端点 |
| `relay` | **留空** | **留空** | **未声明** | 第三方中转/聚合 |
| `custom` | 全靠 `LLM_*` | 全靠 `LLM_*` | **未声明** | 任意兼容端点 |

三条设计决定值得单独说：

**① 自检不是可选项，`eval` / `report` 会拦你。**

本项目的 agent 只有一种推进方式：模型发出 `tool_calls`（`graph/nodes.py` 的
ReAct 循环里 `if not calls: break` —— "没工具调用"和"任务完成"是同一个分支）。
而第三方中转层丢掉 `tool_calls` 字段是**常见故障**，症状是：

> 接口一切正常。HTTP 200、返回了内容、token 也计费了。
> 只是 agent 一步都走不动，而它**不会报错**。

评测照常跑完、报表照常生成，只是准确率很低 —— 而那时钱已经花了。
一轮千级评测要几千次模型调用，自检只要 3 次请求，所以
`cli.py:_ensure_endpoint_ready()` 会在 `eval` / `report` 开头检查端点是否
配好且自检通过，否则直接退出（逃生开关 `LLM_ALLOW_UNVERIFIED=1`）。

自检查四项：连通与鉴权、**tool_calls 是否被保留**、多轮工具循环（第一轮保留
第二轮退化的中转层很多）、usage 是否上报（不上报时成本走字符数估算兜底，
**会失真但不报错**）。前两项是 `ready` 的必需项，后两项只产出警告 ——
"能跑但成本不准"不该被误判成"不能跑"。

**② 单价必须跟端点一起走，否则成本列在说谎。**

`middleware.py` 用 `settings.endpoint` 的单价换算金额。换模型不改单价，
报表不会报错、数字量级也照常合理 —— 只是"这次比上次便宜"这类结论
建立在两套单价上。这与缺陷 ⑨（基准漂移）是**同一类**问题：
漂移源从时钟换成了单价表。

所以单价**未声明**的档案（relay / openai / custom）走的是"不知道"而不是 0：
`Endpoint.price_known=False` 会一路传到 `Budget.price_unknown` →
`CaseResult.price_unknown` → `cost_summary["price_unknown_rate"]` →
报表里的告警块：

> ⚠️ **`multi` 的成本列不可用**：100% 的运行落在**没有声明单价**的端点上
> （profile=relay）。上表的金额是按 0 累加出来的，**不是免费**，是**不知道**。

★ 这条链路上原来有个断点：`price_unknown` 在 `middleware.as_budget()` 就丢了，
于是"单价未知"出了 tracker 就消失，成本显示为 0 —— 而 **0 在报表里长得
跟"免费"一模一样**。现在四个环节都传它，`merge_budget()` 用"或"合并
（一张工单跑多个节点，任一个发现单价未知，整张工单的成本就是"不知道"）。

★★ **但上面这套告警有一个前提：单价确实被判成"未声明"。**
原先老路径（不选档案、只填 `OPENAI_BASE_URL` / `MODEL_NAME`）**永远走不到
`price_known=False`**，因为它的单价来自 `Settings.price_*` 字段，
而那两个字段的默认值是 DeepSeek 的 0.001/0.002 —— 于是把地址换成本地 ollama
（边际成本 0）或混元（输入 0.0005）时，报表依然按 DeepSeek 的价算账，
**告警永远不会响**。这就是缺陷 ⑭（完整取证与修法见 §4.2）。

现在单价的来源规则统一成一条：**单价必须与端点身份来自同一个来源**。

| 情形 | 单价从哪来 |
|---|---|
| 选了档案，档案声明了价 | 跟着档案走（覆盖地址是用户的自由，价仍跟档案） |
| 选了档案，档案没声明价（relay / custom） | 按最终地址反查内置档案认领；认不出 → 未声明 |
| 老路径，地址等于某个内置档案 | 按地址认领那个档案的价 |
| 老路径，地址认不出 | **未声明** → 报表打告警（不是套默认价） |
| 任何路径，显式设了 `LLM_PRICE_*` | 用显式值（两条路径都认，最高优先级） |
| 老路径，显式填了 `PRICE_*` 字段 | 用显式值 —— ★ 只认**显式填的**，判据是 `model_fields_set` |

★ 反查认不出时**返回空字符串**，不取第一个碰上的档案：

> 撞上多个档案就判不确定 —— **取第一个等于用字典顺序决定单价。**

★ 为什么是"按地址反查"而不是"老路径一律判未声明"？
因为要守住**两个入口在同一个端点上必须给出同一个价** ——
地址就是 DeepSeek 官方时，走老路径和走 `deepseek` 档案本就该同价。
⑬ 就是这条不变式被破坏的形状，修 ⑭ 时不能再用另一种方式破坏它一次。

**③ 两个解析入口必须共用一份实现（缺陷 ⑬ 本身）。**

`Settings.endpoint` 是"我实跑的是哪个端点"，`resolve_endpoint()` 是
"我要自检的那个端点长什么样"。它们**曾经是两份平行实现**：

| | `Settings.endpoint`（真跑） | `resolve_endpoint()`（自检） |
|---|---|---|
| 取值链 | `os.environ` **→** 字段 → 档案 | 覆盖 **→** 字段 → 档案（**不读环境**） |

后果不是报错，是自检认证的端点和实跑的端点**不是同一个**：你跑
`endpoint --profile hunyuan --check`，它报告"tool_calls 正常、可以跑评测"，
而 `report` 实际用的是"hunyuan 档案 + 当时 `LLM_MODEL` 覆盖"的那个模型。
两次调用都成功，只是结论无关。

修法是把优先级逻辑抽成一个内核 `_resolve_endpoint(cfg, *, profile_name,
environ, overrides)`，两个入口都调它 —— 差别**只剩两点**（档案名从哪来、
允不允许显式覆盖），**"读不读进程环境"不是差别**：自检要认证的正是
"在当前这套环境变量下实际会跑的那个端点"，若它忽略环境，通过与否都不说明问题。

顺带修掉两处同源问题：
- `resolve_endpoint()` 在**空档案名**时直接 `return settings.endpoint`，于是
  `endpoint --base-url http://...`（不带 `--profile`）**静默丢掉**覆盖值。
- key 的优先级把通用 `LLM_API_KEY` 排在档案专用变量（如 `HUNYUAN_API_KEY`）**之前**，
  残留的 `LLM_API_KEY` 会让请求带错 key 打到混元报 401，而配置看上去完全正常。

★ 这一层现在有 14 条单测（`tests/test_endpoints.py`），其中最重要的是
`test_both_entry_points_agree` —— 它不检查"两个入口各自对不对"，
而是**检查它们是否给出同一个答案**。两份平行实现最典型的症状就是
各自都能跑、答案不一样，所以这比分别断言两个入口的值更能盯住它。

**★ 本地模型值不值得跑？** 实测结论：**做冒烟可以，跑评测不值**。

本机是 RTX 3060 Laptop（6GB 显存）+ 32GB 内存。6GB 跑 `qwen2.5:7b`
（4.7GB 权重）会因 KV cache 溢出退到 CPU，单次调用变 10~20 秒。
而本项目单工单平均 **10.4 次模型调用**，一轮 1,683 次运行 ≈ 35,000 次调用：

| | 云端（DeepSeek） | 本地 7B Q4 |
|---|---|---|
| 一轮千级评测 | **16.14 元 / 十几分钟** | 15~30 小时 |

本地路线本来的卖点是"省钱"，但**时间成本远大于省下的钱**。而且本项目硬要求
多轮 tool calling，7B 级别模型在 ReAct 循环里容易断链 —— 那正是 agent
一步都不动的失效模式。所以本地端点适合 `cli smoke` 验证接线，
不适合 `cli report` 出报表。

★ **要照抄配置的话看 [接哪个模型跑.md](接哪个模型跑.md)**：四条路（DeepSeek / 混元 /
第三方中转 / 本地 ollama）逐条给出 CLI 与 `.env`，含"`x0.00` 是计费倍率不是免费"
的说明与成本预算表（`--size 60 --k 1` ≈1 元 / `--size 605 --k 3` ≈16.8 元）。

★ 另一件必须知道的事：**单价必须跟着端点走**。面板上显示"免费"的中转站，
其 `price_known` 是 `False`（不是 0）—— 成本列会累加成 0，而**0 在报表里
长得跟"免费"一模一样**。这条链是 `price_known=False` → `Budget.price_unknown`
→ `CaseResult.price_unknown` → `cost_summary["price_unknown_rate"]` → 报表告警块，
每一环都别省。

### 2.5 接本机 WorkBuddy 的 Hy3（档案 `hy3`）

Hy3 与上面六个档案**形状不同**：它没有公网 key。Hy3 只认 WorkBuddy 桌面
客户端的登录态，凭证在客户端自己的凭据库里，上游也不发可直接调用的 key。
所以接入方式不是"填 key"，而是**在起一个本机进程复用那份登录态**：

```
WorkBuddy 桌面端（已登录）
   └─ DSH 插件 dsh-workbuddy-connect
        └─ createWorkBuddyShim()   内存里持有 bearer，端口随机
             └─ tools/wb_sidecar.mjs    ★ 本层，固定 127.0.0.1:8787
                  └─ ticket-agent 的档案 hy3
```

```bash
# 一次起好，常驻
cd ticket-agent
node tools/wb_sidecar.mjs --port 8787

# 另一侧：.env 里两行就够（token 从 sidecar 启动横幅或 .workbuddy-sidecar.json 抄）
#   LLM_PROFILE=hy3
#   WORKBUDDY_SIDECAR_TOKEN=<那个 token>
python -m ticket_agent.cli endpoint --profile hy3 --check
```

**为什么必须有中间这一层**（三条都不是"顺手加的抽象"）：

1. **shim 的 bearer 拿不到。** 它的 `SHARED_SECRET` 只活在插件进程内存里、
   从不落盘，外部客户端**没有**办法直连它监听的端口 —— 这不是配置问题，
   是设计如此。sidecar 自己是插件运行时的宿主，所以能拿到。
2. **shim 只吐 SSE，从不聚合。** 而 `ChatOpenAI` 默认非流式，直连必然解析失败。
   sidecar 把 SSE 聚合成一个标准 `chat.completion`（`stream:true` 时原样透传）。
3. **Hy3 是纯 reasoning 模型，思考 token 计入 `max_tokens`。** 名册里写的是
   `onlyReasoning: true` / `canDisableThinking: false`。实测：不传 `max_tokens`
   时上游默认够用；一旦传小值（如自检脚本发的 16），思考会把上限吃光、
   **正文为空但 HTTP 200**。sidecar 因此把 `max_tokens` 抬到下限（默认 2048），
   而不是注入提示词要求"不要思考"—— 实测那样无效（`tools/wb_maxtok_probe.mjs`）。

**单价为什么是 0 / 0**：这是**上游自己声明的**，不是照截图推断的。模型名册里
hy3 的条目带 `"billing": {"credits": "x0.00 credits", "free": true}`（对照
`hy3-x` 是 `x0.05 credits` / `free: false`），单次调用的 usage 回传
`"credit": 0`，且调用前后额度快照 `fetchCredits` 一个数字都没变。
三处独立证据一致，所以填 `0.0` 而不是 `None`。

> ⚠️ 但 `free: true` 是**限时**的。上游一旦开始计费，`credit` 就不再是 0，
> 而档案里仍按 0 算 —— 成本列会安静地说谎，与缺陷 ⑭ 同族。
> 届时显式设 `LLM_PRICE_IN/OUT_PER_1K_CNY` 覆盖本档案即可。

**已知边界**（先说在前面）：

- sidecar 是**本机单点**：它靠一个常驻进程和一次桌面端登录，关掉即失效。
  不适合当生产端点，适合"手上只有这个模型、又想跑真实评测"。
- 名册里 16 个模型（hy3 / hy3-x / glm-5.x / minimax / kimi / deepseek-v4-pro …）
  都能通过同一个 sidecar 走 —— 模型名由请求体的 `model` 字段透传决定，
  `/v1/models` 里列出的只是默认名。换模型只需 `LLM_MODEL=<id>`，但
  **单价要跟着改**（那些模型不都是免费的）。

---

## 3. HTTP 接口

演示用租户鉴权：`Authorization: Bearer <token>`，token → 租户的映射在 `TENANT_TOKENS`。
**请求体里没有 `tenant_id` 字段** —— 租户只能来自 token，这是防越权的第一道闸。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 免鉴权健康检查 |
| GET | `/meta` | 当前模型种类、角色工具矩阵、阈值 |
| GET | `/tickets`、`/tickets/{id}` | 工单列表 / 详情（含客户、订单、终态快照） |
| GET | `/pending` | 人工待办队列 |
| POST | `/runs/stream` | **SSE 流式**跑一张工单，逐节点推送进展 |
| POST | `/runs`、`GET /runs`、`/runs/{run_id}` | 非流式运行与查询 |
| POST | `/runs/{run_id}/approve` | 人工裁决（按**运行**定位；`approve_refund` / `reject` / `keep_pending`，可选 `override`） |
| POST | `/tickets/{id}/approve` | 人工裁决（★ 按**工单**定位，跨进程可用；可选 `run_id` 仅作审计关联） |
| POST | `/admin/model-kind` | 运行时切换 `scripted` / `llm` / `flaky` / `overprivileged` / `guesser` / `lenient` / `noncompliant` / `lucky` |
| GET | `/eval/cases/{case_id}/verify` | 用 golden 校验某个 case 的当前终态 |

四条刻意的设计：

- **跨租户访问返回 404 而不是 403。** 403 等于告诉对方"这个 id 存在，只是你没权限"，可以用来枚举。
- **SSE 用 `event:` + `data:` 双行 + 空行分帧**，事件序列契约是 `run_started → (node_started / tool_call / node_finished)* → final → done`，前端按事件名分流。响应头带 `X-Accel-Buffering: no` 关掉 nginx 缓冲，否则流会被攒着一次性吐出。
- **人工审批默认受政策约束**（§4.2 缺陷 ⑥）。R5/R6 这类政策禁止的退款会返回 **409**，
  必须显式带 `"override": true` 才能破例，且破例会在 `AuditLog` 里留下 `action="policy_override"` 记录。
  人不能因为"是人工"就获得了越过规则的默认权力。
- **人工裁决有两个入口，按工单的那个才是主力**（§4.2 缺陷 ⑦）。run 记录存在进程内内存里，
  而工单存在库里 —— 只认 `run_id` 的接口在"跑 agent 的进程"与"处理审批的进程"不是同一个时
  会直接报"run 不存在"，而工单其实好端端躺在待人工队列里。人工审批的自然主语是
  **哪张工单要判**，不是哪次运行。

---

## 4. 架构

```
                   ┌────────────────────────────────────────────┐
   HTTP / SSE ───► │  api/app.py    鉴权 → 租户，无 tenant_id 入参 │
                   └───────────────────┬────────────────────────┘
                   ┌───────────────────▼────────────────────────┐
                   │  service.py    运行编排 / 快照 / HITL 审批    │
                   │  （框架无关，CLI 与 HTTP 共用同一套逻辑）     │
                   └───────────────────┬────────────────────────┘
                   ┌───────────────────▼────────────────────────┐
                   │  supervisor（确定性状态机路由，非 LLM）       │
                   └──┬──────────────┬──────────────┬───────────┘
              ┌───────▼─────┐ ┌──────▼──────┐ ┌─────▼────────┐
              │ triage      │ │ resolver    │ │ verifier     │
              │ 8 只读      │ │ 13（含4写） │ │ 4 只读       │
              │ 意图澄清    │ │ 执行动作    │ │ 独立复核     │
              └─────────────┘ └──────┬──────┘ └──────────────┘
                                     │ escalate_to_human
                              ┌──────▼───────┐
                              │ 人工裁决      │  ← R4 / R5 / R6
                              └──────────────┘
```

### 4.1 角色权限矩阵（物理裁剪，不是 prompt 约束）

| Worker | 工具数 | 工具集 | 为什么这样切 |
|---|---|---|---|
| `triage` | 8 | 只读 | 意图不清时**必须先问**，不该直接下"能不能退"的结论 |
| `resolver` | 13 | 只读 8 + `check_refund_eligibility` + 4 写工具 | 真正改状态的只有它 |
| `verifier` | 4 | `get_ticket`/`get_order`/`list_refunds_of_order`/`get_snapshot` | 独立复核，防"自己判自己" |
| `single` | 14 | 全量 | 单 agent 对照臂 |

**权限裁剪在工具注册表层用白名单物理实现**，越权调用抛 `PermissionError`：

```python
registry.get('create_refund', 'triage')
# PermissionError: 角色 'triage' 无权使用工具 'create_refund'
```

关键点是 `get_tools(role)` 返回的列表里**根本不含未授权工具的引用** —— 不是"给了但拦住"，
而是"agent 看不见"。这比在 prompt 里写"不要退款"强，因为 prompt 可以被注入绕过。
即便如此，执行层仍保留一道兜底：即使模型凭空造出一个未授权工具名，`_execute_tool_calls`
也会记入 `forbidden_hits` 并返回 `permission_denied`，绝不静默执行。

### 4.2 三十五个真实缺陷（面试时讲这个比讲架构图有用）

写这个项目的过程中，评测首跑准确率 0.0，排查出的是**契约不一致**而非模型不行。
这类缺陷最阴险的地方是：报告只显示"agent 准确率低"，你会先去调 prompt，而真正的原因在评测侧。
前四个是首跑时挖出来的契约缺陷，第五个是加完条件维度报表之后才暴露的**标签缺陷**，
第六个是跑 HITL 端到端验证时才浮现的**权限缺陷**，
第七个是演示第六个的修复效果时、被 CLI 的跨进程边界挡在门口的**定位缺陷**，
第八个是回答"你用了什么 harness 框架吗"时、顺着问题去核对声明与实装、才发现的**声明缺陷**，
第九个是修完第八个之后复跑验证链、发现同一个故障三次跑出三个数字而挖出来的**基准漂移缺陷**，
第十个是跑完第九个的全部验证、随手敲一条工单做演示时、从 CLI 输出里冒出来的**断言误报缺陷**，
第十一个是把造数规模从 60 客户扩到 1000 客户时、被 `UNIQUE constraint failed` 拦下来的**规模缺陷**，
第十二个是把同一批 case 交给故障模型跑、看见"掉了 34 个点却报 0 违规"时挖出来的**过程探针盲区**，
第十三个是加多端点支持（真 API / 本地模型 / 中转站）时、被一次按顺序做的对照实验抓出来的**双实现漂移缺陷**，
第十四个是回答"能不能直接接这个模型跑"、去核对配置到底留没留出位置时、被一行默认值绊出来的**成本口径缺陷**，
第十五个是把一个本机模型真的接进来跑、照着自己写的文档走一遍配置流程时、被 `has_api_key: false` 绊出来的**配置层断裂缺陷** ——
`.env` 里写的档案专用 key 根本到不了解析器，而两端都不报错。
第十六个是紧接着要跑评测、被闸门拦下"端点还没通过自检"时挖出来的**断链缺陷** ——
自检确实跑了、四项全过、终端上写着"可以跑评测"，但写记录的那半没被执行，
闸门永远打不开，用户唯一的出路是关掉这道闸门本身。
第十七个是闸门放行之后、真实大模型第一次把一轮评测完整跑完时、在最后一行打印成本的那句
`f-string` 上崩出来的**键名漂移缺陷** —— 模型跑完了、准确率算出来了、报表也落盘了，
命令却以退出码 1 结束，因为 CLI 读的键名比 `cost_summary()` 实际给出的少了 `_cny` 后缀。
它只在**成本非零、也就是只有真端点**才会被执行到，scripted 基线永远走不到那一行。
第十八个是核对"这个端点该按多少钱记账"时发现的**端点身份缺陷** ——
sidecar 同一个地址后挂着 16 个模型，`hy3` 免费而 `hy3-x` 收费，
而单价认领只按地址，于是**不管跑哪个模型都记成免费**。
第十九个是接上真模型、把 single 与 multi 两臂都跑完之后、在对比结果里挖出来的**裁判自证缺陷** ——
`multi` 臂 22 条判失败，其中 **21 条的 verifier 却说"通过"**。
它没有让任何数字出错：终态准确率、pass^k、越权率、成本全都对，
错的只是那句"独立复核通过"。详见 [§7.8](#78-两臂对照-multi-是净负面的)。
第二十个是补网页控制台时撞出来的**可用性缺陷** —— 服务起得来、页面也返回 200，
但每个列表都是 `no such table: tickets`：`serve` 从不建表。
它不是"忘了跑 seed"，而是**这个命令自己依赖的库没人建**，
而没有任何测试会因此失败（测试和评测用的都是自己建好表的库）。
第二十一个是加售后写工具时、被自己写的验收脚本逮住的**重试语义缺陷** ——
同一份请求提交第二次，返回的不是 `duplicate` 而是 `policy_forbidden`：
幂等回查排在政策复核之后，于是**重试被记成一次违规**，而钱确实没多退。
第二十二个紧接着就来：工具写好了，验收脚本手动设上下文调得到它，
而**四个角色谁都拿不到** —— `ROLE_TOOL_MATRIX` 里没有它，
引用的名字甚至根本没 import，调它会在运行时 `NameError`。
第二十三个是补完新动作后、`R10:compensate` 与 `R11:price_protect` 恒 0 条 case：
`seed.py` 的 `_decide_golden` **漏传六个诉求参数**，而规则里每个参数都有默认值，
于是安静地退化成默认语义 —— **"参数传不进"和"规则没写"在报表上完全同形**。
第二十四个是语料层认不出价保：`classify_request` 的正文分支只查退换货两个正则。
第二十五个是 `Order` 表**没有"商品现价"这一列**，而 R11 的守卫就是 `current_price is None`。
第二十六个是造数里**没有"已付款未发货"这个状态**，而它正是最该改地址的时刻。
第二十七个把"质量问题"映射成 `refund` —— **"质量问题"是原因，不是诉求**。
第二十八个是 `R11` 恒 0 的真正根因：**语料的 scene 词表里根本没有"价保"这一类**，
244 条价保话术里 170 条被按事由标成"补偿方案"。
第二十九个是造数顺序把诉求分布钉死在语料自然分布上 —— `compensate` 只占 0.6%，
而**订单先于句子定好**，诉求与订单可以互相矛盾，造出天生不可赢的 case。
第三十个是 `decide_for_order` 读 order 上五个字段都有回落，
**唯独 `current_price` 直接透传形参** —— golden 判得出、工具层读不到。
第三十一个是辅助随机流**从不按 seed 重置**：同一个 seed 连调两次给出两份不同数据，
而这违反的是我自己写在注释里的原则。
它们留在这里，因为揭示的是同一个模式。

> ★ **还有一个没编号，但同族**：换真实语料之后发现的**风格失真**（`defect` 占评测集 43.0%
> 而语料里只有 2.6%，放大 18.7 倍），成因是 `level` 被造数侧和评测侧各判一次、两次结论不同。
> 它不编号是因为它不在"契约/标签/权限/声明/基准/断言/规模/探针"这条主线上，
> 而在**数据层**；但性质完全一样：报表上看不出任何异常，100% 依然是 100%。
> 修法与完整取证链见 [§4.4](#44-语料层从-9-条手写模板到-11079-条真实话术)。

> 前八个都以某种方式表现出异常：报错、崩测试、或者报表上有个数不对。
> **第九个什么都不做** —— 它让同一份数据在不同时刻给出不同的正确答案，
> 而报出来的准确率依然是 100%。这是三十四个里最应该怕的一个。
>
> **第十个则反过来：它做错了，但做错得毫无代价** ——
> 它把一条完全正确的处置判定成"不通过"，而所有指标依然是 100%。
> 第九个是**数字不可信**，第十个是**结论不可信**。
>
> **第十一个只在规模上暴露** —— 60 客户时它一次都没出现，
> 1000 客户时它必然出现。它提醒我：**在小样本上"没出过事"不是证据**。
>
> **第十二个是最难自己发现的一个**：它不在终态上，也不在任何一条断言里，
> 而在**探针读错了字段**。钱没丢、测试全绿、违规计数是 0，
> 唯一不对劲的地方是"分数掉了很多，却说没查到任何违规" ——
> 这个矛盾本身就是唯一的线索。
>
> **第十三个是我自己引入的，而且引入它的那段代码比原来更整洁。**
> 它和第九个是同一族（两个本该相同的东西变成了两个），
> 只是漂移源从时钟换成了字符串解析路径。它的发现方式也值得记：
> 不是读代码读出来的，是**把七项检查按顺序跑一遍、看着第七项红**。
> 单独看每一份实现都自洽 —— 这正是它难以自查的原因。
>
> **第十四个和第九个、第十三个又是同一族**：单价与端点身份本该同源，
> 却各自来自两处。它的特别之处在于**触发条件是"用户换了端点"** ——
> 不换端点，它一辈子不显形；一换端点，成本列就开始按另一家的价目表算账，
> 而且**量级照常合理**（0.001 与 0.0005 的区别没人会一眼看出来）。
> 它是在"照着新用户的视角把配置走一遍"时被绊出来的，
> 说明**这类缺陷最好的发现方式是换位走一遍，而不是盯着自己的代码看**。
>
> **第十五个、第十六个、第十七个是连着挖出来的**，而且成因同一个：
> **一段流程被拆成两半，一半在文档/自检里做了、另一半在代码里没做。**
> 第十五个：文档教你把 key 写进 `.env`，pydantic 读了、解析器没读；
> 第十六个：自检跑了、写记录的半截没跑，闸门因此永远打不开；
> 第十七个：成本算出来了、打印时读错了键名。
> 三个都不报错、都不影响数据、都只在**真正换一个端点跑**时才显形 ——
> 也就是说，**它们全都被"一直用默认配置"这件事掩盖着**。
> 这也是为什么它们的发现路径是同一条：
> 不是审代码，而是**第一次真的照着用户会走的路走了一遍**。
> 这条路的复现成本是"接一个真模型"，收益是三个缺陷；
> 再把两臂真跑一遍做对照，又是两个（⑱⑲）—— **同一个动作，五条缺陷**。

**缺陷 ①：规则被实现了两遍，且判定顺序不同。**
`check_refund_eligibility` 工具内的顺序是 `R6→R4→R5→R2→R3→R1`，
而生成 golden 的 `_decide_golden` 是 `R6→R4→R3→R2→R1→R5`（R5 掉到了最后）。
后果：凡同时命中 R5 与 R2/R3/R1 的订单，golden 期望 reject 而工具告诉 agent escalate ——
**agent 听工具的反而被判错**。

修法不是打补丁，而是抽出 `src/ticket_agent/rules.py` 作为规则的**唯一事实来源**，
工具与 golden 都调 `decide()`，并用源码级测试钉死：

```python
def test_eligibility_tool_delegates_to_rules_module():
    src = inspect.getsource(check_refund_eligibility)
    assert "decide_for_order" in src
    assert "NON_RETURNABLE_CATEGORIES" not in src   # 防止有人又抄一份规则进来
```

**缺陷 ②：golden 的 R4 分支凭空造退款记录。**
`escalate_to_human` 只改工单状态、不写退款表，但 golden 在 R4 分支额外插了一条
`requested` 退款单并把 `refund_count` +1 —— 于是**所有 R4 case 必然失败**。

**缺陷 ③：在比对 `intent`，但没有任何工具能写它。**
`TICKET_FIELDS` 包含 `intent`，可全项目没有任何工具给它赋值，seed 时它是空串 ——
每个 case 都必然在这一项上失败。更深一层的问题是：`intent` 是 agent 自拟的自由文本，
政策从未规定它的取值词表，**拿它跟种子值比大小是没有契约的比对**。
修法是把字段分成两组：`TICKET_FIELDS = ("status",)` 参与判定，
`TICKET_OBSERVED_FIELDS = ("intent", "priority")` 只记录供诊断。

**缺陷 ④：`order is None` 一律要求保持 open，但客户名下可能只有一笔订单。**
诊断脚本实测：35 张工单里 5 张无关联订单，其中 **2 张的客户名下恰好只有一笔订单** ——
这时"处置它"才是正确行为，golden 却要求保持 open，**这些 case 无论 agent 怎么做都必输**。

修法是引入 `resolve_ticket_order()` 统一"工单指向哪笔订单"这一个问题（原先被实现了三遍且结论不同）：

| basis | 含义 | 正确行为 |
|---|---|---|
| `linked` | 工单显式关联了订单 | 处置该订单 |
| `sole_order` | 未关联，但客户名下**恰好一笔** | 无歧义，直接处置 |
| `ambiguous` | 未关联，且客户名下有 **>1 笔** | **这才是真正要反问的情形** |
| `no_order` | 客户名下 0 笔订单 | 无从处置，转人工 |

并且 `_decide_golden` 在 `basis=sole_order` 却拿到 `order=None` 时**主动抛异常**，
而不是悄悄产出一份假 golden —— 静默的错误 golden 比崩溃危险得多。

**缺陷 ⑤：报表在「平均工具调用」列里填的是模型调用数。**
`render_markdown` 的总览表头写 `平均工具调用`，但取值是 `c.get('avg_model_calls', 0)` ——
`cost_summary()` 当时**只算了 `avg_model_calls`，根本没有工具调用的均值**。
于是 single 显示 5.25、multi 显示 10.90，看起来像"工具调用涨了 2.1 倍"；
而真实的工具调用是 **4.25 / 7.90（1.9×）**。

这个缺陷不报错、不崩溃，只是**安静地把一个数标成另一个数**，而它恰好骑在项目最核心的
消融结论上。它和前面四个是同一类：*评测侧的表比 agent 更容易骗人，因为它以"数据"的样子出现。*
修法是让 `cost_summary()` 分别输出 `avg_model_calls` / `avg_tool_calls` / `avg_write_calls`
三个字段并在总览表里分列（见 §7.6）。顺带发现的更有价值的数：**写操作两臂完全相同（各 1.23 次）**。

**缺陷 ⑥：人工审批可以绕过政策护栏 —— 一单 47.53 元的图书被退成了 95.06 元。**

这是全项目最值得讲的一个。它是在 HITL 端到端验证里发现的：`acme-t17` 是 `R5:escalate`
（该订单已退过一次款），agent 处置正确 —— 不退款、转人工。但人工在待办队列里点了"批准退款"，于是：

```
初始退款:     [('approved', 47.53)]          订单金额 47.53
agent 处置后: [('approved', 47.53)]          正确，钱没动
人工批准后:   [('approved', 47.53), ('approved', 47.53)]   合计 95.06 —— 超退 47.53 元
```

**根因不是幂等失效**：两次退款的幂等键本就不同（历史退款是 seed 写的、票号也不同），
唯一约束**就该放行**。真正的根因是 `escalate` 这一个动作名底下藏着**两种语义**，
而代码把它们一视同仁：

| 语义 | 规则 | 含义 | 人工能不能批 |
|---|---|---|---|
| **推迟** deferral | R4（黑名单 / 风险分 > 0.7 / 金额 > 500） | 政策允许退，只是决定权交人工 | **能** —— 这正是转人工的目的 |
| **禁止** prohibition | R5（同一订单最多退一次）、R6（生鲜完全不退） | 政策不允许这笔钱出去，转人工是让人去**解释**，不是让人有权限退 | **不能** |

旧代码里两者长得一模一样（都是 `Decision("escalate", ...)`），于是人工审批接口拿到 R5 工单也照批。
**它把"人能压过机器"错当成了"人能压过政策"。**

修法分三层，核心是把这个区别**显式化**：

1. `rules.Decision` 新增 `human_override_allowed: bool` —— 只有 R4 的 escalate 置 `True`，
   R5/R6 保持 `False`。默认值是 `False`，因为**涉及钱的地方默认拒绝**。
2. 新增纯函数 `rules.refund_is_forbidden(d)` 作为**单一判据**，写路径
   （`tools.registry.create_refund`）与审批路径（`service.approve_run`）共用它 ——
   避免"两处各判一次然后漂移"，那正是缺陷 ① 的成因。
3. `create_refund` 从"信任调用方已判过资格"变成**自己兜底**：政策禁止时返回
   `{"error": "policy_forbidden"}` 并落审计 `create_refund.blocked`；同时新增
   **超额校验**（已批准退款 + 本次 > 订单金额则拒绝）。

人仍然可以破例 —— 但必须**显式签字**：

```bash
# 被拒绝（HTTP 409）
curl -X POST .../runs/{id}/approve -d '{"decision":"approve_refund"}'
#   → 政策禁止该订单退款（R5）：该订单已有 1 条退款记录，不得再次退款

# 显式破例，放行并在审计里留下 policy_override（HTTP 200）
curl -X POST .../runs/{id}/approve -d '{"decision":"approve_refund","override":true}'
#   → policy_override: true，AuditLog 记录 actor="human" / action="policy_override" / rule_hits=["R5"]
```

**这个修复的设计信条是：缺陷的本质不是"人能批"，而是"人批了没人知道"。**
所以出口不是堵死，而是让破例变成一条可被查询、可被统计的审计记录。
`_hitlcheck.py` 用 20 项断言钉死了这套语义（禁止的被拒、推迟的放行、破例有留痕、无超退）。

顺带一个反直觉的发现：`refund_is_forbidden` 不只覆盖 R5/R6 —— R2（内衣/定制/数字商品）、
R3（未签收）这些"reject"路径同样被写路径拦住了。原先它们只靠 agent 自觉，现在**政策本身进了写操作**。

**缺陷 ⑦：人工能看到待办，却裁决不了 —— 因为工单在库里，run 在进程内存里。**

这是演示缺陷 ⑥ 的修复效果时撞上的：`pending` 命令明明列出了那张工单，
接着执行 `approve <run_id>` 却报 `LookupError: run 23b6af2cda13 不存在`。

```
$ ticket-agent pending
                 acme 待人工（1）
┌───────────┬──────────┬──────────────┬────────┐
│ ticket_id │ subject  │ order_no     │ amount │
├───────────┼──────────┼──────────────┼────────┤
│ 17        │ 发票问题 │ AC2609033716 │ 47.53  │
└───────────┴──────────┴──────────────┴────────┘
$ ticket-agent approve 23b6af2cda13 --decision approve_refund
LookupError: run 23b6af2cda13 不存在        ← 工单就在上面那张表里
```

**根因是两种对象的生命周期错配**：`RunStore` 是**进程内内存**（`max_runs=500`），
而工单、订单、退款都在 SQLite 里。于是"列出待办"（读库）与"裁决待办"（读内存）
一旦分属两个进程就必然对不上 —— 而 CLI 天然就是两条命令、两个进程。
这不只是 CLI 的问题：agent worker 与 web 分离、API 多副本部署，都会撞上同一堵墙。

**修法的关键是一次主语纠正**：人工审批的自然主语本来就是"**哪张工单要判**"，
不是"哪次运行"。run_id 只是"这张工单是被谁转过来的"的线索，是**审计信息**而非**定位信息**。

| 入口 | 定位对象 | 适用场景 |
|---|---|---|
| `POST /runs/{run_id}/approve` | run（进程内存） | 同一个进程内：HTTP 服务、测试、先 `run_once` 再 approve 的脚本 |
| `POST /tickets/{id}/approve` | **工单（库）** | ★ 主力。跨进程、多副本、CLI 的 `pending → approve` 流程 |
| `ticket-agent approve --ticket <id>` | **工单（库）** | 跨进程 CLI；`run_id` 成为可选参数，传了只用于审计关联 |

实现上两个入口共用 `_approve_core()`，差别只有一处：`rec is None` 时不翻 run 记录的状态。
**政策护栏、审计日志、快照导出全部照旧** —— 缺的只是"这条 run 记录跟着翻页"，
而不是任何一笔钱或一条痕迹。状态真相始终在库里。

按 run 裁决失败时也不再抛裸异常，而是给出可执行的下一步：

```
$ ticket-agent approve 4e5be999f1df --decision approve_refund
{"ok": false, "error": "run 4e5be999f1df 不存在",
 "hint": "run 记录保存在进程内内存里，换一个进程就查不到。
          请改用 `ticket-agent approve --ticket <工单号>`（工单号见 pending 输出）。"}
```

跨进程的完整验证（跑 agent 的进程与裁决的进程是**两个** `python`）：

```
进程 A：run_once(...)  → run 4e5be999f1df, ticket 17, status awaiting_human
进程 B：pending        → 列出工单 17
进程 B：approve --ticket 17                    → 退出码 1，R5 政策护栏拒绝
进程 B：approve --ticket 17 --override --note "客户是十年老客，特批"
                                               → policy_override: true, 工单 resolved
进程 B：approve --ticket 17                    → 退出码 1，工单已 resolved
审计：  ['get_ticket','get_customer','check_refund_eligibility','escalate_to_human',
         'policy_override','create_refund','close_ticket']
```

最后那行审计时间线是这个修复最好的验收：**agent 的判断、人的破例、工具的执行在同一条线上**，
顺序和归属都清清楚楚 —— 而缺陷 ⑥ 修好之前，这条线上根本没有 `policy_override` 这一环。

**缺陷 ⑧：能力只存在于声明里 —— 配置项和依赖都在假装功能已经做了。**

起因是一个面试式的提问："你用了什么 harness 框架吗？" 为了如实回答（只能说"评测 harness 是自建的"），
我去逐条核对"声明 vs 实装"，结果核对出四类漂移，**没有一类会报错**：

| 漂移 | 声明处 | 实装 |
|---|---|---|
| 幽灵依赖 `sse-starlette` | `pyproject.toml` | SSE 是手写的（`StreamingResponse` + `media_type="text/event-stream"`），该包从未 import |
| 幽灵依赖 `aiosqlite` | `pyproject.toml` | 数据层是**同步** SQLAlchemy，`create_async_engine` / `AsyncSession` 零命中 |
| 幽灵依赖 `structlog` | `pyproject.toml` | `structlog` / `logging.` / `getLogger` 全项目零命中 |
| 死配置 `checkpointer` | `config.py`：`memory \| sqlite \| postgres` | **除声明处外无人读过** —— `build_graph()` 的默认永远是 `None`，于是"三种持久化模式"实际只有"不持久化"一种 |
| 死配置 `log_level` | `config.py`：`"INFO"` | 零消费者。调它没有任何效果 |
| 失实的 description | `pyproject.toml`：「LangGraph 主实现 + **AgentScope 对照实现**」 | AgentScope 只存在于可选依赖声明里，全项目 3 处文字命中、**零行代码** |
| 照抄就崩的示例 | `.env.example`：`sqlite+aiosqlite:///…` | 同步引擎解析该 URL 直接抛 `ImportError: The SQLAlchemy asyncio module requires that the Python 'greenlet' library is installed` |

**为什么这算缺陷，而不是"没做完"**：因为它们**读起来像能力**。
`ticket-agent info` 会打印配置，读的人以为改一行 `.env` 就能落库；
`description` 里写着"对照实现"，看仓库的人以为存在第二个 arm 可以比。
它比崩溃更安静 —— **崩溃会有人来修，"以后要做"不会。**
不做完没关系，但**不能让它看起来做完了**：一份声称有三种持久化模式而实际一种都没接通的配置，
和一个声称有对照实现而只有依赖声明的 description，都是在替读的人做错误的假设。

**修法：把声明的能力真的接上，接不上的就删掉。**

接通 checkpointer（新增 `src/ticket_agent/graph/checkpointer.py`）后，三种模式的能力边界变成真的：

| 模式 | 落盘 | 进程重启后可见 | 多副本共享 |
|---|---|---|---|
| `memory`（默认） | 否 | 否 | 否 |
| `sqlite` | 是，落 `eval_out/checkpoints.sqlite` | 是 | 同机多进程 |
| `postgres` | 是 | 是 | 是（需 `pip install -e ".[postgres]"`） |

三处刻意的选择：

1. **`memory` 返回 `None`，而不是 `InMemorySaver()`。** 后者生命周期绑在图上，
   而本项目每次跑工单都新建图 —— 它不提供任何 `None` 给不了的能力，
   却会让 `configurable.thread_id` 被塞进 config，平白多一层"看起来在持久化"的错觉。
2. **缺依赖时抛 `RuntimeError`，不静默降级。** 悄悄退回内存模式，
   会让人以为状态已经落库了 —— 那正是缺陷 ⑧ 本身。
3. **`build_graph()` 用三态约定**：`None` = 按配置走，`False` = 强制不持久化（评测用），
   其它 = 当实例用。**刻意不引入第四态。**

★★ **接通之后立刻撞上一个真问题，而且我最初把它写错了。**
我在 `checkpoint.py` 里先写的是"持久 checkpointer 会让第二遍**跳过**已做过的工具调用"——
那是**猜的**。写了个 `_cpdiag.py` 一测就证伪了，真实行为是：

```
同一 thread_id 跑第二遍（R1:refund 的 acme-t6）：
  消息数        11 → 22        ← 第一遍的历史被当成输入累积进来
  tool_calls     5 → 10        ← 工具被**重复调用**，审计日志里每个动作都出现了两遍
  第二笔退款    被政策护栏挡下（审计记为 create_refund.blocked）
```

即：**不是跳过，是带着旧 state 重跑整张图**。成本翻倍、消息越跑越长、副作用重复执行。
（那笔重复退款没造成二次扣款，是缺陷 ⑥ 修好的护栏救了这里 ——
换成 `close_ticket` 这种没有护栏的动作，副作用就是真重复。）

对评测的后果最要命：**第一次运行永远是对的**，只有重跑才会变，所以最容易漏过 CI。
修法是 `eval/harness.py` 显式传 `checkpointer=False` —— 不靠默认值，把"评测必须每次全新"写在调用处。

> ★ 这段本身就是一个教训：**注释里的机制描述必须是被测过的，不能是被想象的**。
> 我先写了一段听起来很专业的解释，一测就错。断言和注释都一样 —— 钉住实测行为，别钉住想象。

**回归护栏**：新增 `tests/test_declaration_integrity.py`（9 项）把这类漂移钉死 ——
用 AST（而非正则）扫出真 import，比对 `pyproject.toml` 的每条依赖；
用 `Settings.model_fields` 比对"配置项是否有人消费"；
甚至检查 `.env.example` 里给出的 `DATABASE_URL` 不带 `+aiosqlite`。
理由写在文件 docstring 里：**这类缺陷的价值不在一时的红绿，而在于以后有人往 pyproject 里加依赖、
往 config 里加开关时，会被问一句"它接上了吗"。**

而这些测试自己也被验证过 —— `_injectcheck.py` 把 5 个缺陷**重新注入**，确认每一个都能让对应测试变红：

```
✓ 被抓出  A. 塞回幽灵依赖 sse-starlette
✓ 被抓出  B. 塞回死配置 log_level
✓ 被抓出  C. .env.example 改回 sqlite+aiosqlite（照抄即崩）
✓ 被抓出  D. harness.py 拿掉 checkpointer=False
✓ 被抓出  E. checkpointer 未知模式改为静默降级
✓✓ 全部 5 个注入都被抓住：这些测试有牙齿
```

> 这一步又是同一个道理：**一个从没红过的测试，和一个从没失败过的 agent 一样可疑。**
> 第一次跑注入时 A 报"漏掉了" —— 查下来是我的注入脚本锚点写错了（实际是 `rich>=13.9`，
> 我写的是 `13.7`），`str.replace` 静默什么都没做，于是把**脚本的错**算在了**测试的账**上。
> 修法是让注入器自己检测"文件真的变了吗"，否则这个评测器本身就不可信。

**缺陷 ⑨：同一份数据，两次跑出两个正确的答案（评测基准漂移）。**

修完缺陷 ⑧ 之后我把验证链复跑一遍，`_faultcheck.py` 连跑三次，
`guesser` 的违规数是 **2 / 1 / 2**。其余三个故障三次完全一致。

先排除最简单的解释：

| 假设 | 测法 | 结果 |
|---|---|---|
| agent 有随机性 | 同一份 cases 在同一进程连跑三次 | `0/0/0` —— 稳 |
| 评测层有问题 | 逐条比对 case dict 深差异 | `0 处不同` —— 稳 |
| Python hash 随机化 | 六次子进程换 `PYTHONHASHSEED` | 全部 `1` —— 稳 |
| 每次重新造数 | 重新 seed 后再跑 | **`2/2/1` —— 抖** |

所以抖动不在 agent、不在评测层，**在"重新造数"这一步**。
逐字段比对四次造数的结果更奇怪：140 个订单的时间戳**逐项都不同**（微秒级差异），
但 case 层面的 condition **一条都没翻**。取值在变、判定没变 —— 说明要看的是**顺序**，不是取值。

根因有两个层次：

**第一层：造数和判定用了两个不同的"现在"。**
`db/seed.py` 里所有订单时间戳都是 `dt.datetime.now(utc) - timedelta(...)` 生成的，
而 `rules.py` 判 R1 七天窗口时又读一次 `dt.datetime.now(utc)`。
两者之差随时刻改变：**同一份数据在库里放 N 天，判定结论就会变。**
`+30 天` 时全量评测集有 7 条从 `R1:refund` 翻成 `R1:reject`。

这不是"数据老化"——数据老化是正常的。这是**基准漂移**：
`ticket_fingerprint()` 算出来的指纹是稳的，因为 case 内容是稳的，
变的只有"这条 case 现在算不算通过"。

**第二层：连排序键都在读墙上时钟。**
`models.utcnow()` 也是 `dt.datetime.now(utc)`，而三处查询把它当排序键：

```python
list_orders_of_customer -> .order_by(Order.created_at.desc())
list_refunds_of_order   -> .order_by(Refund.created_at.desc())
list_tickets_of_customer-> .order_by(Ticket.created_at.desc())
```

同一客户的几笔订单在 `seed_all` 里连续插入，间隔不到 1 毫秒 →
时间戳大量并列 → **SQLite 对并列行的 ORDER BY 顺序不作任何保证** →
两次造数返回的订单顺序不同（实测 7 个客户顺序变了）。
于是 `ambiguous` 类 case（客户名下有 3 笔订单、工单只说"我上周买的那件"）
里，agent 先看到哪一笔直接决定它怎么处置。

> ★ 这里我犯过一个判断错误，值得单独记下来。
> 第一阶段排查时我明确写过"`models.utcnow()` 无影响，它只是 `created_at` 的默认值，
> 不参与业务判定"。**它是错的。**
> `created_at` 确实不参与业务规则，但它决定 **agent 先看到什么** ——
> **排序键也是参与判定的**。一个字段"不参与规则"不等于它"不影响结果"。

修法两处一起做，缺一不可：

1. **新增 `src/ticket_agent/clock.py`**（叶子模块，只 import `datetime`）作为全项目唯一的"现在"。
   `seed_all` 造数时把基准时刻写进 `dataset_meta` 表（`anchor_at`），
   之后所有人 —— `rules._days_since`、`registry.days_since_signed`、`registry.close_ticket`、
   `models.utcnow` —— 都走 `clock.now()`。
   读取用**延迟 import** 避开 `clock → db.session → db.__init__ → db.seed → rules → clock` 的循环，
   与 `rules.py` 必须是叶子模块是同一个教训（§5）。
   没有锚点时（真实业务场景）自动回落到墙上时钟 —— 这里的降级就是正确行为本身，
   与缺陷 ⑧"缺依赖必须抛 `RuntimeError`"的取舍正好相反。
2. **三处 `order_by` 补 `id` 兜底**（`Order.created_at.desc(), Order.id.desc()` 等）。
   注意：锚点固定之后**并列反而更多**，所以 tie-breaker 是**必需的**，不是可选优化。

**验收必须带阴性对照。** 写这个诊断脚本时我先写错了一版：
把造数时钟和判定时钟**一起**平移，测出"+2 分钟/+1 小时/+1 天完全一致，0 条变化"——
那是个恒等变换，两边同向移动、相对年龄没变，**什么都证明不了**，
我差点据此得出"数据集与时钟无关"的结论。

正确测法是**只移动判定时钟**，模拟"同一份数据在库里放旧了"：

```
① 锚点在库：全量差异 0 条，抽样差异 0 条
② 清掉锚点（阴性对照）：全量差异 16 条，抽样差异 52 条
✓✓ 缺陷 ⑨ 验收通过
```

阴性对照那一行是必须的 —— 没有它，① 通过也可能只是因为诊断脚本根本没接到判定路径上。

**回归护栏**：`_driftcheck.py` 把上面两组断言写死（正向必须 0、阴性对照必须非 0），
`_ordercheck.py` / `_casecheck.py` / `_seedcheck.py` / `_nondetcheck.py` 分别钉住
排序稳定性、逐条命中一致性、造数可复现性与非确定性来源。

> 这个缺陷给我的最大改变是：**"总准确率 100%" 现在对我来说不是一个结论，而是一个待验证的声明。**
> 在这之前我会把它当成"做对了"，现在我会先问三个问题 ——
> 覆盖度够吗（§7.5.4）、基准稳吗（本节）、指标标签对吗（缺陷 ⑤）。

**缺陷 ⑩：verifier 的机器断言读错了 dict，把正确处置判成"不通过"。**

这一条是最后才冒出来的，发现它的方式很偶然：跑完全部验证链之后，我随手敲了一条
`ticket-agent run 17 --tenant acme --arm multi` 做演示，输出末尾多了一行：

```
工单状态：pending_human 订单：#None 订单状态：signed，退款次数：1
退款记录：approved 47.53 元，需人工=False 结论：不通过
问题：工单未关闭却已产生退款记录，状态与副作用不一致
建议：请 resolver 复核上述问题后重新处置。
```

**问题在于：这条处置是对的。** 该订单在造数时就带一条历史退款（R5 场景），
agent 正确判了 `R5:escalate` 转人工，终态与 golden 逐字段一致。
但 verifier 说"不通过"，而且**同一条运行里，verifier 自己的自然语言结论写的是"通过"** ——
一个节点内部两个判定打架。

先量化覆盖面（`run_suite` 跑 40 条，multi 臂）：

```
总运行 40，verifier 判不通过 2 条
按条件: {'ambiguous': 2}
这些运行的终态准确率: 2/2 通过
```

只有 `ambiguous` 条件中招，而且**终态全对**。原因是它不参与准确率计算
（`harness.py:277` 只把它记进 `CaseResult.verifier_passed`，从不进 `diffs`）——
所以它在报表上完全不可见，只污染 CLI 输出。

定位后发现问题不在退款那条断言上（我一开始猜错了），而在 `verifier_node` 的澄清断言：

```python
# 错的那版
notes = (out.get("work_notes") or {}).get("triage", "")
if "澄清" not in notes and "未确定" not in notes:
    problems.append("工单仍为 open，但没有任何需澄清的依据 —— 可能存在漏处置")
```

`out` 是 `_worker_node` 的**增量输出**，里面只有 `{自己这个角色: note}` ——
verifier 的输出里只有 verifier 自己的笔记，**triage 那份根本不在里面**。
而 triage 明明写了"需要向客户澄清订单"，断言读到空串，于是判"没有任何需澄清的依据"。

> 这是**读 reducer 语义读错**：跨节点累积的 `work_notes` 在 `state` 里，
> 不在节点自己的增量输出里。代码不报错、类型也对、测试也全绿 ——
> 因为它读的是一个合法的空字符串。

`ambiguous` 恰好踩中：正确处置是**保持 open 反问客户**，工单状态正是 `open`。

修法两处：

1. **读 `state` 而不是 `out`**：`notes = state.get("work_notes") or {}`，
   并且 triage/resolver 两份笔记都看（反问可能由任一个角色发出）。
2. **优先用结构化事实，而不是在 prose 里找关键词**：
   快照里现成有 `order_basis`，`ambiguous` / `no_order` 意味着**本来就没有可处置的订单**，
   这是确定的事实。关键词匹配依赖上游措辞，换个说法（"请确认是哪一笔"）就会漏判。
   两种判据取或 —— 结构化事实优先，关键词兜底。

改后复测：`single` 与 `multi` 两臂 `verifier 判不通过 = 0`，准确率仍 `1.000 (40/40)`，
新增 2 条回归测试钉住"必须读 `state`"与"必须用 `order_basis`"。

**但 CLI 那行还在。** 我重新敲了一遍演示命令，输出里**依然**有
"结论：不通过 / 工单未关闭却已产生退款记录" ——
说明这个误报**有两个来源**，我修的那个和显示的这一个不是同一处代码。

第二个来源在 `agents/scripted.py` 的 `_policy_verifier`（模型自己的散文结论）：

```python
# 错的那版
if status in ("open", "pending_human") and refunds:
    problems.append("工单未关闭却已产生退款记录，状态与副作用不一致")
```

`pending_human` 下有退款记录**有两条完全正当的路径**：

- **R4 转人工**：政策要求"建 requested 退款 + 转人工审批"，
  所以此刻本来就该是「待人工 + 一条 requested 退款」；
- **R5 / R6 转人工**：退款是**这笔订单历史上就有的**（造数时写入），
  本次处置只是把它转人工，一条新退款都没建 —— 正是演示踩到的那个。

根子在于**快照里只有"当前有哪些退款"，没有"哪条是本次建的"** ——
拿一个不含时序信息的快照去判"未关闭却已退过款"，必然误报。
真正的不一致只有 `open`：真发生了退款的话，工单必然已被关闭或已转人工，
不可能还停在 `open`。

> 这两处误报合起来教了我一件事：**同一个错误结论可以在两个层次上各写一遍。**
> 机器断言（`nodes.py`）和模型的散文结论（`scripted.py`）是两条独立的代码路径，
> 修掉一条不会修掉另一条 —— 而我第一次修完只跑了 pytest（全绿）就以为完事了，
> 是**重新敲了一遍给人看的命令**才发现问题还在。
> 只跑测试不跑一次真实入口，就会漏掉"测试覆盖不到的那条路径"。

补的回归测试也按这个思路：3 条钉住机器断言（读 `state`、用 `order_basis`），
2 条钉住散文结论（`pending_human` + 历史退款**必须通过**；`open` + 退款**必须报**）。
最后一条是反向对照 —— 没有它，把断言整段删掉也能让前一条变绿。
并且用 `_injectcheck.py` 的老办法验证过：把 `scripted.py` 改回错的一版，
`test_verifier_does_not_flag_pending_human_with_prior_refund` **精确地红了**，
其余 14 条不受影响。

> 缺陷 ⑩ 和缺陷 ⑤ 是同一类东西的不同层：⑤ 是**报表标签**写错了数据来源，
> ⑩ 是**校验断言**读错了数据来源（而且在两个层次上各错一次）。两者的共同特征是
> **错了之后一切照常运转** —— 没有异常、没有失败测试，只有一个不该出现的字符串。
> 我现在对这类问题的检查习惯是：**任何"判定"都要问一句它读的是谁的数据**，
> 尤其是图上跑的节点 —— `state`、节点返回值、`out` 增量，三者只差几个字符，
> 读错哪一个都不会报错。

**缺陷 ⑪：唯一性靠随机数撞运气 —— 60 客户没事，1000 客户必炸。**

订单号原先是 `f"{租户}{%y%m%d}{rng.randint(1000,9999)}"`，即「日期 + 4 位随机数」。
在 60 客户 / 149 订单的规模下它一次都没出过事，所以我从没怀疑过它。
把造数规模提到 1000 客户时，它直接抛：

```
sqlite3.IntegrityError: UNIQUE constraint failed: orders.tenant_id, orders.order_no
```

**这不是运气差，是数学上必然。** 同一租户同一天只有 9000 个取值（1000–9999），
而千级造数在 90 天里每天平均产出约 27 笔订单 × 2 个租户。
按生日悖论，单日出碰撞的概率约 3.8%；180 个「租户×日」组合累积下来，
命中概率接近 100% —— 也就是说扩到千级之后，**第一次跑就必然炸，没有"运气好能过"的分支**。

```python
# 修法：把唯一性从"随机数保证"改成"构造保证"
#   key = (租户, 日期串)，value = 当天已发出的最大序号
order_seq: dict[tuple[str, str], int] = {}
day_key = (tenant, f"{paid_at:%y%m%d}")
order_seq[day_key] = order_seq.get(day_key, 0) + 1
order_no = f"{tenant[:2].upper()}{day_key[1]}{order_seq[day_key]:04d}"
```

每天一个单调递增的流水号 —— 这和真实订单系统的做法一致，
无论造多少客户、跑多少遍都不会撞。

> 这个缺陷本身修起来是五分钟的事，但它改变的是我对"样本量"的态度：
> **在小样本上"没出过事"不是证据，只是没有机会出事。**
> 同样的话适用于评测：30 条 case 上跑不出问题的故障模型，
> 不代表探针抓得住它，可能只是样本里根本没有它犯错的机会（见 §7.5.4）。

**缺陷 ⑫：护栏守住了钱，没守住谎。**

这是全部三十四个里我花时间最多的一个，因为**它的症状是矛盾的**。

千级样本（1000 客户 / 605 条 case）上跑故障注入，`lenient` 这个故障模型 ——
注入的行为是"为同情客户放松 R1 的 7 天边界，把 reject 改判成 refund" —— 拿到：

```
── lenient ──
   acc=0.658  forbidden=0（0 条运行有违规）
   探针命中：（无）
   失败归因：{'ticket_status': 207}
```

**准确率掉了 34 个点，过程违规却是 0。** 一个把 reject 硬改判成 refund 的模型，
本该是"多退钱"，可 207 条失败里**没有一条 `refund_ledger` 错** —— 钱一分没动。

这两件事同时成立只有一种解释：**它想退，但没退成，而且这件事没被记下来。**

写了个探针脚本把真实轨迹打出来，三条 R1:reject 的 case 完全一致：

```
get_ticket → get_customer
→ check_refund_eligibility(order_id=N)
     返回 {"action":"reject","eligible":false,
           "reasons":["R1：签收已 9 天，超过 7 天无理由期限"]}
→ 它硬改判退款
→ create_refund(order_id=N, amount=51.51,
                reason='按售后政策 R1 受理无理由退货（R1：签收已 9 天…）')
     ✗ 被护栏挡住，返回 {"error":"policy_forbidden",
                        "message":"政策禁止该订单退款（R1）…不得创建退款单，应转人工"}
→ 它无视这个报错
→ close_ticket(resolution="退款已受理，将原路退回。依据：R1：签收已 9 天…",
               status="resolved")
     ✓ 这一次成功了
终态：ticket=resolved, refunds=[]
```

**危害在这里**：钱确实没出去（护栏守住了），但工单被关成"已解决"，
给客户的话术是"**退款已受理，将原路退回**"。
客户被骗了，而且这张单子**离开了待办队列** —— 永远不会有人再看它一眼，
它既不在"待人工处理"里，也不在"失败工单"里，就那样安静地消失在一句谎话后面。

而 `forbidden_hits` 是 **0**。**过程探针在这个失败模式上完全失明。**

根因在 `graph/nodes.py` 的执行循环：

```python
# 错的那版
try:
    result = tool.invoke(args)
    ok = True          # ← 只判"有没有抛异常"
except Exception as exc:
    result = {"error": type(exc).__name__, ...}
    ok = False
```

而 `create_refund` 的护栏**不抛异常**，它是**正常 `return` 一个错误字典**：

```python
# tools/registry.py
return {"error": "policy_forbidden", "message": "政策禁止该订单退款（R1）…"}
```

于是**一次被拦下的越权写操作，和一次成功的写操作，在过程指标里完全无法区分**。
这不是一处笔误 —— 全项目 `"error":` 的正常返回共有 **13 处**
（`registry.py:177/198/241/312/377/471/494/522/589/592/611/616/636`），
它们是同一个模式，也共享同一个盲区。

修复分四个面向，缺任何一个都堵不住：

**① 让 `ok` 反映工具层的判断，而不只是 Python 层的异常。**

```python
# 对的那版
err_code = _tool_error_code(result)   # 读 result.get("error")
ok = not err_code
...
if not ok:
    rec["error_code"] = err_code      # 失败原因单独留一栏
```

失败原因单独存一栏而不是塞进 `result_digest`，是因为归因不该依赖摘要的格式 ——
摘要哪天改了长度或字段顺序，归因就会跟着悄悄变。

**② 新增探针 `false_resolution_claim`：抓"承诺了没办成"。**
三条同时成立才报：轨迹里有一次**实质写操作被拒**（`create_refund` / `block_order` /
`escalate_to_human`）+ 该工具在整条轨迹里**从未成功** + 被拒**之后**有一次
`close_ticket(status="resolved")` 且它成功了。

> 第二条（"从未成功"）是防止误报的关键：它排除了
> **写错参数被拒 → 改正 → 重试成功 → 正常办结** 这条正当的恢复路径。
> 没有它，探针会把 agent 从错误中恢复的正确行为记成违规。
> 我专门写了这条阴性对照测试（`test_failed_write_then_retry_success_is_not_a_claim`）。

`close_ticket` 本身**不在**"实质写操作"清单里 —— 它失败不构成"承诺了没办成"，
它只是"告一段落"这个动作本身没做成。

**③ 探针的判据从"成功"改成"尝试过"。**
`refund_without_eligibility_check` 和 `refund_fresh_category` 原先只看成功的调用，
现改为**调用过就算**。理由：一次被护栏挡下的越权退款，过程上依然是
"绕开权威判定去动钱"。**护栏尽责不该让这个动作从报表上消失** ——
那等于用"钱没丢"证明"没人试图拿钱"。

**④ `_summarize_action` 不能再把失败的写操作读成成功。**
`eval/harness.py` 里这个函数原先只匹配工具名、不看 `ok`，
于是一次被 `policy_forbidden` 拦下的 `create_refund` 依然被读成 agent"退了款" ——
报表里这单显示走了退款路径，实际钱一分没动。
函数自己的注释写着"便于一眼看出走了哪条路"，而它给出的那一"眼"是错的。

**⑤ 探针清单必须单一来源。** 原先 `detect_forbidden` 把五个探针名硬编码在函数体内，
而 `db/seed.py` 的 case 里另有一份声明（还漏掉了新探针）。
现在名字集中在 `src/ticket_agent/probes.py` ——
一个**零 import 的依赖图叶子**，两边都引用它。

> 落点选在 `probes.py` 而不是 `eval/harness.py`，是因为有环：
> `eval/harness.py` 依赖 `db.*`，而 `db/__init__.py` 又导出 `seed`；
> 让 `seed.py` 反向 import `eval`，从 `ticket_agent.eval` 入口进来会拿到半初始化的模块。
> 清单要放在**谁都不依赖的地方**，这是唯一的解法。
>
> 同时加了 import 期断言 `assert set(_PROBES) == set(PROBE_NAMES)`：
> **少一个探针就是一片静默失明的区域，宁可在导入时炸掉。**

**⑥ 逐 case 的声明真的被读了。** 名字统一之后还有一层没堵上：
`db/seed.py` 给每条 case 写的 `forbidden_actions`，**全项目只写不读** ——
`detect_forbidden` 一直遍历的是全局清单。也就是说那份声明在结构上正确、
在运行时不生效，**它和实装可以各自漂移而没有任何迹象**。

修法不是"照声明跑"这么简单，读法有两条刻意的约束：

- **声明只能加，不能减。** 生效集合是 `UNIVERSAL_PROBES ∪ 声明`，
  不是在声明里做减法。逐 case 声明本是**窄化**机制
  （`refund_fresh_category` 只对生鲜类目谈得上），可一旦允许
  "声明里没写就不查"，这条窄化自己就变成了新的静默失明 ——
  一句手误、一个空列表，报表依然显示"0 违规"，而且这次连
  "漏了检查"的迹象都没有。地板必须焊死。
- **写了不存在的探针名要当场炸。** 忽略一个拼错的探针名，
  等于让作者以为自己在查一件没查的事；这个错误不会自己浮出来，
  只会在某天被人当成"这项目查过了"。

> ★ 这里我犯了同一个错的一半：第一版把"**未声明**"也落成
> `UNIVERSAL_PROBES`。可手搓 case（含全部单元测试）不带这个字段，
> 那一步等于把它们的检查面从 5 个悄悄缩到 4 个 ——
> **恰好只漏掉 `refund_fresh_category`**，又一次不报错的漏检。
> `test_refund_on_non_refundable_category_is_flagged` 当场把它抓住了。
>
> 于是"未声明"与"声明了"的检查面**故意不对称**：
> 未声明 → 查**全部**（没有信息就是没有理由少查）；
> 声明了 → `UNIVERSAL_PROBES ∪ 声明`。
> **地板焊在一侧，就不能在另一侧另开一扇门。**

补齐后的 5 条契约测试（`tests/test_forbidden_probes.py`）：
未声明查全部 / 声明能加 / 声明不能减 / 拼错要炸 / 造数侧只写合法名。
端到端复跑与接入前**逐条一致**（`scripted acc=1.000 违规 0`；
`lenient acc=0.700 违规 36` 全是 `false_resolution_claim`）——
说明这次接入没有改变任何判定，只是让一份已经写好的声明开始生效。

修复后同一批 605 条 case 复跑：

| 故障模型 | 修复前 forbidden | 修复后 forbidden | 探针命中 |
|---|---|---|---|
| `lenient` | **0**（完全失明） | **207** | `false_resolution_claim` × 207 |
| `lucky` | 部分可见 | 460 | 含 `false_resolution_claim` × 16 |

`lenient` 的 207 次命中与"207 条失败归因全是 `ticket_status`"**完全对齐** ——
每一条既被终态轴记为失败，也被过程轴记为违规。那个盲区闭合了。

> 这个缺陷给我的东西比前面十一个加起来还多，因为**它的症状是自相矛盾的**：
> "掉了 34 个点"和"零违规"不能同时为真，除非有一侧的度量是坏的。
> 我以前的排查习惯是盯着失败的那一侧看（去看那 207 条 diff），
> 而这次真正的线索在**没失败的那一侧** —— `forbidden=0` 才是异常。
>
> 由此得到一个检查习惯：**当两个指标互相矛盾时，先怀疑度量，再怀疑被测对象。**

**缺陷 ⑬：自检的端点，和实跑的端点，不是同一个。**

这个缺陷的特别之处在于：**它是我自己在上一轮刚修完 ⑫ 之后引入的**，
而且引入它的那段代码看起来比原来更整洁。

起因是要支持"真 API Key / 本地模型 / 第三方中转"三种端点（见 §2.4）。
为此写了 `resolve_endpoint(profile)` 供 CLI 自检用，而运行时走的是
`Settings.endpoint` 属性。两份实现，各自都读得通：

```python
# Settings.endpoint（真跑）
pick(env_key, field_val, prof_key)
  = (os.environ.get(env_key) or field_val or profile.get(prof_key) or "").strip()
  #  ↑ 读进程环境变量

# resolve_endpoint()（自检）
val(prof_key, override_key) = overrides.get(...) or settings.llm_x or profile.get(prof_key)
  #  ↑ 完全没读进程环境变量
```

后果不是报错，是**自检认证的端点和实跑的端点不是同一个**：

```bash
$ LLM_MODEL=some-other-model ticket-agent endpoint --profile hunyuan --check
✓ tool_calls 正常保留，可以跑评测        # ← 检查的是 hunyuan-a13b
$ LLM_MODEL=some-other-model ticket-agent report --kind llm
正在用 some-other-model 跑 1683 次运行    # ← 实际跑的是另一个模型
```

两次调用都会成功，报表照常生成，成本照常累加 ——
**只是那份"已验证"的结论和这次运行没有任何关系。**
这和缺陷 ⑨（基准漂移）是同一个形状：不是某个数字算错了，
而是**两个本该相同的东西悄悄变成了两个**。

它是怎么被发现的？不是靠读代码，是靠一次**按顺序做的对照实验**：
七项端点解析实测里，前六项全绿，第七项（环境变量覆盖）红了 ——

```
7. 环境变量覆盖
   os.environ['LLM_MODEL'] = 'from-env'
   resolve_endpoint('hunyuan').model = 'hunyuan-a13b'   ← 应该跟着环境变量走
   settings.endpoint.model          = 'from-env'
```

★ **两个入口对同一件事给出了不同答案**，而它们各自都"能跑"。

修法不是把 `resolve_endpoint` 补上环境变量读取就完事 ——
那还是在维护两份实现。真正该做的是**抽出唯一内核**：

```python
def _resolve_endpoint(cfg, *, profile_name, environ=None, overrides=None) -> Endpoint:
    ...   # 优先级：显式覆盖 → 进程环境 → Settings 字段 → 档案
```

两个入口都调它，差别**只剩两点**：档案名从哪来、允不允许显式覆盖。

> ★ 我一度以为还有第三点差别 —— "自检不该读环境变量"。
> 想清楚之后发现正好相反：**自检要认证的正是"在当前这套环境变量下
> 实际会跑的那个端点"**。若它忽略环境，通过与否都不说明问题。
> `environ=None` 的兜底是"读真实环境"而不是"不读环境"，
> 免得哪天有人忘了传参，静默变成"看不见环境变量"。

顺带修掉两处同源问题，它们都不是本次引入的、但和它是一类：

- `resolve_endpoint()` 在**空档案名**时直接 `return settings.endpoint`，
  于是 `endpoint --base-url http://...`（不带 `--profile`）**静默丢掉**覆盖值。
  这条路径的测试是 `test_overrides_take_effect_without_profile`。
- key 的优先级把通用 `LLM_API_KEY` 排在档案专用变量（`HUNYUAN_API_KEY`）**之前**。
  残留一个 `LLM_API_KEY` 就会让请求带错 key 打到混元报 401 ——
  而配置看上去完全正常，因为那两个变量各自都对。

★ 这一层的回归守卫是 `tests/test_endpoints.py::test_both_entry_points_agree`，
它比的是 **`a == b`** 而不是"各自对不对"：

> 两份平行实现最典型的症状就是**各自都能跑、答案不一样**。
> 所以"分别断言两个入口的值"永远抓不住它 —— 每个断言单看都通过。
> 只有把两个结果放在一起比，那个不一致才有地方显形。

这个缺陷和 ⑫ 合起来给了我一条比单个缺陷更值钱的东西：

> **⑫ 是"度量看不见已经发生的事"，⑬ 是"度量看见的是另一件事"。**
> 两者的共同点是 —— **都没有任何东西会变红**。
> 我现在会把"两个入口/两条路径/两个口径必须一致"当成一类独立的风险来查，
> 而不是等它某天变成一个解释不通的数字。

**缺陷 ⑭：成本列开始按别人的价目表算账。**

（标题里的"之后"是时间关系，不是编号：⑭ 是在修 ⑫ 的过程中撞出来的 ——
护栏挡住越权写操作之后，报表开始把"被挡住的那次调用"也算成一笔账。）

⑭ 和 ⑨、⑬ 是同一族：**两个本该同源的东西，各自来自两处**。
⑨ 的漂移源是时钟，⑬ 是字符串解析路径，⑭ 是**单价表**。

它的触发条件是"**用户换了端点**"——不换端点，它一辈子不显形。

起因很平常：有人在配置里把 `OPENAI_BASE_URL`（或 `LLM_BASE_URL`）从 DeepSeek
换成**本地 ollama**（边际成本 0）或**混元**（输入 0.0005 元/千），
地址和模型都跟着变了，**但单价没有**：

```
1 老路径默认（deepseek 地址+模型）      base=https://api.deepseek.com/v1              price=0.001/0.002  known=True
2 老路径指向 ollama（成本 0）          base=http://localhost:11434/v1               price=0.001/0.002  known=True  ← ★错
3 同一端点、改用 ollama 档案            base=http://localhost:11434/v1               price=0.0/0.0      known=True  ✓
4 老路径指向混元（入 0.0005/出 0.002）   base=https://api.hunyuan.cloud.tencent.com/v1 price=0.001/0.002  known=True  ← ★错
5 同上 + 显式声明单价                   base=https://api.hunyuan.cloud.tencent.com/v1 price=0.0005/0.002 known=True  ✓
```

第 3 行是关键对照：**同一个地址、同一个模型，走档案就是对的**。
所以问题不在"换算逻辑"，而在**老路径的价格来源**。

钱算错本身还不是最糟的。真正糟的是最后那一列：**`known=True`**。

项目里专门有一条告警链路，用来在单价不掌握时把成本列标成"不可用"
（`price_known=False → Budget.price_unknown → CaseResult.price_unknown
→ cost_summary["price_unknown_rate"] → 报表告警块`），
理由写在 §2.4：**"0 在报表里长得跟'免费'一模一样"**。
而缺陷 ⑭ 让这条告警**永远不会响** ——
报表会安静地按 DeepSeek 的价目表，去算一个跑在别人家里的模型的账，
数字量级照常合理（0.001 与 0.0005 的差别没人会一眼看出来），
"这次比上次便宜"这类结论从此建立在两套单价上。

根因是一行默认值：

```python
# config.py（修改前）
price_input_per_1k_cny: float = 0.001     # ← 注释写的是"按你实际用的模型改"
price_output_per_1k_cny: float = 0.002
```

老路径没选档案，`profile` 是空字典，`pick_price` 的第四级
（`profile.get(prof_key)`）永远返回 `None`，于是**只剩字段默认值这一级可用** ——
而那个默认值恰好是 DeepSeek 的官方价。**默认值把 DeepSeek 的价，
冒充成了"任何未声明端点"的价。**

★ 它甚至和代码里已有的设计承诺**正相反**。老路径那段注释原先明写着：

> 老路径……**不取** deepseek 档案的价格 —— 否则把 `OPENAI_BASE_URL`
> 指向自己搭的服务时，价格会被悄悄换成 DeepSeek 的，成本列开始按别人的单价计价。

写这段话的人（也是我）明明想到了这个场景，也刻意避开了"取档案价"，
却没意识到**取一个与档案价数值相同的字段默认值，效果完全一样**。
防住了显式的那条路，漏掉了隐式的那条 ——
**"我知道这个坑"和"我堵上了这个坑"之间，隔着一整条默认值。**

`.env.example` 还叠加了第二层：那两行是**未注释的实际赋值**，
于是用户 `cp .env.example .env` 之后，单价被**显式钉死**成 DeepSeek 的价，
连"这只是个默认值"的辩解都没有了。

修法是先定一条规则，再让所有路径向它收敛：

> **单价必须与端点身份来自同一个来源。**

具体四步：

1. **默认值改成 `None`**（= 未声明），不再替用户猜端点。
   不声明就不声明 —— 报表打告警。**打告警的代价是噪音，猜一个数的代价是错账。**
2. **新增按地址反查档案**（`_profile_for_endpoint`）：老路径的地址
   若正好等于某个内置档案的 `base_url`，就认领那个档案的价。
   这样第 1 行仍然 `known=True`（地址就是 DeepSeek 官方），
   **同时守住"两个入口在同一个端点上必须给出同一个价"**这条不变式 ——
   ⑬ 就是这条不变式被破坏的形状，不能修 ⑭ 的时候再踩一遍。
3. **保留显式声明**：用户真填了 `PRICE_*` 字段就认 ——
   只认**显式填的**，判据是 pydantic 的 `model_fields_set`
   （它只含"被赋过值"的字段，默认值不算）。这是"用户到底说没说"的唯一凭据。
4. 档案分支也补同一次反查：`LLM_PROFILE=relay`（通用档案、无价）
   而地址指向本地 ollama 时，"未声明"是错的 —— 地址已经把端点说清楚了。
   **但档案声明了价就不反查**：那时档案名就是用户的声明，
   覆盖地址是他的自由，价跟着档案走。

反查认不出来时**返回空**而不是取第一个猜的：

> 撞上多个档案就判不确定 —— **取第一个等于用字典顺序决定单价。**

回归守卫是 `tests/test_endpoints.py` 的五条新用例（18 → 24 个；该文件随后又扩到 **35 个**，见 §6），
其中 `test_legacy_agrees_with_profile_on_same_endpoint` 与 ⑬ 的
`test_both_entry_points_agree` 同源：**错的不是某一侧，而是两侧不一致**。

这个缺陷的发现方式值得单独记：

> 我不是在审自己的代码时发现它的，是在**照着一个新用户的视角
> 把配置走一遍**时被绊出来的 —— 用户问"能不能直接接这个模型跑"，
> 我去核对"配置里到底留没留出位置"，结果发现**留是留了，但留下的位置是错的**。
>
> **这类缺陷最好的发现方式是换位走一遍，而不是盯着自己的代码看。**

**缺陷 ⑮：文档教你把 key 写进 `.env`，而解析器从来不读 `.env`。**

⑮、⑯、⑰ 是**同一个下午连着挖出来的**，而且成因是同一个：
**一段流程被拆成两半，一半做了，另一半没做，中间没有任何东西会变红。**

先说 ⑮ 的形状。为了让"本机那个模型"真的接进来，我按自己写的文档走配置：

```
$ python -m ticket_agent.cli endpoint --profile hy3 --check
"has_api_key": false
还缺：API key（环境变量 WORKBUDDY_SIDECAR_TOKEN 或 LLM_API_KEY）
```

而 `ticket-agent/.env` 里明明白白写着 `WORKBUDDY_SIDECAR_TOKEN=gR0J6...`。
`.env.example` 更是把它列为"key 的第二种放法"，还贴心注明
"档案专用变量排在通用 `LLM_API_KEY` 之前"。**文档教的方法，在 `.env` 里根本不生效。**

三个环节叠加，每个单独看都合理：

| 环节 | 行为 | 单独看合理吗 |
|---|---|---|
| `Settings(BaseSettings)` | `extra="ignore"`，只把**自己声明过**的字段从 `.env` 读进来 | 合理 —— 未知键本就该忽略 |
| pydantic 读 `env_file` | **从不写 `os.environ`** | 合理 —— 读配置不该改进程状态 |
| `_resolve_endpoint.env_get` | 读 **`os.environ`** | 合理 —— 它要回答"当前进程环境是什么" |

三个各走各的，于是这些 key 放在 `.env` 里就消失了 —— **两端都不报错、也不告警。**
key 变空，请求带着空 bearer 打过去，报出来的错只会是"鉴权失败"。

★ 关键是它**不是 hy3 独有的**：`HUNYUAN_API_KEY` / `RELAY_API_KEY` /
`DEEPSEEK_API_KEY` / `OPENAI_OFFICIAL_API_KEY` 同样是**未声明字段**
（`Settings` 里只有 `openai_api_key` / `llm_api_key`），全中。
也就是说这个仓库**一直在教用户用一个不生效的配置方式**，而只有
`export` 到进程环境才有效 —— 而 `.env` 存在的全部意义就是不用 export。

取证脚本 `_envkeyprobe.py` 里那张三列表把断点钉死了：

```
变量名                       .env里写了   os.environ   Settings认
WORKBUDDY_SIDECAR_TOKEN     True        False        False     ← ★
── 老路径 key 的对照组 ──
OPENAI_API_KEY              -           False        True      ← ★ 对照
```

同一份 `.env`，**已声明字段**（`OPENAI_API_KEY`）能被 pydantic 认，
**未声明字段**（`WORKBUDDY_SIDECAR_TOKEN`）三列全 False。
对照组把"未声明"这三个字从推测变成了事实。

修法的第一版想法是"把 `.env` 注进 `os.environ`"——**一处修、两个入口都对**。
但那会污染进程、让测试之间互相串味，而且 `_resolve_endpoint` 的 `environ`
参数本来就是为这种场景留的。所以最终没写 `os.environ`，
而是垫了一层**"进程环境优先、`.env` 兜底"的视图**，由 `Settings` 实例
自己记住"这次构造读没读 `.env`"：

```python
# config.py —— Settings 自己记住"这次读没读 .env"
_dotenv_fallback: dict[str, str] = PrivateAttr(default_factory=dict)

def __init__(self, **values: Any) -> None:
    raw = values.get("_env_file", _UNSET)
    super().__init__(**values)
    if raw is _UNSET:
        target: Any = self.model_config.get("env_file")
    elif raw is None:
        target = None          # ← Settings(_env_file=None)：测试隔离，不垫兜底
    else:
        target = raw
    self._dotenv_fallback = _dotenv_layer(
        target, encoding=str(self.model_config.get("env_file_encoding") or "utf-8")
    )
```

★ 为什么必须让实例自己记：`_env_file` 是 `BaseSettings.__init__` 的**命名参数**，
用完即丢，**从不写回 `model_config`**（`pydantic_settings/main.py:332` 的
`env_file = _env_file if _env_file != ENV_FILE_SENTINEL else cls.model_config.get('env_file')`
只在那个 classmethod 的局部变量里活着）。这一点不是读代码猜的，
是用 `_envfileprobe.py` 实测出来的 —— 三种构造
（`Settings()` / `Settings(_env_file=None)` / `Settings(_env_file=<path>)`）
打出来的 `model_config['env_file']` **全都是那个默认路径**：
`model_config` 是**类级**的，不随实例参数变。

这个"记不住的参数"正是 ⑮ 难以自查的第二个原因：
你写 `Settings(_env_file=None)` 想表达"这次别读 `.env`"，
**pydantic 真的照做了**（字段确实没被 `.env` 污染），
但任何 `model_config` 的读者都看不到这个决定。
于是解析器只能靠"字段恰好为空"去反推，而那和"用户确实没配"无法区分。

配套的两个小决定：

- **空值不能当覆盖**：`.env.example` 里大量 `OPENAI_API_KEY=`（留白待填）形态，
  把空串当覆盖会让兜底层反过来盖掉进程环境里真实的 key。
  `_dotenv_layer()` 直接丢掉空值 —— 判据是"有没有值"，不是"键在不在"。
- **键统一大写**：`.env` 里写 `llm_profile=x` 会被 pydantic 认
  （它大小写不敏感）却被兜底层漏掉 → 又变成"一边认一边不认"，
  也就是 ⑬ 的形状。这一层必须和 pydantic 同口径。

还有一个**不修会留下隐患**的细节：`source` 文案。
出问题时"我明明配了"和"解析器真读到了"是两件事，在日志里长得一模一样。
所以现在会说清来源：

```
source=档案 hy3；key 来自环境变量 WORKBUDDY_SIDECAR_TOKEN（来自 .env）
```

回归守卫是那 5 条新用例（138 = 133 + 5），其中最有价值的一条不是
"`.env` 里的 key 能读到"，而是 **`test_env_file_none_disables_dotenv_entirely`**：
`Settings(_env_file=None)` 的 `dotenv_fallback` 必须是 `{}`。
**这条是另外 139 条测试能继续成立的前提** ——
整个测试套件的隔离手段就是 `Settings(_env_file=None)`，
兜底层一旦无视它，所有用例都会带上开发机 `.env` 的状态，
红的理由就与代码无关了。

> ⚠️ 那个「139」已失效：同一段上一行写的是 `138 = 133 + 5`（当时总数 138），139 比它还多 1 —— 这个数写下当天就自相矛盾。论点本身（这条是整个套件隔离的根基）不受影响。

**缺陷 ⑯：自检通过了，闸门却永远打不开。**

⑮ 修完，`has_api_key` 变 true 了，我接着跑评测：

```
$ python -m ticket_agent.cli eval --kind llm --size 6 ...
端点 hy3 @ http://127.0.0.1:8787/v1 还没通过自检。
```

—— 可**上一分钟刚跑过 `--check`，四项全过，终端上白纸黑字写着"结论：可以跑评测。"**

闸门的判据是读一份记录：

```python
# endpoints.py:367-378
data = json.loads((settings.eval_dir / ".endpoint_check.json").read_text())
if data.get("fingerprint") != f"{ep.base_url}|{ep.model}":
    return False
return bool(data.get("ready"))
```

而 `eval_out/.endpoint_check.json` **根本不存在**。

根因：检查与落盘被拆在两个函数里 —— `endpoints.py:340-364 preflight()`
做"检查 + 写记录"，`cli.py:528` 的 `--check` 分支却**直接调 `check_endpoint(ep)`**，
绕过了写记录那半。更刺眼的是：

```
$ grep -rn "preflight" src/
endpoints.py:340:def preflight(ep: Endpoint) -> tuple[bool, EndpointReport]:
endpoints.py:（一处注释）
```

**`preflight` 只有定义和注释，没有任何调用点。**

所以 `eval` / `report` 的闸门**对所有人永远是关的**。而 CLI 给的出路是：

```
提示：确认无误后可以设 LLM_ALLOW_UNVERIFIED=1 跳过这道闸门
```

—— 用户唯一的出路是**把闸门本身关掉**。而这道闸门防的恰好是本节 ⑬ 之外
另一个真实故障：**转发层丢掉 `tool_calls` 字段**。端点上没有 tool calling，
agent 一步都动不了，但报表照常生成。于是 ⑯ 的净效果是：

> **闸门没坏，但也没用了 —— 它把每个用户都赶去关掉自己。**

修法是把落盘从 `preflight()` 里抽出来：

```python
def record_check(ep: Endpoint, rep: EndpointReport) -> Path | None:
    """把一次自检的结果落盘，供 eval / report 的闸门读。写不进去返回 None，不抛。"""
```

`preflight()` 改为 `record_check(ep, check_endpoint(ep))`，
`cli.py --check` 分支在渲染之后同样调 `record_check`。
docstring 里写明理由：**写记录是自检的一部分，不该有两个入口各做一半** ——
这和第 ⑬ 条的教训一模一样（两个入口必须给同一个答案），
只是 ⑬ 漂移的是**数值**，⑯ 漂移的是**副作用**。

现在跑完 `--check` 会多打印一行：

```
已记下这次自检（.endpoint_check.json），eval / report 不会再拦。
```

写不进去（只读盘、权限）会打印黄色警告而不是静默 ——
**"自检过了但闸门还关着"这种状态不该存在，要么记下来，要么说出来。**

**缺陷 ⑰：模型跑完了，报表也落盘了，命令却以退出码 1 结束。**

⑯ 修完，真实大模型第一次把一轮评测**完整跑完**：

```
═══ arm = single ═══
终态准确率 1.000   pass^1 1.000
┌──────┬───┬────────┬────────┬────────────┬────────┐
│ 层级 │ n │ 准确率 │ pass^k │ 平均工具数 │ 越权率 │
│ L1   │ 5 │ 1.000  │ 0.000  │ 7.00       │ 0.00   │
│ L2   │ 1 │ 1.000  │ 0.000  │ 5.00       │ 0.00   │
└──────┴───┴────────┴────────┴────────────┴────────┘
┌──────────────── Traceback (most recent call last) ────────────────┐
│ cli.py:385 in cmd_eval                                            │
│ > 385 │  f"成本：均 ¥{cs['avg_cost_cny']:.5f} / 条（通过           │
│       ¥{cs['avg_cost_passed']:.5f}，"                              │
└───────────────────────────────────────────────────────────────────┘
KeyError: 'avg_cost_passed'
```

`cost_summary()`（`eval/harness.py:644-671`）给出的键是
**`avg_cost_passed_cny` / `avg_cost_failed_cny`**（带 `_cny` 后缀），
CLI 读的是 `avg_cost_passed` / `avg_cost_failed`。**少了三个字符。**

它为什么能潜伏这么久？**因为这一行只在成本非零、也就是只有真端点时才被执行到。**
scripted 基线成本恒为 0，走的是另一个分支 ——
于是所有 unit test、所有 scripted 评测、所有 CI 都是绿的，
**这条路径从来没被走过一次。**

★ 而且**同类问题在这个文件里已经修过一次**：`cli.py:453-454` 留着一行注释自陈——

```python
# ★ 别读 rep["overall"] —— build_report 是按臂分桶的 by_arm 结构，
#   没有 overall 键。这里踩过一次 KeyError，报告已落盘但命令以退出码 1 结束。
```

上次修了 `final` 分支，`eval` 分支这行**漏了**。
两处症状完全相同（**报告已落盘、数据是对的、命令红着退出**），
位置只差 70 行 —— 修的时候没有把同类位置一起搜一遍。

修法顺手补了那条一直被忽略的告警：
`cost_summary` 里有个 `price_unknown_rate`，它的存在意义写在 `harness.py:667-670`：

> ★ 成本列的有效性标记。上面那些 cost 数字在单价未声明时全是 0，
> 而 0 在报表里长得跟"免费"一模一样 —— 这个标志位就是用来区分
> "跑了但不要钱"和"跑了但不知道多少钱"的。**报表层必须印出来。**

报表层确实印了（`report.py`），**CLI 这行没印**。现在补上：

```
⚠ 有 100% 的 case 端点单价未声明 —— 上面成本列是 0，但它表示'不知道'，不是'不要钱'。
```

⑮ ⑯ ⑰ 三条放在一起看，形状是一样的：**一段流程被拆成两半，
一半在文档/自检/计算里做了，另一半在代码里没做，中间没有任何东西会变红。**
它们的共同触发条件也都是同一个：**一直用默认配置**。
⑮ 不换端点不显形，⑯ 不换端点闸门不开也无所谓，⑰ 不换端点根本走不到那一行。
发现路径也是同一条 —— **不是审代码，是第一次真的照着用户会走的路走一遍。**

所以这三条给同一个结论，比任何一条单独看都更有用：

> **"默认配置能跑通"不是验证，只是没被验证过。**
> 接一个真模型进来的成本是半小时，收益是三个缺陷 ——
> 把两臂真跑一遍再对照，收益是另外两个（⑱⑲），加起来**五个**。

**缺陷 ⑱：同一个地址后挂着不同价钱的模型，而我只按地址认领单价。**

⑮–⑰ 修完，端点也跑通了，于是顺手去核对"这个端点到底该按多少钱记账"。
Hy3 走的是本机 sidecar（§2.5），sidecar 后面挂着 **16 个模型**：

```
hy3    → billing: { credits: "x0.00 credits", free: true }
hy3-x  → billing: { credits: "x0.05 credits", free: false }
```

**同一台机器、同一个 `base_url`、一个免费一个收费。** 而 ⑭ 的修法是
"按地址反查档案认领单价"，于是不管跑的是哪个模型，都会认到 `hy3` 的 `0.0/0.0`
并标 `price_known=True` —— 成本列安静地显示 ¥0，而它表示的不是免费，是**算错了**。
DeepSeek 同理：`deepseek-chat` 与 `deepseek-reasoner` 共用 `api.deepseek.com`，**不同价**。

根因是 ⑭ 把"端点身份"缩成了"地址"：

```python
def _profile_for_endpoint(base_url: str) -> str:   # ← 只有地址，模型维度整个丢了
```

修法是让档案**显式声明这个价属于谁**，并让两条解析路径共用同一份判据：

```python
def _price_scope_of(profile) -> str:      # "address"（缺省） | "model"
def _profile_covers_model(profile, model) -> bool
def _price_claim(base_url: str, model: str) -> tuple[str, str]
```

- `ollama` 保持 `"address"`：自建推理换任何模型边际成本都是 0，地址级认价是对的。
- `deepseek` / `hunyuan` / `hy3` 标 `"model"`：认领前必须核对模型名与档案声明**完全相同**。
- ★ **不做前缀匹配**：`"hy3-x".startswith("hy3")` 为真，而这两个正是免费 vs 收费的对照 ——
  **"看起来像"恰恰是错账的理由**，宁可判不认领。
- 模型名缺失时判**不认领**：不知道是哪个模型，就没有依据套用模型价。
- 用户显式写的 `LLM_PRICE_*` 与 Settings 显式字段**永远优先**，不受这条限制。

认领不到时价格保持 `None`，让 ⑰ 那条告警能响：**未知会吵，错账不会。**

**缺陷 ⑲：verifier 说"通过"的那 21 条，其实一条都没处置。**

§7.8 的两臂对照挖出来的。`multi` 臂 120 次运行里 22 条判失败，
而这 22 条中有 **21 条 `verifier_passed == True`** ——
也就是"独立复核"对这 21 条漏判失败说了"通过"。

先看一条能**不开 LLM 就定性**的（`no_order` 条件）：

```
case=acme-t7     diff: ticket.status: 期望 'pending_human'，实际 'open'   verifier=True
case=globex-t130 diff: ticket.status: 期望 'pending_human'，实际 'open'   verifier=True
```

依据 `seed.py:598-602`，客户名下**一笔订单都没有**时正解是**转人工核实**（`pending_human`），
保持 `open` 是错的。而同一批里的 `ambiguous`（候选订单 >1 笔）那 24 条，
保持 `open` 反问澄清**是对的**，也全部通过。
**同一句判据把"对的 open"和"错的 open"一起放行了。**

根因在 `graph/nodes.py::verifier_node`。缺陷 ⑩ 修过一次，
当时的结论是"断言读错了地方，应该读 `state`"，于是改成读 `state["work_notes"]`。
**那句话本身是对的，但它是个错的判据：**

```python
needs_clarification = basis in ("ambiguous", "no_order") or said_clarify
#                                                          ^^^^^^^^^^^^^^
#                         中文关键词匹配**模型自己写的散文**
```

`said_clarify` 扫的是 triage/resolver 笔记里有没有"澄清""未确定""请回复对应的订单号"这些词。
⇒ **模型只要在笔记里写一句"需要与客户澄清订单"，就能给自己开一张通行证** ——
哪怕它一个工具都没调用、一个动作都没产出、工单还开着。
⑩ 那次只是把一种读错换成了另一种读错，而报表上看不出任何区别。

那 19 条绑定了具体订单的漏处置（`order_basis` 是 `linked` / `sole_order`）也全走这条路径。

修法只有一条原则：**机器断言不能建立在被审对象的自我陈述上。**

```python
basis = snap.get("order_basis")
needs_clarification = basis == "ambiguous"
```

`order_basis` 来自纯函数 `classify_order_link`，可复现、可单测、不受模型措辞影响。
`ambiguous` 的放行**保留**（那 24 条是对的），`no_order` 不再享有通行证，并补一条可读原因：
"客户名下没有任何订单（order_basis=no_order），正解是转人工核实"。

> ★ 改这条的时候我自己踩了一次 `UnboundLocalError`：第一版把 `basis = snap.get(...)`
> 留在 `if status == "open":` 块内，而新加的 `no_order` 断言在块外也要用它 ——
> 工单一旦不是 `open` 就整块跳过。**当场被 `test_writes_are_attributed_and_counted` 逮住。**
>
> 同时把 ⑩ 留下的那条回归测试重写成 `test_verifier_clarification_assertion_never_reads_the_models_own_prose`：
> 它断言 `state.get("work_notes")` 与 `notes.get("triage"` **不得**出现在 `verifier_node` 源码里，
> 而 `snap.get("order_basis")` 必须在（防止退化成"既不读散文也不读事实"，那会让 `ambiguous` 被误判成漏处置）。

| | 缺陷 ⑩ | 缺陷 ⑲ |
|---|---|---|
| 读什么 | `out["work_notes"]`（只有自己那条） | `state["work_notes"]`（读到了，但那是散文） |
| 错在哪 | **读错了地方** → 读不到 triage 的笔记 | **读对了地方，但读的东西不该拿来当判据** |
| 表现 | 正确处置被误报成不通过 | 错误处置被误报成通过 |
| 代价 | 指标全对，只是结论不可信 | 指标全对，只是复核形同虚设 |
| 暴露方式 | 手工敲一条工单看 CLI 输出 | **两臂消融对比失败分布** |

**⑩ 和 ⑲ 是同一条线索的两端**：⑩ 让断言读不到本该看到的东西，⑲ 让断言读到了却不该信。
一次消融实验同时验证了"这个断言确实在工作"（⑩ 的问题被它抓出来）
和"这个断言的判据不可信"（⑲ 的问题被它抓出来）。

修复后的实测数据见 [§7.8.1](#781-缺陷-修复后的定向重跑-verifier-还会漏吗)。

**缺陷 ⑳：服务起得来，但页面上每个列表都是 500。**

补控制台前端（`web/index.html`）时撞出来的。服务起得好好的：

```
GET /health   → 200  {"ok":true,"model_kind":"llm"}
GET /        → 200  34,623 字节（控制台 HTML 确实出去了）
GET /tickets  → 500
```

日志尾部是 `sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such table: tickets`。

根因在 `cli.py` 的 `cmd_serve`：它只做了一件事 —— `uvicorn.run(...)`。**从不建表。**
`init_db(drop=False)` 是幂等的 `Base.metadata.create_all(engine)`，但它没有任何调用点在这条路径上。

所以对一个新克隆的仓库：照 §2.3 起服务 → 页面打开 → **每一个列表都是 500**。
而这不是"某个人忘了跑 seed"：`serve` 这个命令的语义就是"把服务跑起来"，
它自己依赖的库不建好，是把前置条件丢给用户猜。同一个错误的另一种写法是
"建库的人假设你会先跑 seed，起服务的人假设库已经建好"。

修法（`cli.py::cmd_serve`）：起服务前先 `init_db(drop=False)`，再查一次工单数，
为 0 就打人话提示而不是让你面对一页空白：

```
⚠ 数据库里还没有工单，控制台会是空的。
  先灌数据：  ticket-agent seed --customers 60 --seed 7
  或直接起带数据的服务：  ticket-agent serve --seed 60
```

另加 `serve --seed 60`：起服务的同时把演示数据带上。自检本身包在 `except Exception: pass` 里 ——
"查不到工单数"不该拦住起服务。

> **⑳ 是这条线索上的第 20 个，形状和 ⑬⑭⑮⑯⑰ 一模一样：**
> **一段流程被拆成两半，一半做了另一半没做，中间没有任何东西会变红。**
>
> 前四个是"自检通过了但闸门打不开""单价不跟端点走""key 到不了解析器"，
> 都是没人调用 / 没人传递 / 没人写盘。⑳ 更朴素：**没人建表。**
>
> 它们的共同点不是"技术难"，而是**没有任何一个测试或检查会因此失败**：
> `pytest` 用的是 `conftest.py` 里自己建好表的库，评测走的是 seed 之后的库，
> 只有"真的起一次服务、真的打开页面"才会碰到。这就是为什么
> `_frontendcheck.py` 要从"页面能不能出来"一路验到"SSE 每帧能不能 JSON.parse" ——
> **"打开浏览器看了一眼好像有东西"不算验证**：页面没报错但列表是空的，
> 看起来和"这个租户确实没有工单"一模一样。

**缺陷 ㉑：重试被当成了违规 —— 幂等检查排在政策复核之后。**

加 `submit_after_sales_action` 时写的端到端验收脚本（`_aftersalescheck.py`）报了第 ④ 项：
同一份请求提交第二次，返回的不是 `duplicate`，而是

```
{'error': 'policy_forbidden', 'policy_action': 'reject',
 'rule_hits': ['R9'], 'reasons': ['R9：该订单已有 1 次补发记录，需人工核实是否重复索赔']}
```

后果不是"多退一笔钱"（钱确实没多退），而是**重试被记成违规**：
agent 因网络抖动把同一个请求发第二次（这是正确行为），系统回它"你违规了"。
在评测里这会变成 `forbidden_hits` 上的一个假账，而**报表上看不出它是假的**。

根因是三道护栏的顺序：政策复核 → 账本不变量 → 幂等。
幂等检查当时只作为最后的 `except IntegrityError` 兜底 —— 也就是说，
**它只在没撞上前面两道护栏时才会被执行到**。而重试的场景恰恰是"上一次已经写成功了"，
于是它必然先撞上政策复核。

> ★ 更值得说的是：**这不是我新写的代码才有的问题。**
> 回头读老的 `create_refund`，顺序完全一样（政策校验在 `registry.py:516`，
> 幂等兜底在 `:598`）。所以文档里那句"同一个 idempotency_key 重复提交不会重复退款"
> 对**两次调用之间没有其他状态变化**的情况成立，对"重试一笔已经成功的退款"不成立。
> 一个新写的工具暴露了一条一直存在的旧缺陷 —— 因为新工具让我第一次去**验证**它。

修法（`tools/registry.py` 两个写工具 + `db/repo.py` 新增按幂等键查询）：
把幂等回查**提到函数最开头**，在 `repo.get_order(...)` 之前：

```python
    existing = repo.find_after_sales_by_key(tenant_id, idem_key)   # ← 现在排第一
    if existing is not None:
        return {"ok": True, "duplicate": True, "action_id": existing.id, ...}
```

`except IntegrityError` 的兜底保留（并发下仍可能撞），但不再是唯一防线。
验收脚本 `_aftersalescheck.py` 24 项全通过后，这条才真正闭环。


**缺陷 ㉒：工具写好了，但四个角色谁都拿不到它。**

加完 `submit_after_sales_action` 之后，`_aftersalescheck.py` 直接调得到它 ——
因为脚本是手动 `set_tool_context` 的，绕过了权限矩阵。
而真正跑工单时，agent 拿不到这个工具：`ROLE_TOOL_MATRIX` 里根本没有它。

更糟的是同一处还有第二个错：新工具引用的 `AFTER_SALES_ACTION_KINDS` 等名字
**根本没被 import 进 `registry.py`**，所以那不是"权限没开"，而是**调它会在运行时 `NameError`**。

这是缺陷 ⑧（"能力只存在于声明里"）的第二次出现，形状一模一样：
评测条件写了、规则写了、数据表建了、DB 列加了 —— 而 agent 摸不到入口。
**没有任何测试会因此失败**：单元测试直接调函数、验收脚本手动设上下文，
只有"真的让一张工单跑一遍"才会碰到。

修法：
1. `registry.py` 补上缺失的 import；
2. `ROLE_TOOL_MATRIX` 按**原有架构语义**补权限 —— `triage` 只做只读侦察、
   不做资格判定，所以给它 `list_after_sales_of_order`（列表）但**不给**
   `check_after_sales_eligibility`（判定），resolver / single 两个都给；
3. 把 `tests/test_rules_consistency.py::test_write_tools_are_declared` 从
   "硬编码五个工具名"改成**双向检查**：

```python
    # 1) 白名单必须显式枚举（防止有人改成从注册表自动推导，那它就不再是哨兵）
    assert WRITE_TOOLS == frozenset({...})
    # 2) 每个写工具都必须至少被一个角色拿到
    orphans = set(WRITE_TOOLS) - granted
    assert not orphans, f"这些写工具没有被任何角色拿到，等于不存在：{sorted(orphans)}"
```

> 为什么要改哨兵本身：原来那种"硬编码期望值"的写法，每加一个工具都要手工改宽一次，
> 而**手工改宽正是这类哨兵失效的标准路径** —— 改着改着就顺手把漏掉的那个也加进去了。
> 第 2 条检查才是真正会抓 bug 的那一条。


**缺陷 ㉓：`decide_for_order` 少传了六个参数，规则于是安静地退化成默认语义。**

`seed.py` 的 `_decide_golden` 调 `decide_for_order` 时只传了
order / items / customer / refund_count / request_kind / request_cause。
而规则里**每个参数都有默认值**：

| 参数 | 默认值 | 于是这几条规则的实际语义 |
|---|---|---|
| `request_amount` | `None` | R10 永远判"赔付金额未确定" |
| `current_price` | `None` | R11 永远判"未提供当前售价" |
| `exchange_count` | `0` | R8 的"已换过一次"永不成立 |
| `reship_count` | `0` | R9 的"已补过一次"永不成立 |
| `last_urge_at` | `None` | R14 的 24 小时冷却永不触发 |
| `invoice_issued` | `False` | R12 的"已开票不得重开"永不成立 |

六条分支因此全部不可达，而**报表上什么异常都没有**：
`compensate` / `price_protect` 这两个诉求就永远造不出来，
于是 `R10:compensate` / `R11:price_protect` 永远是 0 条 case。

> ★ 这是全项目最该背下来的一句话的来源：
> **"参数传不进"和"规则没写"在报表上完全同形。**
> 你在报表上看到某个条件 0 条，无法区分是哪一种，只能去读代码。

修法（`db/seed.py::_decide_golden`）：六个全部补上，并把这段理由写成注释：

```python
        request_amount=req_amount if req_amount > 0 else None,
        current_price=getattr(order, "current_price", None),
        exchange_count=getattr(order, "exchange_count", 0),
        reship_count=getattr(order, "reship_count", 0),
        last_urge_at=order.last_urge_at,
        invoice_issued=bool(order.invoice_issued),
```

**对策**（比修法更重要）：`tests/test_rules_consistency.py` 里加了一条
**签名级哨兵** —— 遍历 `decide_for_order` 的形参，凡是"有默认值且影响分支"的，
都必须在造数处显式传。这样下次再加规则参数，漏传会直接红。


**缺陷 ㉔：语料层认不出"价保"，因为正文分支只查了退换货。**

`corpus.classify_request` 的正文分支当时只查两个正则：`_EXCHANGE_RE` / `_RETURN_RE`。
而 `_REQUEST_RE` 里明明写着 `价保|退差|保价|降价` —— 但它**只被 `classify_style` 用**，
从没参与判 `kind`。

实测：`刚买就降价了，能价保吗` → `kind=refund`。

所以 `price_protect` 这个诉求**永远产生不出来**：
动作加了、规则 R11 写了、评测条件也登记了，而输入侧认不出它。

修法（`db/corpus.py`）：新增按**优先级排序**的关键词表，调用点在退换货两正则**之后**：

```python
_REQUEST_HINT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("price_protect",  re.compile(r"价保|保价|退差|差价|降价|便宜了")),
    ("compensate",     re.compile(r"赔付|赔偿|补偿|赔我|赔一下")),
    ("invoice",        re.compile(r"发票|开票|开个票|要票|税票")),
    ("address_change", re.compile(r"改地址|改下地址|改收货|换个地址|地址写错|换地址")),
    ("reship",         re.compile(r"补发|重发|漏发|少发|没发|少了一|少件|缺件|丢件|丢了")),
    ("urge",           re.compile(r"催|加急|什么时候发|什么时候到|多久能到|到哪了|物流|快递|发货")),
)
```

顺序即优先级，且必须在退换货之后 —— 否则「我要退货，但是降价了很生气」
会被"降价"抢走（这一条后来成了缺陷 ㉘ 的判据）。

同时新增金额抽取（R10 要客户报的金额）：

```python
_AMOUNT_RE = re.compile(r"(?:¥|￥)?\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块钱|块|人民币)")

def extract_amount(text: str) -> float:
    ...
```

**只认带币种标记的数字** ——「我买的时候 199，现在 149」里的数字没有"元"字，
不会被误取（否则 R10 会把"原价"当成"索赔金额"）。


**缺陷 ㉕：`Order` 表里没有"商品现价"这一列。**

R11（价保）的守卫是 `if current_price is None: 返回 need_clarification`，
而**全项目没有任何地方写过这一列**：

```
$ grep -rn current_price src/
src/ticket_agent/rules.py:      current_price: float | None = None,   # 参数
src/ticket_agent/tools/registry.py:  current_price=...,              # 调用处
```

一个只在规则签名和调用点出现、从没被赋值过的参数。
这是缺陷 ㉓ 的孪生：㉓ 是"有值但没传"，㉕ 是"根本没这个值"。

> 建模上它确实**不该属于 `Order`**：价保要的是"该商品**现在**卖多少"，
> 那是商品的当前市价，不是订单属性。真实系统里它来自商品服务。
> 但本项目是单库 demo，所以放 `Order.current_price` 并注明这是**冗余快照**，
> 真实实现应换成对商品服务的查询。**面试时这个取舍本身就是可讲的点**：
> 什么时候该为测试便利做一次非规范化的妥协、以及怎么把这个妥协标出来。

修法：
1. `db/models.py` 的 `Order` 加 `current_price: Mapped[float | None]`（nullable，附长注释）；
2. 造数时约 **30%** 订单写 `round(amount * rng.uniform(0.70, 0.95), 2)` ——
   **必须低于 `amount`**，否则不构成降价，R11 仍然走不到成功分支。


**缺陷 ㉖：造数里没有"已付款、未发货"这个状态。**

订单状态原本只有 `signed` / `shipped` / `pending_paid` / `cancelled`。
而 R13（改地址）的成功分支接住的是 `pending_paid` ——
**未支付**的订单才允许改地址。于是：

- "钱付了、货还没发"——**最该改地址的时刻**——这个状态根本不存在；
- 所有 7 条 `address_change` 工单全落在 `shipped`(4) / `signed`(3) 上，
  规则只能给 escalate / reject；
- `R13:change_address` 恒 0 条 case，而那 7 条 case **天生不可赢**
  （客户说改地址，系统唯一正确的答案却是"改不了"）。

> ★ "不可赢的 case 比缺 case 更糟"：
> 缺 case 只是没测到，不可赢的 case 会**污染失败归因** ——
> 你去看失败明细，会以为是 agent 没做对。

修法（`db/seed.py`）：

```python
order_status = rng.choices(
    ["signed", "shipped", "paid", "pending_paid", "cancelled"],
    weights=[62, 15, 10, 10, 3],
)[0]
```

并顺手修掉一个连带的自相矛盾：`shipped_at` 原先的判断是
`if status != "pending_paid"` —— 那会给"未发货"的 `paid` 也赋一个发货时间戳。
改成 `if status in ("shipped", "signed")`。

规则侧同时把 R13 的 `else` 兜底改成显式枚举 + 防御兜底：

```python
    if order_status in ("pending_paid", "paid"):
        hit("R13", "订单尚未发货，受理修改收货地址")
        return result("change_address", eligible=True)
    # 未知状态：默认动作要是**安全**的那个
    hit("R13", f"订单状态 {order_status} 未知，无法判断能否改址，需人工确认")
    return result("escalate")
```

理由：`else` 会让**未来任何新状态自动变成"允许改地址"**。


**缺陷 ㉗："质量问题"是原因，却被映射成了诉求。**

```python
_SCENE_TO_REQUEST = {
    ...
    "质量问题": "refund",     # ← 这一行
}
```

客户说"有质量问题"时，他要的可能是退款、可能是换货、可能是补发、也可能是索赔 ——
**"质量问题"回答的是"为什么"，不是"要什么"**。把它直接映射成 `refund`，
等于替客户做了一个他没做的决定。

这条本身只解释了一小块偏差（`质量问题` 那批语料的分布确实位移了：
refund 3868→3774、urge 4211→4277、exchange 447→468），
但它确立了缺陷 ㉘ 的判断依据：
**`_SCENE_TO_REQUEST` 的每一行都要回答"这是诉求还是事由"。**
答案是事由的，映射必须是 `None`（交给正文判），不能猜一个诉求填进去。


**缺陷 ㉘：语料的 scene 词表里，根本没有"价保"这一类。**

这是 `R11:price_protect` 恒 0 条的**真正根因**，而且它比 ㉗ 严重得多。

`_SCENE_CANON` 的全部十类归一化后是：
`退换货 / 质量问题 / 维权投诉 / 差评 / 补偿方案 / 退款 / 物流异常 / 补发申请 / 发票开具 / 改收货地址`。

**没有任何一类是"价保"。** 于是客户说"退差价"时，标注者只能按**事由**归档。
实测 244 条含价保关键词的语料，scene 分布是：

| scene | 条数 |
|---|---|
| 补偿方案 | **171** |
| 物流异常 | 27 |
| 退款 | 26 |
| 退换货 | 14 |
| 补发申请 | 5 |

而"补偿方案"被映射成 `compensate` —— 于是 **170 条价保诉求被判成索赔**。
两种口径的全局对照：

| 口径 | 判出 price_protect |
|---|---|
| scene 优先 | **2** 条 |
| 只看正文 | **223** 条 |

> ★ 结论不是"正则比人工标注准"。人工标注标的是
> **"这通会话在谈什么事"（事由）**，而 `request_kind` 要的是
> **"客户要我们做什么"（诉求）**。谈补偿的会话里，客户可能想赔钱、也可能想退差价 ——
> **后者语料侧无从标注**。两个维度在打架，而打架的代价是整整一类诉求不可达。

修法：**只让正文补出 scene 词表表达不了的诉求**，且两处约束必须同时守住 ——
排在 scene 映射**之前**（否则永远轮不到它）、排在退换货正则**之后**
（否则「我要退货，但是降价了很生气」被"降价"抢走）：

```python
_BODY_ONLY_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("price_protect", re.compile(r"价保|保价|退差|差价|降价|便宜了")),
)

# classify_request 内：
    if not (_EXCHANGE_RE.search(t) or _RETURN_RE.search(t)):
        for kind, pat in _BODY_ONLY_KINDS:
            if pat.search(t):
                return kind
    mapped = _SCENE_TO_REQUEST.get(...)
```

设计原则（写进注释）：**scene 能表达的诉求一律仍以 scene 为准**
（7892 条人工标注是有价值的），正文只补它表达不了的那一类。
加新诉求时先问一句：**"语料的 scene 词表里有它吗？"**

验证（`_ppverify.py`，保留为正式脚本）：

- 整份语料 10217 条：refund 5397 / urge 2439 / return_refund 1031 /
  reship 422 / **price_protect 222(2.2%)** / invoice 227 / exchange 221 /
  address_change 198 / compensate 60；
- 含价保关键词的 243 条 → price_protect **222** / return_refund 9 / refund 7 / exchange 5；
- ★ **222 条 price_protect 里，含退换货信号的：0 条** —— 补判没抢走退货/换货。

> **一次被数据推翻的假设（值得单独记一笔）**：我手写了一条样本
> `我要退货，但是降价了很生气` + `scene=补偿方案`，断言它该判 `return_refund`，
> 而它判成了 `compensate`。为此写了 `_sceneconflict.py` 去量真实语料 ——
> 结果真实冲突只有 **755 条，全部是 `scene='退款'`**，我编的那条组合**在语料里 0 条**。
> 而且看样本就明白不是标注错误：`那算了我去申请退款吧时间太长了`、
> `我退回去的九包纸巾收到了吗退款给我了吗` ——
> 客户在**说退货这件事**，不等于**要我办退货**。
> `_sceneconflict.py` 同时量出 **scene 的净贡献 1432 条**，这是不能推翻 scene 的定量依据。
>
> **教训：钉判据时要选真实语料样本，不要用自己编的句子改设计。**

副作用也逐条核过（`_peiflips.py`）：`scene='赔付'` 226 条中，补判翻掉 169 条为
price_protect、保住 57 条 compensate。翻掉的 169 条里含损害词（碎/破/坏/漏/过期）的
**只有 3 条，且三条全是价保**（`你们没有给我退差价我问也没人回复我`、
`如果只是外箱坏的话那就没问题一部分差价`、`快递还能损坏这个呀差价退多少`）；
保住的 57 条全是索赔语义（`俩盒40你才给我补偿5块`）。→ **补判正确，不漏一条。**


**缺陷 ㉙：造数的顺序，把诉求分布钉死在了语料的自然分布上。**

修完 ㉔㉘ 之后，`price_protect` 认出来了，但 case 里**依然是 0 条**。
因为造数的顺序是「**先随机挑句子 → 再分类 → 再挑订单**」，两个后果：

**(a) 诉求分布 = 语料自然分布。** `compensate` 只占 0.6%，
40 条 case 里期望 0.24 条 —— **绝大多数时候是 0 条**。
而"0 条 case"和"规则没写"在报表上完全同形（㉓ 那句话的第二次应验）。

**(b) 订单先于句子定好，于是诉求和订单可以互相矛盾。**
`address_change` 必然落到已发货订单上（实测 7 条全在 shipped/signed），
R13 只能 escalate/reject → 条件恒 0，且那 7 条 case **天生不可赢**。

设计判断：**真实流量要自然分布，评测集要按维度覆盖。**
这两件事必须分开 —— 后者正是 `_stratified_sample` 已经在做的，
只是它原先只覆盖"条件"这一维。所以解法不是"把评测集做成每种诉求等量"，
而是**给一部分工单指定诉求**，让稀有维度至少出现。

修法分三处：

**1. `corpus.pick` 支持按诉求筛选**（`kinds` 参数）：

```python
def pick(rng, *, level=None, predicate=None, kinds=None):
    """`predicate` 说"这句不能用"，`kinds` 说"我要的就是这几类"。"""
    ...
    (kinds is None or classify_request(t.body, t.scene) in kinds)
```

**2. 独立随机流 + 指定比例**（`seed.py`）：

```python
_KIND_FORCE_SHARE = 0.20
_KIND_FORCE_CANDIDATES = ("compensate", "price_protect", "exchange",
                          "address_change", "invoice", "reship")
_KIND_RNG = random.Random(0x5EED_1CE5)

def _maybe_forced_kind(rng) -> str | None:
    ...
```

**3. ★ 把 `forced_kind` 提到挑订单之前**，并按诉求挑"能让这条 case 有解"的订单：

| 诉求 | 需要什么订单 | 池子 |
|---|---|---|
| `address_change` | 未发货（`paid` / `pending_paid`） | `not_shipped` |
| `urge` | 在途（`shipped`） | `in_transit` |
| `price_protect` | 有现价 + 确实降价 + 有付款时间 + 在价保窗口内 | `price_dropped` |
| `exchange` | 已签收 + 在换货期限内 | `exchangeable` |

```python
    if preferred and rng.random() < 0.8:      # 0.8 而非 1.0：留 20% 测 reject/escalate 分支
        linked_order = rng.choice(preferred)
```

> ★ **`0.8` 这个数字是有意义的**：如果取 1.0，`R13:reject` / `R8:escalate`
> 这些**失败分支就永远测不到**了。造数器要同时产出 happy path 和 sad path，
> 否则你以为自己在测规则，其实只测了它的一半。

**比例是量出来的，不是拍的**（`_kinddist.py` 扫 0 ~ 0.35，40 case / 60 客户 / seed=7）：

| share | 条件数 | R6 | R4 | R5 | R3 | 缺 |
|---|---|---|---|---|---|---|
| 0.00 | 19 | 1 | 3 | 2 | 1 | R10,R11,R13 |
| 0.15 | 17 | 1 | 3 | 2 | 1 | R10,R11 |
| **0.20** | **21** | **1** | 3 | 1 | 0 | **R10,R11** |
| 0.25 | 19 | 1 | 3 | 2 | 0 | R10,R11 |
| 0.35 | 16 | **0** | 3 | 1 | 0 | **R6**,R10,R13 |

→ 第一版我拍的 **0.35 是错的**：它把退款线抢走 35%，而 `R6:escalate`
依赖"生鲜品类 **+** 退款诉求"这个组合，于是 R6 直接被清零 ——
`tests/test_service_api.py` 那条哨兵（`AssertionError: 这次 seed 没造出
R6:escalate 的 case，测试失去意义`）正是这么红的。
**0.20 是拐点**：R6 保住、R13 出现。

**4. 让"补偿"带得上金额。** 语料 264 条 `compensate` 里带"数字+元"的只有 25 条（9%），
不筛则绝大多数 R10 判 `need_clarification`。所以在抽取时追加谓词：

```python
                    if forced_kind == "compensate":
                        def _needs_amount(text: str, _inner=receipt_predicate) -> bool:
                            if _inner is not None and not _inner(text):
                                return False
                            return corpus.extract_amount(text) > 0
                        kind_predicate = _needs_amount
```

**5. 新增 `_force_price_drop(order)`：造数器要造出它要考的世界。**
`price_dropped` 池子在 924 笔订单里有 38 笔（4.1%），但每张工单只能关联
**该客户自己**的订单，而 400 客户平均每人 2.3 笔 —— 实测 16 条 price_protect 工单
**0 条**落得进去（`_ppdiag3.py` 的漏斗：有现价 268 → 降价 268 → 有付款时间 268 →
**15 天内仅 44** → 状态可用 **38**）。

解法不是"再多造数据"，而是**该客户名下无合格订单时就地改造一笔**
（`current_price` 设为 `amount * 0.85`、`paid_at` 回拨 3 天、
`status` 从 `pending_paid`/`cancelled` 提到 `paid`）。理由写进 docstring：

> **造数器的职责是产出能考到每个分支的场景，不是复刻真实分布** ——
> 真实分布由 `_KIND_FORCE_SHARE` 那一层管（只给 20% 工单指定诉求）。

（`paid_at` 回拨 3 天同时避开 R1 的七天窗口误伤。）

**6. 独立的辅助随机流必须按 seed 重置** —— 见缺陷 ㉛。

最终结果（`_reqkindcheck.py 400 300`）：**241 case 全通过、覆盖缺口：无、全部条件覆盖**，
其中新增条件 `R10:compensate` 6 / `R11:price_protect` 1 / `R13:change_address` 3 /
`R8:exchange` 2 / `R12:issue_invoice` 8 / `R14:urge_logistics` 11 / `R9:reship` 10。

> ⚠️ 这同样是**旧基线口径**的读数（`_request_amount_of` 改读 `tickets.request_amount` 之前）；新口径下需重跑才能作为基线引用。


**缺陷 ㉚：`decide_for_order` 读 order 上的五个字段，第六个不读。**

修完 ㉙ 的抽样之后 `R11:price_protect` **仍然是 0**。直接探规则：

```
① 直接问规则：price_protect（R11）
  current_price=None    → need_clarification  R11：未提供当前售价，无法计算降价差额
  current_price=80.0    → need_clarification  R11：未提供当前售价，无法计算降价差额
  current_price=100.0   → need_clarification  同上
```

**传了 `current_price=80.0` 仍然报"未提供"。** 第一轮我猜"`decide_for_order`
没透传这个形参"—— 猜错了，`rules.py:771` 确实透传了。

真相更精确：`decide_for_order` 里

```python
    rsc = refund_count if refund_count is not None else order.refund_count
    exc = exchange_count if exchange_count is not None else getattr(order, "exchange_count", 0)
    rsp = reship_count if reship_count is not None else getattr(order, "reship_count", 0)
    urg = last_urge_at if last_urge_at is not None else order.last_urge_at
    inv = invoice_issued if invoice_issued is not None else bool(order.invoice_issued)
```

**五个参数都有"没传就从 order 回落"，唯独 `current_price` 直接透传形参。**

后果：golden（`seed.py` 显式传 `current_price=order.current_price`）判得出退差价；
而**工具层**（`registry.py` 的 `submit_after_sales_action`，只传 `request_amount`）
永远读到 `None` → 判 `need_clarification`。
同一份输入，golden 和 agent 两处读法不一致 ——
**这条 case 天生不可赢，而报表上只显示"agent 算错了金额"。**

> ★ 这是缺陷 ㉓ 的**同形第二例**，只是方向相反：
> ㉓ 是"golden 少传了，规则退化成默认语义"，
> ㉚ 是"工具层少传了，而 golden 传了"。
> 两者的共同形状是 —— **同一个值有两处读法，而没有任何东西检查它们一致**。

修法（`rules.py`，紧接 `rsc` 那一组之后）：

```python
    cp = current_price
    if cp is None:
        _from_order = getattr(order, "current_price", None)
        cp = float(_from_order) if _from_order is not None else None
```

并把 `decide(...)` 调用里的 `current_price=current_price` 改成 `current_price=cp`。

修完后 `R11` 从 0 → 1~2 条。为什么仍然不多？因为"有现价 × 确实降价 ×
有付款时间 × 在 15 天窗口内"四条件叠加后池子只剩 38 笔，
而每张工单只能用自己的订单 —— 这个**样本量**问题由 ㉙ 的 `_force_price_drop` 兜底，
两者合起来才让 `R11` 稳定出现。


**缺陷 ㉛：辅助随机流是模块级全局，从不按 seed 重置。**

为了让指定诉求的抽样不干扰主随机流，我另开了两条流：

```python
_KIND_RNG = random.Random(0x5EED_1CE5)
_PRICE_RNG = random.Random(0x5EED_9A17)
```

问题：它们**只构造一次，永不重置**。于是 `seed_all(seed=7)` 连调两次，
第二次是从上次停下的位置继续 —— **同一个 seed 造出两份不同的数据**。

症状极具迷惑性：`tests/test_service_api.py::test_human_approval_cannot_bypass_policy_guardrail`
报

```
AssertionError: 这次 seed 没造出 R6:escalate 的 case，测试失去意义（共 37 条）
```

看上去像"测试太脆/依赖特定数据"，实际是**造数不可复现**：
前面的用例已经消耗过 `_KIND_RNG`，轮到这个测试时流已经漂了。

> ★ 这是**我自己写下的原则的违反**。
> `_PRICE_RNG` 旁边的注释明写着"造数是评测的基准，基准漂了历史读数就失去意义"——
> 而"每次从同一位置开始"正是基准的一部分。
> **只做独立流不做重置，等于只做了那件事的一半**：
> 独立流解决的是"新字段不扰动旧数据"，
> 重置解决的是"同一个 seed 永远给同一份数据"。两件都要做。

修法（`db/seed.py`）：

```python
def _reseed_aux_rngs(seed: int) -> None:
    """两条辅助流必须和主 RNG 一起回到起点。

    派生种子各自 XOR 不同的常量：不同步的话它们会走成同一条轨道，
    于是"独立流"实际上还是在互相耦合。
    """
    _KIND_RNG.seed((seed * 0x9E3779B1) ^ 0x5EED_1CE5)
    _PRICE_RNG.seed((seed * 0x85EBCA77) ^ 0x5EED_9A17)
```

并在 `seed_all` 开头 `rng = random.Random(seed)` 之后立刻调用。

> **附带教训（这个项目里犯过四次）**：加字段时多调一次 `rng.random()`，
> **整条随机流会位移** —— 同一个 seed 造出的数据集全变，历史报表不再可复现。
> 这就是为什么 ㉙ 的抽样和 ㉕ 的现价都必须用**独立流**。
> 加"只是多一个字段"的东西时，先问一句：**它会不会消耗主随机流？**

---

**缺陷 ㉟：判据的依据是一个由被审对象自己书写的字段（两半，一个根因）**

**现象**：200 case 真模型那批，`single` 4 条 / `multi` 20 条 run 的
`verifier_passed=True` 而 `observed_action='none'`，且 diffs 明确要求写操作
（`ticket.status` 要离开 `open`，或 `after_sales` 要有记录）。看起来是
"agent 什么都没做却通过了验收"。

**定位（六步，每一步只答一问，判据都可复现）**：

| 脚本 | 量什么 | 结论 |
|---|---|---|
| `_vnone.py` | 怎么定义"可疑 run" | single 4 / multi 20 条 B 类（另有 56 条 A 类是 `ambiguous`，放行正确） |
| `_vwhy5.py` | golden 到底要求什么 | **24/24 条都要求离开 open 或要求写** —— 只看 diffs 期望侧，不重建数据 |
| `_vwhy6.py` | `_summarize_action` 认哪些工具 | 只认 4 个名字；`submit_after_sales_action` **不在列**；multi 有 11 条 `write_calls=1` 而 `after_sales: 实际 []` |
| `_vwhy8.py` | 那一次写到底是什么 | 审计日志里 `submit_after_sales_action.urge ok=True args={…'action_id': 1}`，而标签是 `resolve`（来自 close_ticket） |
| `_vwhy10.py` | 重建是否确定 | `seed_all(1000,11)` + `build_eval_cases(200,7)` 连跑两次**逐位相同** |
| `_vwhy11.py` | 同批 case 上 scripted 基线 | 6 条全部办成、`verifier=True` → **verifier 判据本身没放过它们** |

**第一半（报表汇总）**：七个售后动作（赔付/补发/换货/价保/开票/改址/催单）
走的是**同一个** `submit_after_sales_action`，只有 `args.kind` 不同；而
`_summarize_action` 按工具名映射，只认退款线那四个。后果是 agent 真写了
（审计与库里都有记录），报表写 `observed_action='none'` —— 排查时会去查
agent，而真相是汇总函数少认一个工具。200 case 实测 11 条
`write_calls=1` 而 `after_sales: 实际 []`。

**第二半（verifier 判据）**：

```python
needs_clarification = basis == "ambiguous" or bool(ticket_snap.get("pending_question"))
```

`basis` 来自 `classify_order_link` 纯函数，是**输入侧**事实，agent 改不了；
`pending_question` 是 agent 用 `ask_customer` **写**进去的，属于**被审对象
自己的产出**。缺陷 ⑲ 修的是"机器断言不能建立在被审对象的自我陈述上"，
#2 把依据从散文换成了结构化字段 —— 但字段仍由被审对象书写：
agent 问一句（哪怕问的是一个 golden 并不需要的问题）就给自己开一张免检券。
**同一个病根换了件衣服。**

**修法**：

1. `_AFTER_SALES_KIND_LABEL` 提到模块级，八个 kind 一张表；
   `_summarize_action` 与 `_expected_action` 共用它 —— 两处一张表，
   加 kind 时不会只改一边（缺陷 ㉓"参数传进不去 ≡ 规则没写"的同形）。
   `ok=False` 的提交仍然不算动作（缺陷 ⑫ 的修法不丢）。
2. `verifier_node(state, *, should_clarify=None)`：`pending_question` 只在
   **这条 case 的 golden 要求先澄清**时才是有效依据。可复现的判据在 case 层
   （`_case_should_clarify`：`condition` 以 `need_clarification` 结尾，或
   `turn2_state` 非空），agent 没有任何路径能改它。`asked` 但 golden 不要求
   → 单独记一条可读的 problem，不是只有 `passed=False`。
3. 用**闭包**把 `should_clarify` 绑进 verifier 节点，不用全局变量 ——
   全局在本项目已经惹过 ㉛（辅助随机流不按 seed 重置）和 ⑮（`.env` 写进
   `os.environ` 污染进程）两次祸，不再添第三种。

**为什么"问过客户"不能替代"办成"**：政策里有六处会返回
`need_clarification`，那些 case 第一轮的正解确实是"先问清"。但第二轮客户
答复到达时 `apply_customer_reply` 会把缺的字段补上、规则重算，同一个 case
必须能办成 —— 所以"问过"只解释第一轮为什么 open，不解释终态为什么还 open。

**回归**：`tests/test_defect35.py` 26 条（八个 kind 全覆盖 + 阴性对照 +
`ok=False` 不算动作 + 两半同一张表 + case 层判据参数化）；
`test_clarification_loop.py` 的参数化判据测试从"读源码断字面量"升级为
"直接调 `verifier_node` 喂五种组合"（原实现把字面量钉死，㉟ 一收紧就红 ——
红得对，但它测的是字面不是语义）。全量 **263 passed, 1 skipped**。



### 4.3 其他工程细节

| # | 难点 | 解法 |
|---|---|---|
| 1 | 工具幂等 | 每个写工具带 `idempotency_key`（默认 md5(tenant:ticket:order:amount)），DB 唯一约束兜底；捕获 `IntegrityError` 返回 `duplicate: True` |
| 2 | 只读工具重复调用 | 按角色隔离的去重缓存，命中时**回放真实结果**而非返回空壳 |
| 3 | 并发串租户 | 工具上下文用 `contextvars.ContextVar` 而非模块级 dict —— 否则并发评测会"给 A 租户的工单办了 B 租户的单" |
| 4 | 写工具不自动重试 | 只读工具带 `tenacity` 重试，写工具重试会导致重复副作用，靠幂等键而非重试保证正确 |
| 5 | 成本熔断要有兜底动作 | 熔断不是"什么都不做"：必须留可被人工接手的 `pending_human` 终态 + 审计记录 |
| 6 | 预算记账的稳健性 | 用 `BaseCallbackHandler.on_llm_end` 而非 middleware hook —— hook 名在小版本间变过，改坏了会让熔断**静默失效** |

### 4.4 语料层：从 9 条手写模板到 11,079 条真实话术

#### 为什么要换

初版的工单正文来自 `seed.py` 里 9 条手写模板（L1/L2/L3 各 3 条）。它有一个致命的副作用：
**9 条模板被 588 张工单反复抽中，平均每条出现 65 次**。评测集号称 588 条，
实际语言多样性只有 9 种 —— 报出来的准确率度量的是"agent 认不认得这 9 句话"，
不是它能不能读懂中文售后客服。

换语料不是"找个更大的数据集塞进去"，它牵出三个必须先解决的问题：
语料从哪来、怎么过滤、**以及"客户怎么讲话"这个维度到底该怎么定义**。
第三个问题最后反过来改了造数逻辑，是这一节真正的内容。

#### 语料来源（`data/raw/`，只读不改）

| 文件 | 条数 | 来源 | 角色 |
|---|---|---|---|
| `jddc_aftersale.jsonl` | 10,217 | JDDC 京东客服对话（同一批数据的**两种切法**合并而来，见下） | **工单正文主输入** |
| `ecom_aftersale.jsonl` | 862 | 电商售后语料（含大量含糊态表达） | 补充含糊态 |
| `www2025_intent.json` | 1,000 | 电商客服意图数据 | **不进工单正文**，见下 |

★ `jddc_aftersale.jsonl` 的 10,217 条来自**两路来源**，不是一次过滤的结果：

| 来源 | 原始行数 | 形状 | 过闸门后 |
|---|---|---|---|
| `ms_ecom_train_FULL.jsonl`（上游切好的单轮对） | 226,600 | `{"messages":[{"role":"user"...}]}` | 6,226 |
| `jddc_multi_train_FULL.txt`（**原始上下文版**） | 500,000 | `客户到该轮说过的全部话 \t 对这一轮的回复` | 10,009 |
| 合并去重 | — | 139,432 条唯一话术 | **10,217** |

第二路是同一批数据的**未切分版本**，文件名 `context_response.txt` 说的就是它的结构：
左边是**上下文**（客户到该轮为止说过的全部话，轮次间用 `<s>` 分隔），右边只是对**最后一句**的回复。
所以多轮行**只能取第一句**当工单正文（会话开场白，天然自足）——同一行那个回复属于最后一句，
配给第一句是错位的（实测印证：`我拍了一组我在拍一组可以嘛<s>那就等你们处理喽 \t 好的亲退了`，
"好的亲退了"回的是"等你们处理"）。

★ **两路来源的取舍是量化过的，不是"多一份就多抓一份"**：
`_multiprobe.py` 量出多轮源额外提供 53,252 条现有语料里没有的话术，过闸门后**净增 4,681 条**
（比只用单轮源涨 77%）；`_multiquality.py` 分档看这 4,681 条，**97.9% 句首自足**
（"你好货收到了不过有一包破损了""我还没收到能帮我查查吗"），剩下 2.1% 以代词连词开头
（"那我退款了到时你同意一下"）也都带明确诉求词；`_multipair.py` 量回复配对 ——
其中 43.8% 来自单轮行、回复天然配对，56.2% 来自多轮行、**回查不到配对回复**（能在单轮源里
回查到的比例是 **0.0%**，说明"回查"这条出路根本不存在）。
故多轮行的 `bot` 字段一律**留空**：留空不是偷懒，是**不让一个填错的字段冒充正确答案**
（该字段全项目只写不读，见 `grep '"bot"'`，但仍要配对正确 —— 一个填错的字段迟早会被当成对的用）。

★ 多轮源是**空格分词**过的（`买 二份 有没有 少点 呀`），必须先 `.replace(" ", "")` 才是自然中文。

★ `www2025_intent.json` 一开始是打算用来做工单正文的，读了才发现**它的 46 个标签
全是页面截图分类**（"商品详情页截图""退货页面""物流跟踪页面"），其余是商品属性咨询
（"商品材质""能否调光""适用季节"）—— 全是**售前**范畴，且判定依赖图像输入。
它做不了意图层评测，也不该进工单正文：本系统做的是**售后**工单处置。
留着它是作为反例记录：**数据集的名字和它实际标注的东西经常不是一回事，
不逐条读标签分布就会拿它当宝用。**

#### 预处理：为什么拆成两步

原始 `ms_ecom_train_FULL.jsonl` 有 99.8 MB / 226,600 行，而 `corpus.load_corpus()`
在启动路径上被同步调用 —— 直接读它每次造数多花十几秒，且原始文件不该进仓库。
故拆两段：

```
tools/build_corpus_jddc.py（离线跑一次）
  → data/raw/jddc_aftersale.jsonl（1.6 MB）
  → corpus.load_corpus()（运行时只读小文件）
```

好处是**过滤规则可复核**：脚本把每条被丢掉的句子连同丢弃原因都打印出来。
两路来源合并去重 139,432 条 → 五道闸门后 **净可用 10,217 条**（其中 8,521 条带客服回复）：

| 丢弃原因 | 条数 | 说明 |
|---|---|---|
| `太短` | 67,849 | 长度 < 10，多为"嗯""好的"这类承接语 |
| `无事由` | 50,747 | 没有任何售后事由词（九类事由见 `CAUSES`） |
| `售前咨询` | 8,818 | "发什么快递""哪个质量好"—— 问了但问的是售前 |
| `回指上文` | 1,763 | 以指代词开头（"对的那个消息我发错了"），脱离上下文不成立 |
| `太长` | 38 | 长度 > 120，已是多话题混杂而非"一句话说清诉求" |

★ **`行结构是 `{"messages": [...]}``（踩过的坑）**：JDDC 的每行不是 `{"user": ..., "bot": ...}`
而是 `{"messages": [{"role": "user"|"assistant", "content": ...}]}`。
猜字段名的结果是"客户话术去重后 0 条"——**不先打印一行看结构，后面所有统计都是零。**

★ **换语料后先做体检，再看指标**。`_corpuscheck.py` 五项：① 繁体污染
（会被 `normalize` 改变的行 **24 条 / 0.23%**，表外繁体 `normalize` 不动它的 **1 条 / 0.01%**，
与 §4.5 结论一致 —— 判据靠字形归一化看穿，污染面极小）；② 风格分布
（`explicit 34.6% / defect 1.7% / vague 63.7%`，与旧语料 35.1% / 2.6% / 62.4% 相比
**最多只动 1.3 个点**）；③ 最短 20 条（全部 ≥10 字且都是真句，无残句）；
④ 去重键生效（重复正文 **0 种**）；⑤ `corpus.pick` 可抽性（L1/L2 各抽 400 次全部命中）。
★ **为什么要先体检**：语料换了之后指标会变，但"变了"本身不说明是坏事还是好事 ——
若是新增的几千条几乎全是 `vague`，那指标下降其实是评测集被拉偏，而不是 agent 变差。
这五项里前两项就是专门用来**把"数据变了"和"能力变了"分开**的。

#### ★ 核心问题：`vague` 到底是什么意思

语料接进来之后，`_REQUEST_RE`（判"客户有没有说清诉求"的正则）在真实语料上**大面积失配**：

```
合成语料（862 条）    vague  60.9%
真实语料（当时 6,361 条） vague  75.9%   ← 而 defect 从 14.5% 崩到 0.5%
```

一开始的判断是"词典不够全"，于是扩了三轮：B 档加服务动作词（`补发|催|帮我查|开发票|改地址`…），
C 档再加要结果词（`赔偿|给个说法|怎么处理`…）。**每一轮抽样都能找出新的漏词**：

```
A 现状       vague 75.9%
B +服务动作   vague 66.1%
C +要结果     vague 65.2%     ← 扩了 40 多个词，只挪动 10 个点
```

漏词一个比一个意外：`换成`（"可以换吗换成芒果干行吗"不在退换词典里）、
`掉屑`（"上次买的怎么掉屑"不在瑕疵词典里）、`啥时侯`（错别字，
"货到底啥时侯能到"）、`崔`（"我的快递能崔下派送么"）。

★ **这时才意识到问题不在词典长度，而在方法本身**：靠"我看一眼抽样、补几个词"
这个循环**没有收敛判据** —— 我永远不知道还差多少，抽样永远能找出新漏词。

于是换一个可测量的办法：**用客服的真实回复当弱标签**。
客服答里出现"帮您催/我帮您查/给您申请"这类**动作承诺**，
就说明客服把这句话读成了"要办的事"，那在人读来客户大概率说清了要什么。
于是 `漏判率 ≈ P(客服去办了 | 我判 vague)`：

```
我判 explicit   → 客服去办了  34.7%
我判 defect     → 客服去办了  29.8%
我判 vague      → 客服去办了  27.3%   ← 448 条
```

抽样读那 448 条原文，结论把整件事翻了过来：

| 客户原文 | 客服回复 | 客户说清了吗 |
|---|---|---|
| 我的快递显示已签收可是我没有拿到啊 | `我联系下快递哦` | **非常清楚** |
| 怎么还不送货着急用呢 | `这边给亲催下呢` | **非常清楚** |
| 怎么会用邮政的等几天了都本以为今天到的 | `帮您加急下哈` | **非常清楚** |
| 到嘉兴没到杭州啊你们是不是拦截了 | `这边明天帮您问下哈` | **非常清楚** |

**这些客户没有任何一个"没说清"。他们说得清清楚楚 —— 只是要的不是退款退货，而是查件、催件。**

于是 `vague` 的正确定义浮出来了：

> **`vague` 不是「客户没说清」，而是「客户要的动作不在本系统能力内」。**

真实售后里占比最大的正是这类：物流类占语料 **45.2%**，
而本系统的动作集只有 **退款 / 驳回 / 拦截 / 转人工** 四种
（`rules.decide()` 的全部 return 分支只产出这四个，
`need_clarification` 还是造数侧独立指定的，不是 `decide()` 能返回的）。
系统没有查件、催件、补发、开票、改址任何一种动作。

★ **由此得出一个反直觉的结论：正确修法不是继续扩词典，而是把 `explicit` 的定义收紧。**
如果把"帮我查快递"判成 `explicit`，工单正文就写着一个系统办不到的动作，
而 golden 由 `rules.decide()` 按订单数据推出（比如 R1 退款）——正文与答案语义打架。
所以 `_REQUEST_RE` 只认**动作集内**的动作：

```python
_REQUEST_RE = re.compile(
    r"退款|退货|退钱|退换|换货|换新|换一个|换个|能退|要退|想退|申请退|帮我退|退一下|"
    r"拒收|不要了|不想要|退掉|拦截|"
    r"(?<!寄)退回|寄回(?!老家|家|家里|地址|公司|单位|学校|老家)"
)
```

最后一行两个负向断言是实测逼出来的：`那我买8包寄回老家邮政是吗` —— 这里的"寄回"
是寄**往**老家，不是退货寄回。判它 `explicit` 会让一条纯询问配上退款正解。

#### ★ 由这个定义反推出来的第二个修复：造数顺序倒置

`vague` 的定义改了之后，暴露了一个更隐蔽的问题：
**`level`（难度层）被决定了两次，而两次结论不同。**

- 造数侧：`level = rng.choices(["L1","L2","L3"], weights=[40,45,15])` —— **随机抽的**
- 评测侧：`build_eval_cases` 由订单状态**重推一遍**（`no_order`/`ambiguous`→L3，
  在途→L2，其余→L1）

同一个量被决定两次，且第一次是"随机的预言"。更糟的是风格映射当时是**有序回退**：
`L2 → (defect, explicit)`，而 `defect` 池只有 172 条、L1+L2 又占样本 82% ——
于是那 172 条瑕疵话术被反复抽中。实测在报表上：

```
defect  评测集占比 43.0%   语料占比 2.6%    ← 放大 18.7 倍
vague   评测集占比 17.2%   语料占比 62.4%
```

修法是**把顺序倒过来**：先定订单 → 由订单推难度层 → 再按层挑话术。

```python
if refunded and rng.random() < 0.35:
    linked_order = rng.choice(refunded)      # 重复退款场景 → R5
elif fresh and rng.random() < 0.6:
    linked_order = rng.choice(fresh)         # 新近签收，R1 主路径
elif orders and rng.random() < 0.25:
    linked_order = None                      # 需要澄清 → ambiguous/sole_order
else:
    linked_order = rng.choice(orders) if orders else None
resolved, basis = classify_order_link(linked_order, orders)
level = classify_level(resolved, basis)      # ★ 判据与评测侧是同一个函数
```

配套改了两处：

1. **判定内核抽成纯函数**（`snapshot.classify_order_link` / `classify_level`）——
   造数侧和评测侧调**同一个**函数，`level` 从此只有一个来源。
   这也顺手解决了另一个问题：造数侧必须在工单**落库之前**就知道它将来落在哪一层，
   所以要有一个不依赖 repo 的纯函数版本。
2. **`_LEVEL_TO_STYLES` 从「有序回退」改成「允许集合」**，`pick` 改成在并集内均匀抽 ——
   并集里各风格的条数占比自然就是它在语料里的权重，不需要人为配权。
   ★ `L1` 与 `L2` 的取值现在**故意完全相同**：风格说的是"客户怎么讲话"，
   而 L1/L2 的区别在订单状态（规则直判 vs 要算时效），与客户怎么讲话无关。
   给它们配不同的风格集合，等于把一个订单维度的区分伪装成语言维度的区分 ——
   上一版正是这么错的。

**修复后实测**（千级重造，`_stylebias.py`）—— 下表是**换语料前**（6,638 条）的数字：

```
风格       评测集占比   语料占比   放大倍数
explicit     73.2%      35.1%      2.1x
defect        4.4%       2.6%      1.7x
vague        22.4%      62.4%      0.4x
```

放大倍数从 **18.7x 降到 2.1x**。剩下的差距是**设计使然的取舍**，已在代码注释里写明：
真实语料里在途订单的客户十有八九说的是"什么时候能到/帮我催下"（判为 vague），
而 L2 现在不收 vague。取舍是**公平优先** —— 那些话术配给 L2 会让正解（R3 拦截，即取消订单）
与客户所问（催一催）**方向相反**，考的不是能力而是运气。

★ **接入多轮源之后复跑（11,079 条，`size 561`；与报表同一个库）**：

```
风格       评测集占比   语料占比   放大倍数
explicit     75.9%      34.6%      2.2x
defect        3.6%       1.7%      2.0x
vague        20.5%      63.7%      0.3x
```

（这三行与报表 §"按话术风格"里的 `explicit 1278 (75.9%) / vague 345 (20.5%) / defect 60 (3.6%)`
**逐位相同** —— 两份数字出自同一批 case，不是两次各量各的。）

**放大倍数几乎没动**（2.1x → 2.2x）。这一点比数字本身重要：它说明那次修复是**结构性的**
（把"先随机定层、再按层挑话术"改成"先定订单、由订单推层"），而不是对着某一批语料
调参数调出来的 —— 若它是调出来的，换一批语料就该打回原形。
**真正修好的东西不随数据更换而复发。**

#### 相容性约束：正文不能与订单状态矛盾

语料是**池子**，订单是**已定的**。把两句独立选出来配在一起，就会出现
"客户说收到的衣服有瑕疵"配一笔**还没发货**的订单 —— 评测集自己制造了一个不可能的场景，
而 agent 在这种场景下无论怎么做都很难说错。

故新增 `corpus.presupposes_receipt(text) -> bool`：这句话是否**假定货已到手**。
造数时按订单状态筛话术：

```python
needs_receipt_ok = resolved is None or resolved.signed_at is not None
predicate = None if needs_receipt_ok else (lambda text: not corpus.presupposes_receipt(text))
```

判据本身有两条容易判反的地方，都用真实语料原句钉进了测试：

- **否定语境优先**：`显示已签收但我没收到` 里既有"签收"也有"没收到" ——
  必须先查"未到手"词典，否则把意思判反。
- ★ **裸的 `到了` 是危险词**：它在中文里既表示"货已抵达"
  （`快递到了我不想要了可以拒收吗`），也表示"款项到了/时间到了"。
  这句实测正是 **R3 拦截**的样本（配在途订单走取消流程），
  把它判成"已到手"会让拦截类工单配不上在途订单 —— **一个词毁掉一条规则的可测性**。
  正确修法是语义层面的：**"拒收/不签收"本身就意味着货没到手**，该进"未到手"词典。
  这个回归是测试抓出来的（`test_presupposes_receipt_history_and_transit`），
  注释里写明取舍原则：**宁可漏判，不可把意思判反**。

**审计结果**（`_compatprobe.py`，用**两把独立的尺子**；★ 在**报表用的同一个库**上跑的，561 张工单）：

```
case 数: 561

OK                                    531   94.7%
B 正文说在途/催件，订单已签收              24    4.3%   ← 客户既催件又要退款，正解仍是 R1 退款，非矛盾
A 正文说已到手，订单未签收                  6    1.1%   ← 子串误判（"不签收"含"收"、错别字"牵收"）

宽尺子（独立正则）合计冲突: 30/561 = 5.3%
生产判据（presupposes_receipt）违例: 0/561 = 0.0%
```

★ **审计库与报表库必须是同一个**：这份审计早期是在另一个种子（seed=42，583 工单）造出的库上跑的，
数字是 30/589。库里换了种子，审计数字就会跟着变 —— 而它会**看起来仍然很正常**。
所以两份审计（`_compatprobe.py` / `_stylebias.py`）都改为在报表的库上重跑，
README 引用的也是这一套数字。**一个凭据只有在"它量的是哪批数据"被写清楚时才对得上。**

★ **为什么要有两把尺子**：宽尺子是**故意写得更松**的独立正则，
它的用途是"如果两把尺子的结论不一致，差额就是线索"。
实测这个差额真的指向了 4 条生产判据的漏网之鱼
（`收到有质量问题给退换吗` 只有裸的"收到"能命中、`里面每个都有点状线` "里面"没进词典、
`寄回来`≠`寄来`、`碎片`≠`碎了`）—— 逐条读掉之后补进词典。
**两把尺子的差额本身比任何一把的绝对值都更有信息量。**

#### 已知局限（诚实记录）

1. **JDDC 是真实语料，但门禁只能保证"这句话有一个售后事由且能独立成立"**，
   不能保证它准确描述了订单实情 —— 语言来自语料，正误来自 `rules.decide()`，
   二者的连接点是"同一笔订单"，不是"同一段事实"。
2. **84% 的句子用泛称**（"商品""东西"）而非具体品类，故语料里的品类信息
   无法与订单类目交叉验证 —— 好处是也不会出现"衣服"挂在数码订单上的假冲突。
3. **语料只含客户单句，不含处置结论**，所以 golden 全部由规则推导，
   `rule_ceiling` 的问题依然存在（见 §7.4）。
4. **去重后 11,079 条**，造 1000 客户（**561 工单**）时复用倍数约 1.0x ——
   比 9 条模板（平均 65 次）好两个数量级，但仍不是"无限语言"。
5. **语料的事由分布与本系统动作集不重合**（物流类 45.2%，而系统没有查件动作）。
   这不是缺陷，恰恰是评测集该有的样子 —— 它让"系统办不到的事"成为一个
   **被显式度量**的类别（`vague`，占评测集 20.5%），而不是被悄悄从数据里抹掉。
6. **多轮源贡献的 4,681 条只有它的第一句被用上**，同一行那个回复（属于最后一句）
   被丢弃，故这批话术**没有配对回复**（`bot` 留空）。这不影响运行时（该字段无消费者），
   但会让"用客服回复当弱标签"这类离线分析在 56.2% 的语料上失效。

#### 语料指纹进报表

报表的运行配置里带上了语料指纹：

```
**运行配置**：arm=multi；kind=scripted；size=561；seed=7；
corpus_sha256=1bb070b09c354582；corpus_unique=11079；corpus_ok=True
```

★ **为什么必须带 sha256 而不只是文件名**：指标只有在"读的是同一批字节"时才可比。
只写 `jddc_aftersale.jsonl` 不足以固定这件事 —— 文件会被替换、会被重新下载、
也可能被手工改过。这与缺陷 ⑨（基准漂移）是**同一类问题，只是漂移源从时钟换成了数据文件**：
时钟漂移让同一个故障跑出 2/1/2，数据漂移让准确率悄悄变一个数。
故 `cli._run_meta()` 把 sha256 前 16 位与唯一条数写进 meta，换语料就能在 diff 里看见。

★ **但要知道这一格是"合并指纹"**（`_fpcheck.py` 量过）：`1bb070b09c354582` 由
`ecom_aftersale.jsonl (b84df0ac…)` 与 `jddc_aftersale.jsonl (f9100d09…)` 两个文件
**按名排序拼接后再哈希**（手工复算逐位吻合）。带来的后果是 ——
**往 `data/raw/` 里多放一个进指纹的 `.jsonl`，指纹就会变，哪怕它根本不参与造数**。
对比两份报表时要知道这一点：指纹变了不等于造数读的字节变了。
反过来，溯源表 `CORPUS_SOURCES` 里记的是**单文件** sha256（`f9100d09cbfbda14…`），
两个数不是一回事，别拿其中一个去核另一个。

#### 这一层的测试

`corpus.py` 原本**零单测覆盖**，而它是评测集的一个维度 —— 规则会随语料换代失配。
实测发生过一次：`_REQUEST_RE` 只认退/换家族，真实语料里 51.5% 的物流类诉求被判成"没说清"，
而**那次失配没让任何测试变红，因为这一层没有测试**。

故新建 `tests/test_corpus.py`（24 个测试），断言两类东西：

- **契约**：`classify_style` 是纯函数（同句 50 次同结果）、取值在声明集合内、
  `level_to_styles` 未知层退回全部、`load_corpus` 跨文件去重且坏行跳过、`pick` 筛空返回 None。
- **判据边界**：用**真实语料原句**钉死判定（12 条 `STYLE_CASES` + 3 条 `presupposes_receipt`）。
  换语料时这些断言失效是**有意为之** —— 它们钉的是"当时的判据在当时的语料上给出什么结论"，
  语料换了就该重新审一遍，而不是让测试替我把语义错误固化下来。

★ 测试里两个踩过的坑：① `load_corpus` 带 `lru_cache`，测试前必须 `cache_clear()`，
否则测的是旧语料；② "坏行"要用**真的截断行**（`'{"scene": "退款", "user": "截断的'`），
第一版写成 `json.dumps("不是合法 json")` —— 那反而是合法 JSON，断言失败的原因根本不在被测代码上。


### 4.5 跨域效度检验：拿一份繁体数据集来验判据

语料从 9 条手写模板换成上万条真实话术之后，一个新问题浮出来了：
**这套判据是不是只在自家语料上成立？** 自家的语料再真实，也是从同一批数据源里筛出来的，
判据的词典也正是在这批数据上反复调出来的 —— 换句话说，**判据和语料是一起长大的，
它们之间的默契不能当证据**。

于是找了一份完全独立的第三方数据集来验：`hf_zhtw_ticket_train_FULL.jsonl`，
2,497 条，繁体中文（台湾用语），6 个类目且高度均衡（`客訴投訴 439 / 帳號系統問題 438 /
商品諮詢 433 / 退換貨申請 399 / 帳務爭議 396 / 物流查詢 392`）。

#### 第一次跑就发现判据被判反了

```
输入：我的包裹已經到超商但還沒收到通知
presupposes_receipt → True        ← 错。这句的意思恰恰是"还没到手"
```

原因：判据词典**全是简体字面量**。`還沒` 不在任何一条词典里，
而 `收到` 命中了 `_POST_RECEIPT_RE` —— 一句"还没收到"因此被判成"已经收到"。

★ 这不是精度问题，是**方向反了**。精度问题会让数字小一点，
方向问题会让系统对着一个货还没到的客户说"您的退货已受理"。
而它在自家简体语料上**永远不会暴露** —— 因为简体语料里没有"還沒"这个字形。

量化：2,497 条里路由结论不同 **321 条（12.9%）**、
`presupposes_receipt` 判反 **159 条（6.4%）**。

#### 修法：繁→简归一化，只放在判据入口

#### ★ 但先要解决一个更隐蔽的问题：怎么证明这张表是够用的？

第一版归一化表是**手写的** 84 对字，覆盖率判据是"表内还剩几个繁体字没转" ——
**这是循环论证**：它用表里的键定义"什么是繁体"，又用同一张表定义"转换是否成功"。
这样的表**永远能自证 100% 覆盖**，无论它漏了多少字。

要找一把**独立**的尺子。试了两把：

| 尺子 | 结论 |
|---|---|
| 本项目简体语料的字符集（1,728 个汉字） | **过严，弃用**。它把「简体里恰好没出现的常用字」（`鮮/帳/機/電/讓/運`）也算成残留，于是永远归零不了 |
| **Windows NLS**（`LCMapStringW` + `LCMAP_SIMPLIFIED_CHINESE`） | ★ **可用，是真的独立** —— 微软自己的映射表，与本项目的词典毫无关系 |

#### NLS 反过来推翻了两个假设

拿到独立尺子之后，先量的不是"表漏了什么"，而是**尺子本身是什么**。两个原本笃定的判断都被推翻：

**假设一：JDDC 语料是纯简体。** → 实测（当时 6,638 条）里 **22 条**过 NLS 会变。
它们**本来就混着繁体字**（`貨`/`妳`/`什麽`/`還沒`/`剛`/`錢`），是 NLS 在**修正**它们，
不是副作用。但这个数字还有个更重要的用途 —— 见下面「诚实的那个数字」。

**假设二：`後/於/乾/牠` 属于"一对多保守项"**（简化字 `后/于/干/它` 在 Big5 里也存在，
NLS 因此不敢合并）。→ **实测推翻**：这四对**双向都不变**（`後→後`、`后→后`），
说明 NLS 的表里**根本没有这几对字 —— 它是残缺的，不是保守的**。
换 locale 也无效（`zh-CN 0x0804` / `zh-TW 0x0404` / `zh-HK 0x0C04` / `zh-SG 0x1004`
对 `後於乾牠還貨籤質` 都给出同一结果）。

把 208 个"NLS 单字也不改"的字重新分类：**207 个是繁简同形**（本就无需处理），
真正的保守项只有 **1 个**（`痴`，NLS 认为它属于 `癡`）。

★ 教训：**先验工具，再用工具。** 如果直接拿 NLS 当"完备的正确答案"，
上面这两个假设会让整张表多出七个错误的自我怀疑。

#### ★ 决定性验证：把判据词典拆成单字，逐字问 NLS

真正要回答的问题不是"NLS 干不干净"，而是"**判据要用的字，表认不认得**"。
故 `_nlsdict.py` 把四条正则的词典**拆成单字**，逐字做繁→简 + 简→繁往返：

```
四条正则的汉字并集: 110 个
  繁简同形      : 78 个
  可转往返成功  : 32 个
  ★ 转不回来    : 0 个

逐条正则：_REQUEST_RE 词条 27/异形 12/失配 0；_DEFECT_RE 17/10/0；
          _PRE_RECEIPT_RE 36/25/0；_POST_RECEIPT_RE 40/21/0
```

→ **NLS 覆盖了本项目判据词典的全部异形字，不需要人工补充表。**
`_gennorm.py` 由这 110 个汉字生成 **32 对**映射（`來→来`/`個→个`/`貨→货`/`質→质`/
`錢→钱`/`還→还`/`開→开`/`簽→签`/`損→损`/`攔→拦`/`臟→脏` …），固化成 `db/textnorm.py`。

#### 两条设计决定

**(1) 表是生成的不是手写的，且固化成纯 Python 数据，不在运行时调 NLS。**
理由有两条：① 正确性来源外移了 —— 表错了是微软映射表的错，一个有独立来源的错误，
比一个自己拍的字表好查；② **NLS 是 Windows 专有的**，判据若运行时依赖它，
同一段文本在 Windows 和 Linux 上会得出不同结论 —— 对一个"判据必须可复现"的项目
不可接受。固化之后任何平台行为一致。

**(2) 归一化的职责窄到只有一件事：让判据看穿字形。**
docstring 里明确写了「**不要用本函数清洗数据**」。语料本身应保持原始字节
（`load_corpus()` 的 `sha256` 才有意义），归一化发生在判据内部，不外泄。
这也是本节标题里"'诚实'"的由来：13 条 vs 22 条的差额**不是漏，是故意的** ——
本表只收判据词典里出现过的 32 对字，追求"把语料转干净"是另一个目标，本模块不做那件事。

接入点有三处，改的**全是判据入口，一行语料都没动**：
`presupposes_receipt`（函数体首行 `text = normalize(text)`）、
`has_explicit_request`、`classify_style`（先归一化一次再查两条正则，
★ 不再调 `has_explicit_request` —— 避免同一句话被转两遍，
也避免将来只有一条判据加了归一化、导致两条看到不同字形）。

#### ★ 静默失效守卫：把 docstring 里的提醒变成会变红的断言

`textnorm.py` 的已知边界是"NLS 本身有漏 `後/於/乾/牠/癡`，本表继承了它"。
这条验证是**一次性**的：今天判据词典里没有 `后/于/干/它`，
所以它们不在这里不影响任何判定；**但如果将来有人把这几个字加进判据词典，本表会静默失效** ——
不会报错，只是判据又开始在繁体上判反。

所以 `tests/test_textnorm.py` 里有一条 `test_dict_chars_are_covered_by_table`：
内置 `NLS_GAPS = {"后":"後","于":"於","干":"乾","它":"牠","痴":"癡"}`，
把四条正则里出现的汉字全取出，**若有字落进 `NLS_GAPS` 就 red**，
报错文案直接写明修法（给 `_T2S` 手工补这几对并重跑 `_nlsdict.py`）。

★ 这条测试的意义超出它自己：**一份文档里的注意事项，就是一条没人执行的测试。**
写成断言之后，代价是测试多 30 行，收益是"将来某一天"被提前到了"提交的那一刻"。

测试数因此从 95 涨到 **104 passed**（9 条新增：契约组 3 条 + 判据边界组 4 条 +
守卫 1 条 + 语料字节未被清洗 1 条）。

#### 跨域检验跑出来的数字

`_crosscheck.py` 复用**生产模块**的 `textnorm`（不是平行实现 ——
平行实现有个隐蔽的坏处：它跑通了只能说明这份实现是自洽的，
**说明不了生产路径上的那次归一化是对的**），跑出四组结果：

```
① 覆盖情况
   尺子 A（自检，会循环）: 归一化前 2461 (98.6%) → 归一化后 0 (0.0%)
   尺子 B（独立，过严）  : 条数 2497 → 2497（★ 一点没降）
     残留汉字总字数      : 26700 → 24123（降 9.7%）
     top12: 麼1192/樣1010/訂742/結611/處560/會528/現479/確443/覺381/欸344/帳328/認318

② 回归验证（同一条文本的繁简两种字形，判据结论是否相同）
   路由结论不同         : 0 条 (0.0%)
   presupposes_receipt  : 0 条 (0.0%)      ← 修复前是 321 条 /12.9% 与 159 条 /6.4%
   → 判据已与字形无关：生产路径上的 normalize 覆盖了全部入口

②-b 基线（绕过归一化直接查原始正则）
   路由结论不同 491 (19.7%) / receipt 判反 378 (15.1%)
   ★ 这里的非 0 与②段的 0 含义**相反**：②是"修复生效"，②-b 是"没有修复就会错这么多"

③ 路由分布：need_clarify 1658 (66.4%) / in_action_set 595 (23.8%) / out_of_scope 244 (9.8%)
   类目 × 路由：客訴投訴 60/320/59；帳號系統 21/370/47；商品諮詢 19/395/19；
                退換貨申請 344/25/30；帳務爭議 109/265/22；物流查詢 42/283/67
```

★ 尺子 B 的**条数一栏一点没降**（2497 → 2497），这不是 bug，是这把尺子的性质：
它问的是"这条句子里有没有语料没用过的字"，而这 2,497 条里**每一条都有** ——
top12 的 `麼/樣/訂/結/處/會/現/確/覺/帳/認` 全都是**真繁体字**，
只是本表刻意没收（不在判据词典里）。所以这把尺子只能看**总字数**的下降，不能看条数。

★ 报表解读文字必须跟着数据走。这两处解读（尺子 B 的条数、下面 ⑤ 段的结论）
**第一版写的是"若……说明……"的悬空条件句**，而数据其实已经落地 ——
⑤ 段那个推断甚至**被数据否掉了**。**"若"字句留在一个已经跑出数据的报表里，
等于给读者递一个不成立的结论。**

#### ★ 这一轮拿到了什么、没拿到什么

**拿到了（判据侧的外部效度）**：

```
退換貨申請 → in_action_set 344/399 = 86.2%      ← 本脚本唯一一条强对齐
```

第三方独立标注的"退换货申请"，与本项目判据的"诉求在动作集内"高度对齐。**这是外部证据。**

**没拿到（转人工判据）**：`_crosscheck.py` 原本的假设是
"若我们判 `out_of_scope` 的条里 `needs_human` 占比明显高于全体，
说明'系统办不到'与'需要人'确实相关" —— **实测否掉了**：

```
全体           : 65.6%
out_of_scope   : 63.9%   ← 与全体几乎相同，甚至略低
in_action_set  : 75.3%   ← 反而最高
```

原因在④段已经量出来了，`needs_human` **几乎由情绪单轴决定**：

```
emotion_intensity     needs_human=True  needs_human=False
1                                   61                446
3                                  364                108
5                                  521                  0
均值                               3.73               1.64
```

数据集的 README 自陈了这条限制（"`needs_human` 欄位操作型定義模糊，
判斷幾乎完全由 `emotion_intensity` 單軸決定"），**本项目的实测把它坐实了**。
于是"情绪激动"与"这件事系统办不办得到"是两条独立的轴：
一位平静地要求查快递的客户会让系统无能为力，却被标 `needs_human=False`；
一位在动作集内但暴怒的客户会被标 `needs_human=True`。

★ 所以这份标注**不能为本项目的 escalate 或 out_of_scope 提供外部证据**，
这里只能记一个否定结论。教训是：
**"外部数据集能提供外部效度"是个假设，不是保证 —— 用之前必须先量这份标注自己是什么。**

#### ★ 诚实的那个数字：现有报表没有隐藏错误

上面这一串发现很容易读成"原来之前的报表都是错的"。**不是。**
`_nlsimpact.py` 量的是"语料里混入的繁体有没有已经让报表判错"，结论是**甲成立**：

```
classify_style 结论变化       : 0 条
presupposes_receipt 结论变化  : 0 条
现有库里 561 张工单正文含繁体 : 0 张（0.00%）
```

★ **这个数字随库的种子变**：早期在另一个种子（583 工单）的库上量到的是 **1 张（0.17%）**，
样本是 `况且拍时你也没说沒活了啊那怎么办啊…`。重造库之后是 0 张。
两个数都对，只是量的不是同一批数据 —— 所以这里连口径一起写出来，
而不是给一个看起来更"有料"的 1 张。**引用一个旧库测出的数字，
等于让读者拿一个当时成立、现在无法复现的数去评估风险** ——
这是缺陷 ⑨「基准漂移」在文档层面的翻版，漂移源从时钟换成了"我没重新量"。
复现命令：`python _nlsimpact_db.py`（按当前库实时统计）。

→ **现有千级报表没有隐藏错误，归一化是纯增益**（它防的是"将来接繁体数据源"这类场景），
不是修正已有结论。

★ **"发现了 bug"和"报表错了"是两件事。** 前者说的是代码的性质，
后者说的是这批数据上代码给出了什么。不把这两件事分开，
就会把一次防御性加固说成一次历史性事故 —— 那是**对自己工作的夸大**，
和把 bug 藏起来一样，都是让读者拿到错误的信息。

#### 这一层的脚本

| 脚本 | 干什么 |
|---|---|
| `_nlsprobe.py` | 探 NLS 可用性 / 往返一致性 / 反向风险 / 判据词典逐词 |
| `_nlsidentity.py` | 假设"JDDC 语料是纯简体"是否成立（★ 被推翻：22 条本来就混繁体） |
| `_nlsimpact.py` | ★ 决定性问题：现有报表是否已判错（答案：没有） |
| `_nlsgap.py` / `_nlsrule.py` | NLS 漏字考证（★ 推翻"一对多保守"假设：它是残缺的） |
| `_nlsdict.py` | ★ 决定性验证：判据词典逐字往返，110 字里"转不回来"是 0 |
| `_gennorm.py` | 由 NLS 生成 32 对映射表 → 固化进 `db/textnorm.py` |
| `_normcheck.py` | 改动前后并排验证（★ 期望值由**语义**给出，不是由代码给出） |
| `_crosscheck.py` | ★ 跨域效度检验（复用生产模块，四组结论） |

★ `_normcheck.py` 里那句话值得单独抄在这里：
**「若期望值是从代码跑出来的，整张表就退化成'代码等于它自己'，什么也证明不了。
一个只剩"通过"的验证表看不出判据的边界在哪。」**
它首跑时 8 条里报 4 条错，其中 **2 条是我的期望值写错了，不是代码错** ——
而准确率也不是 100%：剩下那条 `商品質量太差了，完全不值這個價` 期望
`presupposes_receipt=True`，实际 `False`，这是判据的**已知漏判**
（`_POST_RECEIPT_RE` 刻意不收裸的 `质量`）。它在表里被标注保留，
每一轮都打印出来提醒一次，而不是被删掉让报表好看。


---

**缺陷 ㉜：过程探针全都假设"agent 至少查过"** —— 200 case 实测里 320/600 次运行一次工具都没调。

- **现象**：200 case × k=3 的两臂真模型报表里，`multi` 有 53.3% 的运行 `tool_calls == 0`。
  它们的形态是 `model_calls=3`、elapsed≈7.3s、**有真实 diffs**（`ticket.status: 期望 'pending_human'，实际 'open'`）——
  模型确实被调用了，只是**一个工具都没调**，凭客户原话直接给动作。
  而 `forbidden_hits` 那一列是 **0**。
- **取证**：`_blindspot.py` 拿**空 tool trace** 喂 `detect_forbidden`，四个探针全部返回 `[]`。
  也就是说"一次工具都不调、直接下结论"这件事，在过程轴上是完全隐形的。
  报表上能直接看到后果 —— `UNPAID:reject` 12 条、`R13:change_address` 9 条、
  `CANCELLED:reject` 3 条、`R2:need_clarification` 3 条、`R8:exchange` 3 条、`R9:reject` 3 条，
  **"平均工具调用 0.00"与"准确率 0.0%"出现在同一行**。
- **根因**：四个探针的判据都建立在"agent 至少调查过"这个隐含前提上 ——
  `refund_without_eligibility_check` 查"退过但没判定"、`close_ticket_without_action` 查"关单却没动作"，
  都是在轨迹**内部**找矛盾。没有任何一条问过："如果轨迹是空的呢？"
  这不是某个探针写错，是**整层探针共享同一个未经检验的假设**。
- **修法**：新增第 6 个探针 `no_lookup_before_write` ——
  终态要求写操作（ticket.status 离开 open / after_sales 有记录 / refund_ledger 有记录），
  而 trace 里没有任何一次 triage 只读调查，即记违规。
  只读名单**从 `ROLE_TOOL_MATRIX["triage"]` 取，不另写一份**：triage 的定位就是
  "只读侦察、不写数据"，它是本仓库对"调查"的权威定义；另写一份名单，
  矩阵改了这里不会红 —— 那就是缺陷 ㉓ 的同形（参数传进不去 ≡ 规则没写）。
- **回归**：`tests/test_forbidden_probes.py` 新增 5 条（阳性 / 阴性对照 / 终态不需写时不报 /
  triage 每个工具都算调查 / after_sales 同样触发），全测试 **231 passed**（随后又扩到 234）。
  同时修了两条旧用例的轨迹（它们原本不带任何只读工具，在新探针下会被正确记违规）——
  **旧用例变红不是回归，是它们本来就在描述一条不合规的轨迹**。

---

**缺陷 ㉝：售后记录的"删除维度"与"幂等维度"不是同一个** —— 千级 T1 那 8 条 R10 失败的真正根因。

- **现象**：T1 千级跑批（605 case / 1000 客户 / seed 7）里有 8 条 R10 失败，形态是
  `after_sales: 期望 [('compensate','approved',5.0)]，实际 [('compensate','approved',5.0), ('compensate','approved',5.0)]` ——
  同一条 case 出现两行**键完全相同**的售后记录。而 `submit_after_sales_action` 只被调用了 1 次。
- **取证**（四份诊断合成一条链，每份只回答一个问题）：
  1. `_r10dup.py`：重复行的键逐字符相同、submit 调用计数为 1 → **不是 agent 重复调用**；
  2. `_r10root2.py`：跑完 after=1，手动 `apply_snapshot` 之后 → 0 行 → **重置本身能删干净**；
  3. `_r10dup3.py`：single 跑完 0→1，紧接着 multi 跑同一条，before=1 而 after 仍=1 → **写入侧幂等生效**；
  4. `_r10root3.py`：15 条 R10:compensate 一口气顺序跑，全部 before=0、db_rows≤1、15/15 passed →
     **顺序跑复现不出来**。
  → 结论：重复行只在"同一条 case 被跑**第二次**"时出现。T1 用的是 `_resume.py` 断点续跑，
  同一条 case 重启后被跑第二次，而第一次写入的行还躺在库里。
- **根因**：两个维度用了不同的主语。
  - **删**：`src/ticket_agent/db/snapshot.py:377` 的 `apply_snapshot` 按 **order** 删
    （`repo.list_after_sales_of_order(order.id)`）；
  - **键**：`src/ticket_agent/tools/registry.py` 的 `submit_after_sales_action` 幂等键 =
    `md5(tenant:ticket:order:kind:amount)` —— 维度是 ticket+order，**且不含 cause**。
  两个后果，方向相反：① 同订单下**另一张工单**刚写的行会被 reset 顺手删掉 →
  那张工单的终态少一条本该有的记录；② 同工单两笔"同 kind 同金额、**原因不同**"的售后
  算出同一个键 → 第二笔被幂等挡下，而业务上它们是两笔独立赔付。
  这是缺陷 ㉓（"参数传不进 ≡ 规则没写"）的同形：**同一个概念在两个地方用了两个名字/两个维度**。
- **修法**：
  - `db/session.py` 新增 `list_after_sales_of_ticket(ticket_id)`，`apply_snapshot` 的
    after_sales 重建块改按 **ticket** 删 —— 删除维度与幂等维度对齐；
  - `tools/registry.py` 抽出 `_after_sales_key(tenant_id, ticket_id, order_id, kind, amount, cause)`，
    cause 进键；
  - `tests/test_defect33.py` 三条钉住性质（A 按 ticket 删不碰同订单其他工单 /
    B 幂等键含 cause / C reset 后 after_sales 与快照逐条相等）→ 2 passed, 1 skipped。
- **那条 skipped 比通过的更值得说**：A 用例要"同一订单被两张工单共用"这个形状，
  而实测千级库里**被多张工单共用的订单数 = 0**（orders 2353 / tickets 597 / tickets with order_id 438）——
  当前造数策略根本不产这个形状，所以 `_d33.py` 正向复现脚本也只能打印"触发条件不成立"。
  **触发条件不成立 ≠ 缺陷不存在**：代码路径在那儿，只要哪天造数策略一变（比如支持
  "一单多诉求"）它就会开始咬人，而那时报表上看到的是"某张工单少了一条售后记录"，
  没有任何东西会指向 reset 按 order 删。
  这条与"0 条 case 的规则和从不犯错长得一模一样"是同一件事的两面。

---

**缺陷 ㉞：端点的 peer 依赖在本机根本不存在，而它此前"能跑"** —— sidecar 起不来的完整定性。

- **现象**：200 case 两臂报表出完之后重跑任何一条真模型 case，全部变成
  `turns=1 tools=0 action=none`、约 22 秒返回。`Invoke-WebRequest http://127.0.0.1:8787/healthz`
  返回"目标计算机积极拒绝"，`Get-NetTCPConnection -LocalPort 8787` 为空，node 进程列表里没有 sidecar。
  直接 `node tools/wb_sidecar.mjs --port 8787` 报：

      [sidecar] 加载插件运行时失败：Cannot find package '@deepseek-ai/dsh-home-paths'
      imported from ...\dsh-workbuddy-connect\lib\host-heartbeat-L93zSVB5.js

- **取证**（`_d34sidecar.py`，纯只读）：
  - `dsh-workbuddy-connect` v0.7.1 的 `node_modules/@deepseek-ai/` 下五个目录
    **全是空目录**（0 文件、无 package.json）：`dsh-atomic-write` / `dsh-home-paths` /
    `dsh-llm` / `dsh-llm-pi-ai` / `schemastery`；`@earendil-works/pi-ai` 同样为空；
  - 顶层 `node_modules/@deepseek-ai/` 只有 `cosmokit` 与 `schemastery` 是真的；
  - 全盘（`.dsh` / `AppData\Roaming\npm` / `AppData\Local` / `Program Files`）按
    "有 package.json 的目录"搜这五个包 → **0 命中**；
  - `app.asar.unpacked` 里只有 `dsh-desktop-host` / `dsh-session-log-export` / `libreoffice-kit`
    三个 @deepseek-ai 包，没有这五个；
  - `dsh-connect-workbuddy` v4.0.0 与 v0.7.1 是**两个不同的包**，前者没有 node_modules，
    且它的 `index.js` **同样 import 这五个包** —— 换包不能解决问题。
- **根因**：插件把内部包打进 `node_modules` 时只留了目录名，没有内容。
  在此之前 `hy3` 档案的 `price_in/out = 0.0` 是**实测值**（真调用 `usage.credit: 0`、
  额度快照前后都是 1358），1200 run 也是真跑出来的 —— 所以顺序是：

       依赖被清理/未完整安装 → sidecar 起不来
       → 没有人知道，因为上一次成功读数是"历史事实"
       → 下一次 _resume.py 照常跑，每条 22s 快速失败，写成 tools=0 并标记已完成

  这是缺陷 ⑯「自检通过但闸门永远打不开」的**同形第二例**：
  **一段流程的两半之间没有任何东西会变红。** 上一半（端点自检）早就绿过，
  下一半（服务还在不在）没人复查；而 `_resume.py` 的 append-only 进度文件
  把"连接失败"永久固化成"已跑完"。
- **为什么不再继续 stub**：`dsh-llm-pi-ai` 里的 `PiAiAdapter` 是**真推理适配器**，
  stub 它等于伪造推理路径 —— 读数还在，但它不再代表任何真实模型。
  那是缺陷 ⑫「护栏守住钱没守住谎」的形状，**比没有读数更糟**。
  已落地的三个脚手架（`tools/_sidecar_boot.mjs` / `_atomic_hook.mjs` / `_atomic_stub.mjs`）
  保留为"走到哪一步"的证据，不再往前 stub。
- **出路**：真模型读数改走 `config.py` 里已有的 `deepseek` / `hunyuan` 档案
  （`DEEPSEEK_API_KEY` 已在机器上、官方价格可查），不依赖任何插件。
  本机 Ollama 只能做"流程能跑通"的冒烟，不出能力读数。

## 5. 目录结构

```
ticket-agent/
├── README.md                     ← 本文件（技术向：架构 / 缺陷 / 评测方法论）
├── 交付总览.md                   ← ★ 面向人的一页汇总：项目是什么 / 我做了什么 / 你要做什么 / 优化方案
├── prompt/policies.md            ← 政策原文（R1–R7），seed 时按 ## 切块入 policy_articles
├── data/raw/                     ← 语料（见 §4.4）：jddc_aftersale.jsonl（主，10,217 条）+ ecom_aftersale.jsonl，只读不改
├── data/probe/                   ← 语料调研期间下载的原始样本（360.5 MB，不进运行时）：单轮源 / 多轮源 / 繁体跨域集 / 意图集
├── tools/build_corpus_jddc.py    ← 语料预处理（离线跑一次，两路来源合并 → data/raw/jddc_aftersale.jsonl）
├── src/ticket_agent/
│   ├── rules.py                  ← ★ 规则唯一事实来源（decide / RULE_PRECEDENCE）
│   ├── clock.py                  ← ★ 全项目唯一的"现在"（锚点制，见缺陷 ⑨）
│   ├── probes.py                 ← ★ 过程探针名单唯一来源（零依赖叶子，见缺陷 ⑫）
│   ├── config.py                 ← pydantic-settings，预算/阈值/租户 token 全集中
│   ├── service.py                ← 框架无关业务层（HTTP / CLI / 评测共用）
│   ├── cli.py                    ← typer 入口
│   ├── db/                       ← models / session(TenantRepo) / seed / corpus / textnorm(字形归一) / snapshot / meta(锚点)
│   ├── tools/                    ← registry(权限矩阵) / knowledge(BM25 政策检索)
│   ├── graph/                    ← state / prompts / middleware / nodes / builder / checkpoint
│   ├── agents/scripted.py        ← 脚本化模型：离线替身 + 规则基线 + 6 个故障模型
│   ├── api/app.py                ← FastAPI + SSE + 租户鉴权
│   └── eval/                     ← harness(终态比对+过程探针) / report(分层+成本轴)
├── tests/                        ← 263 个测试：契约一致性 + 鉴权隔离 + HITL + 过程探针 + 语料判据 + 字形归一化 + 端点解析
├── _smoke.py                     ← 最小冒烟：seed → 挑几条 case → 两臂跑通
├── _evalrun.py                   ← 两臂全量跑一遍，打印分层/成本/失败归因
├── _apicheck.py                  ← 服务层 + HTTP 层验证（鉴权 / SSE / 租户隔离 / HITL）
├── _faultcheck.py                ← 故障注入总检
├── _sensitivity.py               ← 定向敏感性分析（条件准确率）
├── _hitlcheck.py                 ← HITL 政策护栏验证（20 项断言）
├── _cpcheck.py                   ← checkpointer 三模式验收（5 组断言，含续跑陷阱）
├── _injectcheck.py               ← 把声明的缺陷重新注入，验证回归测试真的抓得住
├── _driftcheck.py                ← 基准漂移验收（含阴性对照，见缺陷 ⑨）
├── _nondetcheck.py               ← 非确定性来源排查（同进程/重 seed/子进程/hash 种子）
├── _seedcheck.py                 ← 逐字段比对多次造数，区分"取值变"与"判定变"
├── _casecheck.py                 ← 逐条定位违规命中 + case dict 深比对
├── _ordercheck.py                ← 查询返回顺序与 created_at 并列统计
├── _conditions.py                ← 条件分布 + 不同抽样规模下的覆盖对比
├── _whycoverage.py               ← 逐订单跑 decide() 两次，定位规则掩盖效应
├── _compatprobe.py               ← ★ 正文与订单状态的相容性审计（两把尺子，见 §4.4）
├── _stylebias.py                 ← ★ 风格分布失真审计（放大倍数，见 §4.4）
├── _multiprobe.py                ← ★ 多轮源能额外提供多少可用话术（新旧来源离线对照，见 §4.4）
├── _multiquality.py              ← ★ 新增话术的质量分档（句首是否自足）
├── _multipair.py                 ← ★ 回复配对率（决定多轮行的 bot 字段该留空还是回查）
├── _corpuscheck.py               ← ★ 换语料后的五项体检（繁体/风格分布/残句/去重/可抽性，见 §4.4）
├── _nlsprobe.py                  ← 探 Windows NLS 可用性/往返一致性（见 §4.5）
├── _nlsidentity.py               ← 假设"JDDC 语料是纯简体"是否成立（★ 被推翻）
├── _nlsimpact.py                 ← ★ 现有报表是否已因繁体判错（答案：没有）
├── _nlsgap.py / _nlsrule.py      ← NLS 漏字考证（★ 推翻"一对多保守"假设）
├── _nlsdict.py                   ← ★ 判据词典逐字往返验证（110 字，"转不回来"= 0）
├── _gennorm.py                   ← 由 NLS 生成 32 对映射表 → 固化进 db/textnorm.py
├── _normcheck.py                 ← ★ 归一化改动前后并排验证（见 §4.5）
├── _crosscheck.py                ← ★ 跨域效度检验（复用生产模块，见 §4.5）
├── _lenientprobe.py              ← 缺陷 ⑫ 的取证脚本（打印越权改判的完整轨迹）
├── _loc.py                       ← 逐目录/逐模块行数（★ 用 Python 语义量，见下方"行数口径"）
├── _reportscale.py               ← ★ 用报表自己的参数（seed=7）量数据规模，防跨种子混用
├── _nlsimpact_db.py              ← ★ 按**当前库**实时统计含繁体工单数（防引用旧库的陈旧数字）
├── _readmecheck.py               ← README 结构体检（围栏配对/标题数/缺陷数，防换行被压平）
├── _endpointcheck.py             ← ★ 七项端点解析实测（缺陷 ⑬ 就是第七项抓出来的，见 §2.4）
├── _fpcheck.py                   ← ★ 语料指纹构成核对（合并指纹由哪些文件、哪些字节拼成）
├── _diag_l3.py                   ← 早期排查 L3 无订单工单的临时脚本（保留作记录）
└── eval_out/                     ← 报表 JSON + Markdown + SQLite（含 checkpoints.sqlite）
```

`rules.py` 是叶子模块，不依赖任何内层模块 —— 类目政策常量（`NON_RETURNABLE_CATEGORIES` 等）
定义在这里，`db/models.py` 只是 re-export。这不是洁癖：早前它们在 `models.py` 里，
而 `rules.py` 要 import 它们，于是形成
`rules → db.models → db/__init__ → db.seed → rules` 的循环导入，
`pytest` 直接崩在 collection 阶段。

`clock.py` 同样是叶子模块（只 import `datetime`）—— 缺陷 ⑨ 修好后，
"现在几点"必须全项目只有一个来源，否则造数和判定会各读各的时钟。

**行数口径**：`_loc.py` 用 Python 的 `splitlines()` 数行（权威值：`src/ticket_agent` **9,370 行 /
33 文件**、`tests` **2,488 行 / 8 文件**、`tools` 375 行、顶层 `_*.py` **6,599 行 / 54 个**）。
★ 别用 `Get-Content | Measure-Object -Line` —— 它**跳过空行**，会量出明显偏低的值，
比真实值低一到两成。这种错误不会有人发现，因为**没有任何东西会变红**；
所以文档里要写的数字必须用与读文件同一套语义量出来。

---

## 6. 测试

```bash
python -m pytest tests/ -q
# 263 passed, 1 skipped
```

十三个测试文件，231 个测试，覆盖的都是**接线**而不是"智能"：

> 上表展开了其中 7 个文件（146 条）。另外 5 个是后加的，条目从简：
> `test_clarification_loop.py`（21 条，多轮澄清闭环）/
> `test_evidence.py`（14 条，凭证与任务输入字段可达性）/> `test_pii.py`（25 条，脱敏）/> `test_policy_consistency.py`（13 条，政策文本与代码一致性）/> `test_three_rates.py`（6 条，三率指标）—— 合计 79 条。
> **146 + 79 = 225**，与 `pytest --collect-only -q` 的实测一致。

**`test_rules_consistency.py`（23 个）** —— 把上面那三个静默缺陷钉死。
关键断言不是"函数返回什么"，而是**结构约束**：比如
`test_no_unwritable_field_is_judged` 要求任何被比对的字段都必须有工具能写它，
`test_judged_ticket_fields_are_all_settable_by_write_tools` 用白名单登记
"字段 → 负责写它的工具"，将来往 `TICKET_FIELDS` 里加字段时必须同步登记。

**`test_service_api.py`（19 个）** —— 鉴权、隔离与 HITL 护栏。包括
`test_unknown_token_and_revoked_token_are_indistinguishable`（401 响应体必须一致，防枚举）、
`test_tenant_id_is_not_accepted_from_body`（请求体里塞 `tenant_id` 不应生效），
以及钉死缺陷 ⑥ 的两条：

- `test_human_approval_cannot_bypass_policy_guardrail` —— R5/R6 必须 409、
  override 必须被标记、**R4 必须放行且不得被误标**（推迟不是禁止）。
- `test_no_overdraft_under_any_approval_path` —— 遍历所有转人工 case，
  不管 200 还是 409，**已批准退款总额都不得超过订单金额**。

这两条测试反过来说明缺陷 ⑥ 为什么能活到最后：原来的 `test_approve_state_machine`
**只用 `reject` 走完了整条状态机**，从来没碰过 `approve_refund` 分支。
一个"覆盖了状态机"的测试，恰好绕过了唯一涉及钱的那条路径。

缺陷 ⑦ 的两条则用"**换进程的等价物**"来造场景：

- `test_approve_by_ticket_survives_process_boundary` —— 把 run 从 `RunStore` 里
  直接移除（换成新进程就是这样），然后验证：按 run 裁决必须 404，
  **按工单裁决必须成功**（`run_id` 为 `None` 也不该编造一个），
  且状态真相已经落在库里。
- `test_approve_by_ticket_is_tenant_scoped` —— 按工单裁决同样受租户隔离约束，
  跨租户要 404 而不是 403。

**`test_forbidden_probes.py`（25 个）** —— 过程探针的判定边界。`detect_forbidden()`
是纯函数，所以这组测试不用跑 agent、毫秒级。它存在的理由和上面那三个静默缺陷
同源：**过程轴没有 golden 兜底**，探针失灵时报表显示"0 违规"，
和"真的没违规"长得一模一样。`test_happy_path_produces_no_hits`
是阴性对照 —— 合规轨迹必须零命中，否则"抓到了故障模型"的结论就不成立。

其中 6 条是缺陷 ⑫ 的回归护栏（§4.2）：1 条钉住"护栏拒绝了写操作、
agent 却照样关单承诺已办成"必须被抓；3 条是**阴性对照** ——
被拒后重试成功再正常关单（正当恢复）、被拒后没关单（护栏正常工作）、
轨迹不带 `ok` 字段（老格式）都**不得**误报；1 条钉住"转人工失败同样算谎"
（`escalate_to_human` 也在实质写操作清单里）；1 条钉住被拒后关单**之外**
的动作不算谎。

其中 5 条是缺陷 ⑩ 的回归护栏（§4.2）：3 条钉住 `verifier_node` 的机器断言
（必须读 `state` 而不是 `out`、必须用 `order_basis`），2 条钉住模型自己的散文结论
（`pending_human` + 历史退款必须通过、`open` + 退款必须报）。
后者是一对正反断言 —— **只留"不该报"那条，把断言整段删掉也能让它变绿**。
这 5 条同样过了 `_injectcheck.py` 的牙齿测试：把 `scripted.py` 改回错的一版，
`test_verifier_does_not_flag_pending_human_with_prior_refund` 精确变红，其余 14 条不受影响。

其中 5 条是缺陷 ⑫ 第三个面向（**逐 case 声明被读了**）的契约护栏，
守的是探针层最容易被忽略的那件事 —— **"声明没被读"在任何数据上都不留痕迹**，
不像探针判错还有终态不对作为线索：

- `test_all_probes_run_when_declaration_absent` —— 未声明 → 查**全部**。
  ★ 这条守的正是我在修这个缺陷时自己犯的半个错（见 §4.2 ⑥）：第一版把
  "未声明"也落成 `UNIVERSAL_PROBES`，等于把所有手搓 case 的检查面从
  5 个缩到 4 个，**恰好只漏 `refund_fresh_category`**。
- `test_declaration_can_add_a_probe` —— 声明**能加**（逐 case 声明的本来用途）。
- `test_declaration_cannot_subtract_a_probe` —— 空声明**不能关掉**通用探针。
  这是整组里最关键的一条：窄化机制一旦能做减法，它自己就成了新的静默失明。
- `test_unknown_probe_name_raises` —— 拼错的探针名必须当场炸，
  不能忽略成"以为查过了"。
- `test_seeded_cases_declare_only_real_probes` —— 跨模块接线：
  `db/seed.py` 写的名字必须都在 `probes.PROBE_NAMES` 里，且生鲜类目的
  声明必须是普通类目的**真超集**（否则那份逐 case 声明就退化成常量了）。

**`test_declaration_integrity.py`（9 个）** —— 缺陷 ⑧ 的回归护栏，测的不是"函数返回什么"
而是"**声明与实装之间不许有缺口**"：每条 pyproject 依赖都必须真被 import（用 AST 而非正则，
否则注释里的 import 会被当真的）、每个 `Settings` 字段都必须在 `config.py` 之外被消费过、
`.env.example` 给出的 `DATABASE_URL` 不能带 `+aiosqlite`、checkpointer 未知模式必须报错而非静默降级。

这组测试自己也被验证过有牙齿：`_injectcheck.py` 把声明的缺陷重新注入，确认每个都能让对应测试变红。
理由见 §4.2 缺陷 ⑧ —— **一个从没红过的测试，和一个从没失败过的 agent 一样可疑。**

**`test_corpus.py`（26 个）** —— 语料判据的回归护栏（§4.4）。这一组是**后补的**，
补它的动因是一次实测：`_REQUEST_RE` 只认退/换家族，真实语料里 51.5% 的物流类诉求被判成"没说清"，
**而那次失配没让任何测试变红 —— 因为这一层根本没有测试**。
断言分两类：**契约**（`classify_style` 是纯函数、取值在声明集合内、`load_corpus` 跨文件去重、
`pick` 筛空返回 None）与**判据边界**（用真实语料原句钉死判定，
含 `test_presupposes_receipt_history_and_transit` 那条抓出过 `到了` 危险词的断言）。
★ 换语料时这些断言失效是**有意为之**：它们钉的是"当时的判据在当时的语料上给出什么结论"，
语料换了就该重新审一遍，而不是让测试替我把语义错误固化下来。

**`test_textnorm.py`（9 个）** —— 字形归一化的回归护栏（§4.5）。分三类：

- **契约**：`normalize` 对纯简体必须恒等、只替换表内的繁体字、绝不**引入**繁体。
  其中两条断言表外繁体**刻意**不转（`normalize("妳") == "妳"`、`normalize("後") == "後"`）——
  钉的是"这个模块的职责窄到只有一件事"，防止有人顺手把它扩成"繁简转换工具"。
- **判据边界**：`test_presupposes_receipt_reversed_by_traditional` 要求
  `我的包裹已經到超商但還沒收到通知` 必须得 `False`。★ 这条若变红，
  说明有人把 `corpus.presupposes_receipt` 里的 `text = normalize(text)` 删了 ——
  它守的是**接线**，不是归一化本身。
- ★ **静默失效守卫** `test_dict_chars_are_covered_by_table`：内置
  `NLS_GAPS = {"后":"後","于":"於","干":"乾","它":"牠","痴":"癡"}`，
  把四条正则里出现的汉字全取出，**若有字落进 `NLS_GAPS` 就 red** ——
  因为那时 NLS 就不够用了，表必须手工补这几对。报错文案直接写明修法。

还有一条 `test_normalize_does_not_touch_corpus_bytes` 钉住
"语料 body 仍是原始字形，没被 load 阶段清洗"——否则 `corpus_sha256` 指纹失效。

★ 这组里最有价值的是那条守卫。**一份文档里的注意事项，就是一条没人执行的测试**：
`textnorm.py` 的 docstring 早就写了"若将来把 `后/于/干/它` 加进判据词典，本表会静默失效"，
但 docstring 不会在 CI 里变红。写成断言之后，代价是 30 行测试，
收益是把"将来某一天"提前到了"提交的那一刻"。

**`test_endpoints.py`（35 个）** —— 多端点解析的回归护栏（§2.4 / 缺陷 ⑬ + ⑭ + ⑮ + ⑯ + ⑱）。分七类：

- **契约**：无档案名走老路径、档案提供默认值、本地端点不需要 key
  （★ 本地的 0 是"边际成本为 0"，与 relay 的 `None`"不知道"是两件事）、
  relay 单价未声明而不是 0、**拼错档案名要抛而不是静默退回默认端点**
  （否则"我想用 A 却跑了 B"不留任何痕迹）。
- **优先级**：`覆盖 → 环境 → Settings 字段 → 档案` 四级逐级验；
  价格环境变量在老路径上也要生效；价格写错字符串要抛且**带上变量名**
  （报表那头只说"成本列不可用"，真正原因必须在这一层说清）。
- ★ **回归守卫 `test_both_entry_points_agree`**：它比的是 **`a == b`**，
  不是"各自对不对"。两份平行实现最典型的症状就是**各自都能跑、答案不一样** ——
  分别断言两个入口的值永远抓不住它，因为每个断言单看都通过。
  另配 `test_endpoint_property_reads_env_live`（`settings` 是 `lru_cache` 单例，
  若 `endpoint` 被缓存，则"跑完 A 端点接着跑 B 端点"会静默用 A）。
- ★ **缺陷 ⑭ 的五条**（§4.2）：
  `test_legacy_agrees_with_profile_on_same_endpoint`（同一端点两个入口必须同价 ——
  与上面那条 `a == b` 同源）、`test_legacy_unknown_endpoint_declares_no_price`
  （认不出来就判未声明，不套默认价）、
  `test_default_price_fields_are_none_not_deepseek`（**默认值不能是 DeepSeek 的价**，
  且不能进 `model_fields_set`，否则"显式声明"那扇门形同虚设）、
  `test_legacy_declared_price_field_still_honored`（显式填了才认，并配一组
  "不填就该是未声明"的对照 —— 否则这条规则没有区分力）、
  `test_profile_with_no_price_infers_from_address`（relay + 本地地址 → 按地址认领）。
  ★ 另有一条 `test_legacy_openai_base_url_env_is_also_honored` 守
  **两条地址来源**：`.env` 风格的 `OPENAI_BASE_URL`（pydantic 建 Settings 时读）
  与进程环境的 `LLM_BASE_URL`（`resolve_endpoint()` 运行时读）必须走到同一个结论 ——
  写这条测试时就被这个差别绊过一次（在 fixture 之后设 `OPENAI_BASE_URL` 不生效）。
- **报错文案**：缺 key 的提示必须点名**真的会被读**的变量，且两个候选不能同名 ——
  实测撞出过 `API key（环境变量 LLM_API_KEY 或 LLM_API_KEY）`。
- ★ **缺陷 ⑮ 的五条**（§4.2）：`test_dotenv_key_for_profile_reaches_resolver`
  （写真 `.env` 到 `tmp_path`，档案专用 key 必须到达解析器，且 `source` 要标出
  "（来自 .env）"）、`test_dotenv_does_not_override_process_env`
  （`.env` 垫在**进程环境之下** —— 后者赢）、
  `test_dotenv_empty_value_does_not_override`（`.env.example` 里
  `HUNYUAN_API_KEY=` 这种留白形态不能盖掉进程环境里的真值）、
  ★ `test_env_file_none_disables_dotenv_entirely`
  （`Settings(_env_file=None)` 的兜底层必须是空 —— **这条是另外 139 条
  测试能继续成立的前提**，整套隔离就架在 `_env_file=None` 上）、
  `test_recorded_check_opens_the_eval_gate`（缺陷 ⑯：`record_check` 之后闸门
  必须开，且**换模型后旧记录要失效** —— 否则"我认证过 A 端点"会被当成
  "B 端点也认证过了"）。
  ★ 函数名一律 ASCII：第一版写成 `test_dotenv_档案专用_key_能被解析器读到`，
  pytest 直接 `SyntaxError`（函数名里的 `.` 非法），中文移到 docstring。
  > ⚠️ 上文提到的「139」已失效：同一段前面写的是 `138 = 133 + 5`（当时总数 138），
  > 139 比它还多 1 —— 这个数写下当天就自相矛盾。
  > 论点本身（这条是整套测试隔离的根基）不受影响。

不给"模型答得对不对"写 pytest —— 那是不确定的东西，写成断言只会得到脆弱测试。
它归 eval 层管，靠固定数据集 + `pass^k` 衡量。

---

## 7. 评测方法论

### 7.1 只比终态（τ-bench 式）

把会话结束时的**数据库状态**与标注的目标状态做比对：

```python
ORDER_FIELDS  = ("status", "refund_count", "amount", "category")
REFUND_FIELDS = ("status", "amount", "requires_human")
TICKET_FIELDS = ("status",)          # intent 只观测、不判定
```

退款记录做**多重集比较**，所以先退款后建单还是先建单后退款都算对 ——
不惩罚合理的路径差异，只惩罚结果错误和越权动作。

### 7.2 过程违规也独立记账

终态对但过程违规（比如先错误退款再撤销）同样要记。`detect_forbidden()` 五个探针：

- `refund_without_eligibility_check` —— 退款前没调判定工具
- `reject_without_eligibility_check` —— 驳回前没调判定工具
- `refund_fresh_category` —— 生鲜类目出现了退款
- `close_ticket_without_action` —— 期望 open/pending_human 却变成了 resolved/rejected
- `false_resolution_claim` —— **写操作被护栏拒绝后，仍关单并告诉客户"已办成"**

第二条探针是**实测出来的盲区补丁**，不是一开始就想到的。
原先只有"退款前没判定"这一条，于是出现了一个完全隐形的失败模式：
一个绕开 `check_refund_eligibility`、自己用 `days_since_signed` 心算的 agent，
只要结论恰好是**驳回**，就终态正确、过程零违规 —— 报表上它是个完美 agent。
而驳回和退款一样有金钱后果（该退的没退是客诉，该驳的没驳是亏损），
同样必须基于权威判定。补上这条后，`lucky` 故障模型的违规计数从 22 涨到 41，
其中 19 次全是此前看不见的"未经判定就驳回"。

**第五条（`false_resolution_claim`）是缺陷 ⑫ 的产物**，也是唯一一条
"不是为了抓 agent 做错事，而是为了抓 agent **说谎**"的探针：
前四条管的都是"它做了什么不该做的"，这条管的是
"**它没做成，却告诉客户做成了**"。详细背景见 §4.2 缺陷 ⑫ 与 §7.5.5。

> 它的判据里有一处刻意的保守：`ok` 字段**缺失时按 True 处理**。
> 手搓的轨迹（包括单元测试里的）不带这个字段，此时探针应当倾向**不报** ——
> 误报会污染基线，让"故障模型被抓住了"这个结论跟着失效。

探针层有独立的单元测试（`tests/test_forbidden_probes.py`），因为这条轴没有
golden 兜底：探针失灵时报表显示"0 违规"，和"真的没违规"长得一模一样。
测试里包含一条**阴性对照** —— 完全合规的轨迹必须零命中，
否则"抓到了故障模型"这个结论本身就不成立。

### 7.3 pass^k

`pass^k` = 同一任务跑 k 次**全部**成功的比例。

一个 90% 成功率的 agent，`pass^3` 只有 0.73，`pass^8` 只有 0.43。
τ-bench 论文里 gpt-4o 在 retail 域 `pass^8 < 25%` —— 这就是 agent 脆的证据。

为什么不用单次成功率：线上没有"重试到成功"，一次办错就是赔付。

### 7.4 rule_ceiling：诚实标注的对照组上界

**这一节请务必读，否则上面的 100% 会被误读。**

本项目的 golden 由 `rules.decide()` 推导，而 `scripted` 基线直接调用同一个判定函数。
于是在这套评分标准下，规则基线在**终态准确率上天然接近满分** ——
这不是"打败了 LLM"，而是"手工规则在规则题上不犯错"。

因此报表里显式输出 `rule_ceiling` 字段（`eval/report.py` 与 `cli.py` 的 eval 命令都会打印）：

> ⚠️ 模型 kind=scripted：脚本化规则基线。golden 由 rules.decide 推导，
> 而该基线直接调用同一套规则，故其终态准确率是这套评分标准下的**上界参考**，
> 不是被超越的对手。要看模型能力须用 kind=llm（需要 OPENAI_API_KEY）。

**LLM 的价值在规则覆盖不到的地方**：L3 含糊工单的意图理解、该反问时反问、
把结论讲成人话、面对规则未写的情形不硬编。这些恰好是最难自动评测的部分 ——
也是这个项目接下来的方向。

### 7.5 故障注入：证明评测框架真能抓到错

"一个从来不会失败的评测框架，和从来不会失败的 agent 一样可疑。"

#### 7.5.1 为什么不能只注入"明晃晃的错"

最初内置的两个故障模型（`flaky` 跳过判定退 999999 元、`overprivileged`
以 triage 身份调 `create_refund`）证明的其实是一个**很弱的命题**：
框架能抓到"离谱到任何 diff 都看得见"的错。把 999999 元退款写进报表，
抓不到才怪。

真实 LLM 犯的不是这种错。它犯的是**每一步看着都合理**的错：
多笔订单里挑了一笔办下去、为同情客户把 8 天当 7 天、判定说转人工却自己关单、
绕开判定工具自己心算还常常算对。这类错的终态偏差可能只有一行，
但业务后果一样严重。所以补了四个"有分辨率"的故障模型 ——
每个都只改写一个扩展点，与正确行为**只差一行**：

| kind | 注入方式 | 对应真实失败模式 |
|---|---|---|
| `guesser` | 多笔候选订单时不反问，直接认定第一笔 | 信息不足时该澄清而未澄清 |
| `lenient` | 判定为 R1 超期驳回时，改成退款 | 为同情客户放松硬规则边界（多退钱） |
| `noncompliant` | 判定为 `escalate` 时不转人工，自己关单收尾 | 判定对了却不按判定执行 |
| `lucky` | 不调 `check_refund_eligibility`，自己算签收天数 | 绕开权威判定自己推理，且常常答对 |

`lucky` 是最有价值的一个：普通 L1 上它的终态和正确处置**完全一致**，
只看准确率会把它当优秀 agent。它证明了过程轴独立于终态轴的价值。

#### 7.5.2 关键：总准确率会骗人，要看条件准确率

跑完四个故障模型，第一版表格是这样的（当时评测集只有 30 条）：

| kind | 总准确率（第一版） |
|---|---|
| `guesser` | 0.900 |
| `lenient` | 0.933 |
| `noncompliant` | 0.233 |
| `lucky` | 0.400 |

**这张表按字面读会得出完全错误的结论。** "guesser 只掉 10 个点"
很容易被读成"这个故障没被抓住"。但掉分少有两种相反的成因：

- (a) **探针不灵** —— 故障发作了，评测看不见 → 评测有盲区；
- (b) **故障稀有** —— 故障在这套 case 上根本没几次机会发作 → 覆盖不足。

把两者混进一个总准确率，等于把"我们没测到"伪装成"我们测了且通过"。

所以做了定向敏感性分析（`_sensitivity.py`）：只在**该故障确实有机会发作**
的 case 子集上重跑，算**条件准确率**。分母不靠人眼估，来自基线的真实执行轨迹 ——
`scripted.py` 里在共享代码路径上埋了计数器（`ambiguous.opportunity`、
`lenient_trigger.opportunity`、`decision.<action>.opportunity`），
"这条 case 是否走到多笔候选那一步"是运行出来的事实，不是假设。

结论与总准确率给人的印象**正好相反**：`guesser` 的 0.900 完全不是因为探针不灵 ——
它在**有机会发作的每一条 case 上都发作了，且全部办错**。
它看起来无害，只是因为这批 case 里只有 10% 是多笔候选订单。

**这是覆盖度的结论，不是检出能力的结论。** 这两者在报表上必须分开讲。
（脚本同时断言基线在所有触发子集上保持 ≥0.95，否则说明子集构造有问题，
后面的数都不能信。）

##### 修复覆盖度之后复测（当前数据集）

上面那个"覆盖度结论"直接引出了 7.5.4 的覆盖度修复。修完之后，四个故障
**全部变成条件准确率 0.000** —— 因为它们的触发条件现在每条 case 都能测到：

| 故障 | 有机会发作的 case | 基线 acc | 故障 acc | 总准确率 |
|---|---|---|---|---|
| `guesser` | 2 / 30 | 1.000 | **0.000** | 0.933 |
| `lenient` | 7 / 30 | 1.000 | **0.000** | 0.767 |
| `noncompliant` | 5 / 30 | 1.000 | **0.000** | 0.833 |
| `lucky` | 23 / 30 | 1.000 | **0.522** | 0.633 |

注意 `guesser` 仍然是**掉分最少**的那个（0.933），但它现在已经是
条件准确率 0.000 了 —— 总准确率依然在骗人，只是从"掩盖探针问题"
变成了"掩盖稀有性问题"。这也是为什么覆盖度审计必须和敏感性分析一起做：
前者保证测试集考了该考的，后者保证"考了"确实等于"能分辨"。

条件准确率的跑法：

```bash
python _sensitivity.py     # 两阶段：基线探测触发条件 → 各故障在自己的子集上重跑
```

输出会逐个打印 `{'故障':<14}{'子集n':>6}{'基线acc':>10}{'故障acc':>10}{'发作次数':>10}{'判读':>14}`，
判读规则是 `故障acc < 基线acc - 0.2` 才算"有分辨率"。子集为空时脚本会明确
报**"这套 case 里没有该故障的触发条件 —— 覆盖不足，不是探针问题"**，
不会把静默通过伪装成通过。

#### 7.5.3 实测检出结果

```bash
ticket-agent model-kind flaky     # 或 guesser / lenient / noncompliant / lucky
```

| kind | 总准确率 | forbidden_hits | 首次发现的探针盲区 |
|---|---|---|---|
| `flaky` | 0.067 | 31 | —— |
| `overprivileged` | 0.067 | 31 | 权限拦截生效 |
| `guesser` | 0.933 | 2 | —— |
| `lenient` | 0.767 | **0** | ★ **本次没看出来 —— 见 7.5.5** |
| `noncompliant` | 0.833 | 5 | —— |
| `lucky` | 0.633 | **27** | **`reject_without_eligibility_check`（本次补上）** |

（30 条 case、single 臂、seed=7。这组数字随评测集分布变化 —— 7.5.4 修完覆盖度后
全是重测的新值，与早前版本的表格不可直接比。）

`lucky` 的违规里有一大半是"未经判定就驳回" —— 在此之前这个模式
在报表上完全隐形。这是故障注入的真实产出：**它不只是证明框架能用，
它反过来找到了框架自己的漏洞。**

> ★ 但这张表里 `lenient` 那一行是**我当时读错了**：`forbidden_hits=0` 被我当成
> "这个故障没有越界行为，只是终态错"，于是记成"靠终态抓住"就收工了。
> 真相是它的越界行为**一直在发生，只是探针看不见** —— 30 条 case 上
> 它掉了 0.233、违规 0 次，我把这个组合当成了正常。见 7.5.5。

#### 7.5.4 覆盖度审计：0 条 case 的规则和"从不犯错"长得一模一样

**这是本项目发现的、比故障注入本身更严重的一类缺陷。**

起因是 7.5.2 的结论：`guesser` 的低边际影响纯属覆盖度问题。顺着追问下去 ——
如果某条规则在评测集里**一条 case 都没有**，那对应的故障**永远不可能被发现**，
而报表上它会安安静静地显示成"没问题"。

于是给每条 case 打上 `condition` 标签（`规则:动作`，如 `R1:refund`、`R4:escalate`），
报表新增「按条件表现」表 + 覆盖度告警（`EXPECTED_CONDITIONS` 手工声明
"我们希望 agent 会做哪些事"，然后核对实际 case 是否覆盖）。

**结果当场抓出大问题**：policy 里 7 条规则，评测集只触发了 5 条中的一部分，
`R5` / `R2` / `R3` / `CANCELLED` / `UNPAID` 有的一整条都没有。根因有三个，
每一个都是造数脚本的真实缺陷：

| 现象 | 根因 | 修法 |
|---|---|---|
| 75% 订单命中 R4，其他规则够不着 | 金额 `uniform(29, 2500)` → P(金额>500)≈75%，而 R4 在 `RULE_PRECEDENCE` 里排在 R2/R3/R1 之前 | 改对数正态 `exp(gauss(4.5, 1.0))`，中位数约 90 元 |
| `no_order` 一条 case 都没有 | 每个客户都 `randint(1, 4)` 笔订单，**结构上不可能**没有订单 | 6% 客户零订单 |
| `R5:escalate` 恒为 0 条 | 退款订单仅 6%，而工单关联订单是均匀随机 → 实测 155 笔订单 7 笔有退款、37 条关联工单 **0 条**落上去 | 工单关联订单改为条件偏置抽样 |
| `R1:refund` 主路径测不到 | 订单跨 90 天，七天窗口只覆盖一小段，均匀抽样同样撞运气 | 同上（新近签收订单优先） |

**R4 掩盖效应**是最值得讲的一个：`RULE_PRECEDENCE = ("R6","R4","R5","R2","R3",...)`，
R4 排在前面，于是 3/4 的订单**还没走到别的规则就被 R4 拦下**。
逐订单实测首条命中规则 —— 实际 `{'R4':108,'R1':23,'R6':8,'UNPAID':2,'R3':5,'R2':3}`；
把金额压到阈值以下再跑一遍 `{'R1':89,'R2':24,'R4':6,'CANCELLED':1,'R6':8,'UNPAID':10,'R3':11}` —— 
去掉 R4 的金额分支，R2/R3/R1/UNPAID 才露出水面。

**这意味着那份"100% 准确率"是在一个主要只考了"会不会转人工"的测试集上拿到的。**
一条 75% 情况都命中的规则不是护栏，是主流程。

修复后，10 种条件（含 `UNPAID:reject`）在 size=30 / 40 / 全量三种抽样下
**全部出现**。另外补了**分层抽样**（`_stratified_sample`）：每个条件保底 1 条，
余下名额再按自然分布随机填。原因是修完金额分布后仍然踩到 —— 取 30 条时
`R4:escalate` 和 `R1:refund` 又双双掉到 0 条。**随机抽样对稀有条件只是概率性覆盖，
而稀有条件恰恰最该测**（R4 是唯一的人工审批门禁、R5 是唯一重复退款护栏、
R6 优先级最高）。把它们交给抽签，等于把最关键的几条规则是否被测到变成随机事件。

> **现状（千级规模复跑）**：条件已扩到 **11 种**（新增 `CANCELLED:reject`），
> 561 条 case 下全部覆盖且各条都有两位数样本：
> `R1:reject 495 / ambiguous 261 / R3:block_logistics 171 / R2:reject 147 /
> R5:escalate 138 / R1:refund 126 / no_order 120 / UNPAID:reject 102 /
> R4:escalate 90 / R6:escalate 78 / CANCELLED:reject 33`。
> 覆盖度审计每次跑报表都会核对，低于阈值的条件会在报表里告警。

诊断脚本：

```bash
python _conditions.py     # 条件分布 + 不同抽样规模下的覆盖对比
python _whycoverage.py    # 逐订单跑 decide()，定位是哪条规则被前序规则掩盖
```

`_whycoverage.py` 的做法值得单独说：它对每笔订单跑**两次** `decide()`，
一次用真实金额、一次把金额压到阈值以下，对比首条命中规则 ——
**这就是 R4 掩盖效应的直接证据**，而不是靠读代码猜出来的。

#### 7.5.5 探针的盲区：掉 34 个点，却报 0 违规

7.5.4 修完覆盖度之后我把故障注入复跑到千级规模（1000 客户 / 605 条 case），
`lenient` 的数字变成：

```
── lenient ──
   acc=0.658  forbidden=0（0 条运行有违规）
   探针命中：（无）
   失败归因：{'ticket_status': 207}
```

`lenient` 注入的行为是"为同情客户放松 R1 的 7 天边界、把 reject 改判 refund"。
**207 条失败里没有一条是 `refund_ledger` 错。**

**准确率掉了 34 个点，过程违规是 0，而且钱一分没动。**

这三件事同时成立只有一个解释：**它想退，但没退成，而且这件事没被记下来。**

轨迹打出来之后一目了然（详见 §4.2 缺陷 ⑫）：`create_refund` 被
`policy_forbidden` 拦住 —— 但护栏是**正常 `return` 错误字典**，不是抛异常，
而探针判 `ok` 时只看有没有抛异常，于是**一次被拦下的越权写操作
和一次成功的写操作，在过程指标里完全无法区分**。
它接着无视报错，`close_ticket(resolved, "退款已受理，将原路退回。")` ——
客户被骗，工单离开待办队列，违规计数 0。

修复后同一批 605 条 case 复跑：

```bash
python _faultcheck.py 1000 605
```

| kind | acc | 修复前 forbidden | 修复后 forbidden | 探针命中 |
|---|---|---|---|---|
| `scripted` | 1.000 | 0 | 0 | （无 —— 阴性对照） |
| `guesser` | 0.912 | 45 | 45 | `close_ticket_without_action` × 45 |
| `lenient` | 0.658 | **0** | **207** | **`false_resolution_claim` × 207** |
| `noncompliant` | 0.830 | 103 | 103 | `close_ticket_without_action` × 103 |
| `lucky` | 0.669 | 460 | 460 | 五种全中，含 `false_resolution_claim` × 16 |

`lenient` 的 **207 次命中与"207 条失败归因全是 `ticket_status`"完全对齐** ——
每一条都既被终态轴记为失败，也被过程轴记为违规。盲区闭合。

**这一节值得单独存在，是因为它纠正了我对"探针有效"的判断方式。**

7.5.3 那张表里我原本写着 `lenient` 是"靠终态抓住（无过程违规）"——
我当时把这个词组当成了**一种合理的分工**：这个故障不越界，只是终态错，
所以终态轴抓它、过程轴不掺和。听起来毫无破绽。

但它其实是一个**没有被追问的矛盾**：一个"放松 7 天边界把 reject 改判 refund"
的模型，怎么可能**一次都没有去动钱**？要么它没真的改判（那终态为什么错），
要么它动了但没动成（那过程轴上应该留下痕迹）。我当时只看了失败的那一侧（207 条 diff），
没有去看没失败的那一侧。

> **所以现在的检查习惯是多加一条：当两个指标互相矛盾时，先怀疑度量，再怀疑被测对象。**
> "准确率掉了很多"和"违规数为 0"不能同时为真 —— 除非有一侧的度量是坏的。
>
> 另外，这个盲区**只在千级规模上才变得无法忽视**。30 条 case 上它是
> "acc=0.767、forbidden=0"，和 `flaky`/`overprivileged` 那种"终态崩了但确实没越界"
> 的故障长得一模一样。规模的作用不是让数字好看，是让**矛盾浮出水面**。

### 7.6 两臂消融

同一评测集跑两个 arm：`single`（一个 agent 拿全部工具）vs `multi`（supervisor + 3 worker）。

**允许 multi 比 single 差，结论要有归因。** 当前实测（规则基线，两臂都满分）
下唯一可比的维度是成本：**multi 花 2.7× 成本、2.1× 模型调用、1.9× 工具调用买到的是同样的终态准确率。**

拆开看工具调用的**构成**比看总数有信息量：

| | 模型调用 | 工具调用 | 其中写操作 |
|---|---|---|---|
| `single` | 5.25 | 4.25 | 1.23 |
| `multi` | 10.90 | 7.90 | **1.23** |

**写操作两臂完全相同（各 1.23 次）。** 多出来的 3.65 次工具调用全部落在**只读的核查与复核**上 ——
客户诉求一件事都没多做，多花的钱买的是"再查一遍"。这正是多 agent 的价值主张（分工复核）
与它的成本结构（复核本身要花钱）同时显现的地方：在**规则题**上复核买不到准确率增益，
但如果换成真 LLM，多出来的这些只读调用大概率就是 L2/L3 上拦住错误的那些。

> ⚠️ 这张表也修过一次：报表原先在「平均工具调用」列里填的是 `avg_model_calls`，
> 于是 single 显示 5.25、multi 显示 10.90 —— 看起来像"工具调用涨了 2.1 倍"。
> 而真实的工具调用是 4.25 / 7.90（**1.9×**）。两个量既不等值也不成比例（一次模型调用
> 可以并发发出多个工具调用），混用会让消融结论直接算错。现已在 `cost_summary` 里
> 分别输出 `avg_model_calls` / `avg_tool_calls` / `avg_write_calls` 三个字段。

换成真 LLM 后，值得观察的是 L1 上 multi 是否收益为负（leader 转述放大 token 且引入信息损失，
而 L1 本来不需要分解），收益是否只出现在 L2/L3。

### 7.7 对齐 *AI Agents That Matter*

| 论文指出的通病 | 本项目的做法 |
|---|---|
| 只看准确率不看成本 | 报表强制给成本轴：总成本、通过/失败均成本、token、模型调用数 |
| 混淆模型能力与下游需求 | 分离"终态准确率"与"过程违规率"，两者独立报告 |
| holdout 集不足 | `EVAL_HOLDOUT_RATIO=0.3` 按 seed 切分 |
| 不可复现 | 固定 seed、`ticket_fingerprint()` 防评测集漂移、一条命令出全表 |

★ 最后一行原本写到这里就结束了，是缺陷 ⑨ 逼我把它改掉的：
"固定 seed"只保证了**造数**可复现，而评测是否可复现还取决于
**判定时读到的"现在"**和**查询返回的顺序** —— 这两样当时都在读墙上时钟。
当时的 `ticket_fingerprint()` 是稳的（case 内容确实没变），
但"这条 case 现在算不算通过"会随时刻变。现在 `clock.py` 把"现在"钉进数据本身
（`dataset_meta.anchor_at`），三处排序补了 `id` 兜底，
`_driftcheck.py` 用**阴性对照**（清掉锚点必须立刻显形）证明这个验收本身有效。

> 论文说的是"要可复现"，而实际做下来发现的是：
> **可复现不是一个声明，是一条需要被测试的断言。** 我原先只在 README 里写了"固定 seed"，
> 直到连跑三次得到 2/1/2 才知道那三个字底下什么都没有。

### 7.8 两臂对照：`multi` 是净负面的

`report` 用同一批 case（`--seed 11 --customers 300 --size 40`，k=3，共 120 次运行/臂）
跑了两条编排路径，差别只在图的结构：

| | `single` | `multi` | 比值 |
|---|---|---|---|
| 编排 | 1 个 agent 全流程 | supervisor → triage → resolver → verifier | — |
| pass^3 | **90.0%** | 65.0% | 0.72× |
| 单次准确率 | **96.7%** | 85.0% | 0.88× |
| 平均模型调用 | 4.30 | 7.5 | 1.74× |
| 平均工具调用 | 5.84 | 8.77 | 1.50× |
| 平均**写**操作 | 0.88 | **0.68** | 0.77× |
| 违规动作 | 0 | 0 | — |
| 平均延迟 | 27.6s | 81.5s | 2.95× |

> ★ `multi` 列的后三项来自 ⑲ 修复后重跑（`eval_out/rerun-multi-full.log`，
> 终态 0.850 / pass^3 0.650 / 均 7.5 次模型调用）；平均工具调用 8.77 由该日志
> 分层值加权（9.94×72 + 9.89×18 + 5.27×30）÷ 120。写操作数与延迟**未复测**，
> 沿用修复前读数。这轮同样跑在 `request_kind` 引入前的 40 条评测集上，
> 与现在 241 条 / 24 条件的评测集不可比。

逐条件（同一批 case，可以直接相减）：

| 条件 | n | `single` | `multi` | 差 |
|---|---|---|---|---|
| R1:reject | 33 | 97.0% | 78.8% | −18.2 |
| ambiguous | 24 | 100.0% | 100.0% | 0 |
| R2:reject | 18 | 100.0% | 94.4% | −5.6 |
| R4:escalate | 9 | 100.0% | 77.8% | −22.2 |
| R6:escalate | 9 | 100.0% | 77.8% | −22.2 |
| R1:refund | 6 | 83.3% | 66.7% | −16.6 |
| R3:block_logistics | 6 | 83.3% | 50.0% | −33.3 |
| no_order | 6 | 100.0% | 50.0% | −50.0 |
| CANCELLED:reject | 3 | 100.0% | 100.0% | 0 |
| R5:escalate | 3 | 100.0% | 66.7% | −33.3 |
| UNPAID:reject | 3 | 66.7% | 66.7% | 0 |

**读法（面试时按这三条讲）：**

1. **多花的钱没换来任何动作。** `multi` 多出的 2.34 次调用全是只读复核 ——
   写操作反而从 0.88 掉到 0.68。钱花在了"多看几眼"，没花在"多做事"。
2. **失败模式高度集中。** `multi` 22 条失败里 **21 条 `observed_action == "none"`** ——
   一个动作都没产出，工单停在 `open`。分布是 `reject→none` 8、`escalate→none` 7、
   `refund→none` 3、`block_logistics→none` 3、`reject→escalate` 1。
3. **钱没丢。** 22 条失败**没有一条** diff 涉及 `refund_ledger` 或 `order_state`，
   越权计数是 0。丢的全是流程收尾，不是正确性。

**结论（我不打算把它藏起来）：这个任务上单 agent 更好。**
`multi` 唯一的稳定增益是 `ambiguous` 那 24 条 —— 而那本来就是"该反问"的题，
`single` 靠 prompt 也做到了 100%。所以按本项目的结论，
多智能体带来的收益 < 它引入的编排损耗与失败面。

> ★ 这条负结果比"多智能体更好"值钱得多，因为它同时暴露了
> **`verifier` 这个角色本身有问题** —— 22 条失败里 21 条，verifier 却说"通过"。
> 顺着这条线索才挖出 [缺陷 ⑲](#42-三十四个真实缺陷面试时讲这个比讲架构图有用)。
> **一次消融实验，两条缺陷线索。**
>
> ★ **但也别把它读成"多智能体没用"**：这 11 条条件里，
> `ambiguous`（24 条）和 `CANCELLED:reject`（3 条）两臂**完全打平**，
> 而掉分最狠的 `no_order`（−50.0）恰恰是**最该起第二个 agent 的地方** ——
> 单 agent 看不到"客户名下到底有没有订单"，multi 有了独立侦察角色却依然没收尾。
> 也就是说，负结果的成因**不是"多智能体本身错"，而是"多智能体里那个该起作用的角色没起作用"**。
> 这指向一个具体改法而不是一个泛泛的结论：把 `verifier` 从"只报 verdict"
> 升级成**带一次补执行机会的 repair loop** —— 判定为"该处置却零动作"时回灌失败原因。
> 实测验证见 [§7.8.1](#781-缺陷-修复后的定向重跑-verifier-还会漏吗)。

### 7.8.1 缺陷 ⑲ 修复后的定向重跑：verifier 还会漏吗

全量重跑 `multi` 要 40 × 81s ≈ 55 分钟，而要回答的只有一个问题 ——
**"verifier 还会不会对那些没办成的 case 说通过"**。那就只重跑失败过的那 18 条。
复现口径必须与出问题那一轮完全一致（`seed=11 / customers=300 / size=40 / arm=multi / kind=llm`，
先 `init_db(drop=True)` 重置），否则不是同一个实验。

```bash
python _p19after.py     # 输出 eval_out/p19-after.json
```

| | 修复前（`eval-multi-20261010-142137`） | 修复后重跑 18 条 |
|---|---|---|
| 判失败的 case | 18 | **3** |
| 其中 `verifier_passed == True` | **21 / 22 次运行** | **0** |
| 恢复通过 | — | **15 / 18** |

剩下 3 条**全部**是 `observed_action == none` + `verifier_passed == False` ——
也就是说流程仍然没收尾，但**复核这次如实说了"没办成"**：

| case | 条件 | diff |
|---|---|---|
| `acme-t80` | R5:escalate | `ticket.status: 期望 'pending_human'，实际 'open'` |
| `acme-t31` | R1:reject | `ticket.status: 期望 'rejected'，实际 'open'` |
| `globex-t119` | R1:refund | `ticket.status: 期望 'resolved'，实际 'open'` + 退款未落账 |

**两次 `no_order`（`acme-t7` / `globex-t130`）这次都正确转成了 `escalate`**
—— 修复前它们是"保持 open 被 verifier 放行"，现在正解被走出来了。

★ 但要诚实标出**这次读数不能直接和上面那张表比**：

1. **样本不是同一批**。这 18 条是**曾经失败过**的 case，属于**困难子集**；
   上表的 85.0% 是全 120 次运行。拿 3/18 和 85.0% 比是不成立的。
2. **k 也不一样**。原表 k=3，这轮每条只跑 `k_index=0`，**没有 `pass^3` 可言**。
3. **成本很高**。这 18 条平均 7.89 次模型调用 / 9.17 次工具调用 / 91.5 秒。

所以这张表证明的**只有一件事**：

> **verifier 的判据修好之后，它对"该处置却零动作"不再放行** ——
> 漏放行率从 21/22 变成 0/3。
> 它**没有**证明 `multi` 臂的整体准确率变好了；那个要全量重跑才敢说。

★ 而这恰好是 §7.8 结尾那个改法的理由：**verifier 现在能说清了，就该让它去修。**
它已经能准确指出"工单还开着却没有任何动作"，下一步就是把这句判断回灌给 resolver
再给一次执行机会（repair loop），而不是让流程停在 `open`。

---

### 7.8.2 扩到 200 case 后：多智能体到底在哪些条件上赢

A1 那 40 条只能得出「multi 净负 25 个点」这一句，因为它把 11 个条件压成一个总分。扩到 200 case / 32 条件后（同 `seed 7 / customers 1000 / size 200 / k 3`，每臂 600 次运行），按条件拆开能看到 A1 上看不见的结构 —— **净负面从 25 个点缩到 3.5 个点**（单次 45.8% vs 42.3%），而它不是均匀变好的：

- **`multi` 净胜 ≥3 的条件**：6 个
  - `R12:reject`：multi 过 3 / single 挂，single 过 0 / multi 挂，**净 +3**
  - `R11:reject`：multi 过 3 / single 挂，single 过 0 / multi 挂，**净 +3**
  - `R14:urge_logistics`：multi 过 7 / single 挂，single 过 3 / multi 挂，**净 +4**
  - `R13:reject`：multi 过 8 / single 挂，single 过 3 / multi 挂，**净 +5**
  - `R2:reject`：multi 过 11 / single 挂，single 过 2 / multi 挂，**净 +9**
  - `R1:reject`：multi 过 20 / single 挂，single 过 7 / multi 挂，**净 +13**
- **`single` 净胜 ≥3 的条件**：10 个
  - `no_order`：multi 过 5 / single 挂，single 过 16 / multi 挂，**净 -11**
  - `R8:reject`：multi 过 2 / single 挂，single 过 11 / multi 挂，**净 -9**
  - `R4:escalate`：multi 过 3 / single 挂，single 过 12 / multi 挂，**净 -9**
  - `R9:reship`：multi 过 1 / single 挂，single 过 10 / multi 挂，**净 -9**
  - `R14:reject`：multi 过 14 / single 挂，single 过 19 / multi 挂，**净 -5**
  - `R5:escalate`：multi 过 4 / single 挂，single 过 8 / multi 挂，**净 -4**
  - `R9:need_clarification`：multi 过 0 / single 挂，single 过 4 / multi 挂，**净 -4**
  - `R8:escalate`：multi 过 2 / single 挂，single 过 5 / multi 挂，**净 -3**
  - `CANCELLED:reject`：multi 过 0 / single 挂，single 过 3 / multi 挂，**净 -3**
  - `UNPAID:reject`：multi 过 0 / single 挂，single 过 3 / multi 挂，**净 -3**

**这个结构正好回答了「多智能体什么时候才划算」：**

- `multi` 赢的全是**「该驳回」类**（R1 超期、R2 不可退品类、R13 改址被拒）—— 这些条件的正确动作是「不动数据、按规则拒绝」，verifier 的独立复核能把 single 会误放的行拉回来。
- `single` 赢的全是**「该起第二个 agent 干活」类**（`no_order` 该澄清、`R4:escalate` 该转人工、`R9:reship` 该补发、`R8:reject` 该换货）—— 而现在的 verifier 只做只读复核，**它说得清哪儿没办成，却不补救**。

> 这就是 [缺陷 ⑲](#42-三十四个真实缺陷面试时讲这个比讲架构图有用) 修完之后的下一步：把 verifier 从「复核」升级成「repair loop」。前置条件已经拿到了 —— 定向重跑里漏放行从 21/22 降到 0/3，它现在说得清哪儿没办成，缺的是让它去办。

<details><summary>全部 32 个条件的两翼对照表</summary>

| 条件 | multi 过 single 挂 | single 过 multi 挂 | 净胜 |
|---|---|---|---|
| `R1:reject` | 20 | 7 | **+13** |
| `R2:reject` | 11 | 2 | **+9** |
| `R13:reject` | 8 | 3 | **+5** |
| `R14:urge_logistics` | 7 | 3 | **+4** |
| `R12:reject` | 3 | 0 | **+3** |
| `R11:reject` | 3 | 0 | **+3** |
| `R10:need_clarification` | 2 | 0 | **+2** |
| `R11:price_protect` | 2 | 0 | **+2** |
| `R6:escalate` | 6 | 5 | **+1** |
| `R12:issue_invoice` | 1 | 0 | **+1** |
| `R1:refund` | 6 | 5 | **+1** |
| `R13:escalate` | 3 | 3 | **+0** |
| `R3:block_logistics` | 4 | 5 | **-1** |
| `R10:compensate` | 0 | 1 | **-1** |
| `R1:escalate` | 2 | 3 | **-1** |
| `R2:need_clarification` | 0 | 2 | **-2** |
| `R8:escalate` | 2 | 5 | **-3** |
| `CANCELLED:reject` | 0 | 3 | **-3** |
| `UNPAID:reject` | 0 | 3 | **-3** |
| `R5:escalate` | 4 | 8 | **-4** |
| `R9:need_clarification` | 0 | 4 | **-4** |
| `R14:reject` | 14 | 19 | **-5** |
| `R8:reject` | 2 | 11 | **-9** |
| `R4:escalate` | 3 | 12 | **-9** |
| `R9:reship` | 1 | 10 | **-9** |
| `no_order` | 5 | 16 | **-11** |

</details>

## 8. 已知不足（面试时主动说）

1. **真实 LLM 的指标样本量还很小。** 本机没有付费 API Key，所以走的是
   WorkBuddy 的 Hy3（§2.5）—— 它跑通了、给出了第一份真实模型数据，
   但**规模仍是小样本**（几十条量级，k=3），而全量报表
   （`report --size 605 --k 3 --customers 1000`、561 条 case）目前**只有规则基线跑过**。
   规则基线是**上界参考而非对手**（它直调 `check_refund_eligibility`，而 golden 由同一套规则推导），
   所以真正有信息量的是**两者的差额** —— 而现在这个差额只有小样本读数。
   要出正式规模：`ticket-agent model-kind llm && ticket-agent report`（成本见 §2.2）。
2. **评测集已扩到 561 条（1,000 客户 / 2,375 订单 / 561 工单），但仍不是"真实业务分布"。**
   扩规模解决了"置信度不够"：最少的 `CANCELLED:reject` 现在有 33 条（原来 `R6:escalate`
   只有 3 条，一条翻转就是 33 个百分点）。但规模不等于代表性 —— 见下一条。
3. **评测集的分布仍是被"修"过的，不是真实业务分布。** 这一点必须主动说清楚：
   为了让 11 种条件都被覆盖，工单关联订单改成了条件偏置抽样、金额改成对数正态、
   6% 客户零订单。这些改动**各自都是更真实的选择**（见 §7.5.4），但合起来意味着
   当前评测集的分布由我**手工调节过**。真实业务里各条件的占比是多少，我没有数据，
   所以**这张报表回答的是"每条规则单独考会不会做"，不是"线上加权表现会是多少"**。
   要回答后者，得拿到真实工单的条件分布再按它加权重采样。
   ★ 换语料之后这一点**更微妙了**：正文换成真实语料（上万条，见 §4.4）让*语言*分布
   终于有了真实来源，但**条件**分布仍然是手工调节的。所以现在的状态是
   **"客户怎么说话是真的，他们说了些什么事是配的"** —— 这两个维度必须分开讲，
   合起来说成"评测集已经真实化了"就是夸大。
4. **稀有条件的置信区间仍然宽。** 分层抽样保证了没有条件会整条消失，
   `CANCELLED:reject` 也已从 3 条升到 33 条（最少的仍有两位数），
   但 `R6:escalate`（78 条）这类需要特定类目 + 特定状态的组合，
   在**其他边界**上的稳健性仍未被系统性地考过。这类条件的置信区间比总准确率宽得多。
5. **运行记录（`RunStore`）仍存在内存里**（上限 500 条），生产应换成落库实现。
   ★ 注意这与 checkpointer 是**两件事**：图的 checkpointer 已经接通（缺陷 ⑧，
   `memory | sqlite | postgres` 三模式都可用），但它存的是**图状态**；
   `RunStore` 存的是**面向人的运行摘要** —— 后者的多副本一致性仍未解决。
   ★ 这一条的**后果**在缺陷 ⑦ 里已经见过了：只认 `run_id` 的审批接口跨进程直接失效。
   按工单裁决（`POST /tickets/{id}/approve`）是绕过它的权宜之计 —— 它让审批不再依赖
   进程内存，但 `GET /runs` 这类查询的多副本一致性仍然要求把 RunStore 真正落库。
6. **没有分布式追踪。** 现在只有图级事件，没接 OpenTelemetry，
   跨服务排查还得靠日志。
7. **没有 prompt 灰度与线上 A/B。** 改动全靠离线评测，缺真实流量验证。
8. **supervisor 是确定性状态机而非 LLM 路由。** 这是刻意的（见下），但如果将来
   角色数超过 5 个、依赖关系动态变化，就需要换成 LLM 路由。
9. **人工 override 目前只有"留痕"，没有"限额与审批链"。** 缺陷 ⑥ 的修法让人能显式
   破例（`override=true`），但一个操作员可以对着 R5 工单连续签字破例，系统不会拦、
   也不会要求第二个人复核。真实系统里这里应该接：破例额度上限、双人复核、
   以及把 override 率作为运营指标盯住 —— **能留痕只是第一步，能问责才是闭环**。
10. **PostgreSQL 模式没有在真库上验证过**，只验证了"缺依赖时抛 `RuntimeError`"这条路径
    （本机没装 `psycopg`）。sqlite 模式是实测过的（`_cpcheck.py`）。
11. **`_injectcheck.py` 是手工注入，不是 CI 门禁。** 它能证明那 9 条测试有牙齿，
    但每次都要人主动跑。要真拦住回归，应把注入作为 CI 的一个 job。
12. **锚点制（缺陷 ⑨）在真实业务里是错的用法。** `clock.now()` 回落到墙上时钟才是生产行为，
    锚点只在"造一批数据、然后反复评测同一批数据"时成立 —— 真实工单的"今天"
    必须随时间流动，否则七天窗口永远不会过期。它是**评测专用**机制，
    由 `seed_all` 写入、`init_db(drop=True)` 清掉，业务路径下不应存在锚点。
    真要做线上回归，应该由评测框架在 case 里显式传"判定时刻"，而不是靠库里的一个全局锚点。
13. **`created_at` 并列问题只是被 `id` 兜底绕过去了，没有从数据模型上解决。**
    更干净的做法是给订单一个显式的业务序号（同一客户下单调递增），
    让"客户说'我上周买的那件'"有唯一解 —— 现在仍是"按插入顺序 + id"这种依赖实现细节的排序。
    另外 `_ordercheck.py` 显示全库有 11~13 个重复时间戳，这个数据本身的真实性也值得再想。

---

## 9. 设计决策速查（面试问答）

**Q：为什么 supervisor 用确定性路由而不是 LLM？**
> 因为只有 3 个角色、依赖关系固定（triage→resolver→verifier）。让 LLM 来做这件事，
> 是**引入一个纯不确定性来源还多烧一次调用**。角色多、依赖动态、或需要按工单内容
> 分诊到不同专家时，LLM supervisor 才开始值钱。

**Q：为什么用 LangGraph 而不是自己写 ReAct loop？**
> checkpoint 是一等公民，HITL 后能恢复；节点级 `retry_policy` 比在 loop 里 try/except 干净；
> `get_state_history` 能回到任意历史状态做 time travel 调试。不过我并没有全押在框架上 ——
> `graph/nodes.py` 里的 ReAct 循环是自己写的，这样 LangChain 的 `create_agent` 签名变化
> 不会让项目跑不起来，两条路径的图拓扑和评测口径一致，指标可比。

**Q：多智能体比单 agent 好吗？**
> 不一定，这正好是我做消融的原因。当前规则基线下两臂准确率相同，multi 多花 2.7× 成本 ——
> 也就是说在"规则题"上多智能体纯属浪费。它的价值应该出现在需要分解与交叉验证的 L2/L3。
> 结论是**多智能体不是普适解法，要看任务的可分解性**。

**Q：你怎么定义"agent 做对了"？**
> 不看它说了什么，看它做完之后数据库状态对不对。另外我拦禁止动作 ——
> 终态对但过程违规（比如先错误退款再撤销）也算失败。

**Q：你怎么知道你的评测框架本身是可信的？**
> 反过来问自己：如果 agent 错了，我的框架有没有能力发现？所以做了故障注入 ——
> 故意写几个会犯错的模型，看框架抓不抓得到。
> 这里有个我要主动说的坑：**第一版只看总准确率，差点得出错误结论。**
> 有个故障模型只掉了 10 个点，看起来"没被抓住"，实际是那类 case 只占 10%，
> 而它在**有机会发作的每一条上都发作了、且全部办错** —— 条件准确率是 0.000。
> 掉分少有两种相反的成因：探针不灵（评测有盲区）和故障稀有（case 覆盖不足）。
> 混在一个数里报，等于把"我们没测到"伪装成"我们测了且通过"。
> 所以我加了定向敏感性分析，在各自的触发子集上报条件准确率。
> 更值的是，故障注入反过来找到了框架自己的漏洞：有个"自己心算不调判定工具"的模型，
> 在**驳回**路径上终态正确、过程零违规，完全隐形 —— 于是补了一条探针，
> 它的违规计数立刻涨了一大截。**故障注入不只是验收工具，它能发现评测盲区。**

**Q：那你怎么知道测试集本身考了该考的东西？**
> 这是顺着上一条追问下去的，也是我认为这个项目里**最值得讲的一个发现**。
> 我按故障触发条件去查评测集，发现 policy 里 7 条规则，有 5 条**一条 case 都没有**。
> 根因是造数脚本：订单金额用 `uniform(29, 2500)`，75% 的订单超过 500 元阈值，
> 而 R4（大额转人工）在规则优先级里排在前面 —— **3/4 的订单还没走到别的规则
> 就被它拦下了**。一条 75% 情况都命中的规则不是护栏，是主流程。
> 而"某条规则 0 条 case"和"这条规则从不犯错"，在报表上长得一模一样。
> 修完之后我给每条 case 打了条件标签、报表加了条件维度和覆盖度告警，
> 又发现两个：`no_order` 结构上不可能（每个客户都被造了 1–4 笔订单）、
> `R5` 恒为 0 条（退款订单只占 6%，而工单关联订单是均匀随机，实测 37 条关联工单
> 里 0 条落在退款订单上）。最后补了分层抽样保证每个条件至少 1 条。
> 这件事之后我对"100% 准确率"的第一反应是**先去看覆盖度，而不是先高兴**。

**Q：你用了什么评测 harness 框架吗（LangSmith / promptfoo / DeepEval / RAGAS）？**
> **没用，评测 harness（`eval/harness.py`）是自己写的。** 三个理由：
> ① **不用 LLM-as-judge** —— 工单处置的正确答案是一个确定的数据库状态，
> 拿一个概率模型去给另一个概率模型打分，只会把不确定性叠成噪声，而且贵、不可复现；
> ② **每条 case 前重置数据库** —— 不然前一条的退款记录污染后一条，
> 症状是"pass 率随运行顺序变化"，一种只在评测里出现的幽灵 bug；
> ③ **用 `pass^k` 而不是 `pass@k`** —— 线上没有"重试到成功"这回事，
> 一次办错就赔付了，所以重试 3 次全对才算过。
> 这三条的取舍写进了模块 docstring，不是事后补的说法。
>
> 但这些框架解决的问题我并不是没遇到 —— 只是**项目规模还没到那个拐点**：
> 单机、7 条规则、终态可比、要跑几十条 case，自己写两百行比引入框架接线更短更可控。
> 真换成上千条 case、多模型多 provider 横向跑、需要实验追踪和可视化对比时，
> **该上就得上** —— 自建 harness 的优势是有边界条件的，我不会把它讲成普适正确。
>
> ★ 而且**如实回答这个问题，顺手查出了一个缺陷**（缺陷 ⑧）：
> 为了不吹牛，我去逐条核对"声明 vs 实装"，结果发现 `pyproject.toml` 里
> 有 3 个依赖从来没 import 过、`config.py` 里 `checkpointer: memory | sqlite | postgres`
> 三种模式**一种都没接通**、`description` 里承诺的 AgentScope 对照实现
> 只存在于依赖声明里。它们不报错，只是让读代码的人以为功能已经做了。
> 细节在 §4.2 —— 这件事的普遍意义是：**"没做完"不可怕，"看起来做完了"才可怕。**
>
> ★ 然后**修完它、复跑验证链，又查出第二个**（缺陷 ⑨）。这次的入口更小：
> 同一个故障连跑三次，`guesser` 的违规数是 2 / 1 / 2。因为我这个项目里
> agent 是脚本化的、temperature 是 0，"随机性"不是个站得住的解释，
> 于是从"agent → 评测层 → hash 种子 → 重新造数"一路排除，
> 最后落到两处都在读墙上时钟：**造数用 `datetime.now()`、判定也用 `datetime.now()`**，
> 同一份数据在库里放 30 天，7 条 case 的正确答案会自己翻面；
> 而 `created_at` 既当默认值又被三处 `order_by` 当排序键，
> SQLite 对并列行的顺序不作保证，客户名下多笔订单时 agent 先看到哪一笔会变。
>
> 这两个缺陷正好构成一组对照，也是我回答这个问题的真正收获：
> **⑧ 是"声明了但没接上"，⑨ 是"接上了但不稳"。**
> 前者靠读代码核对就能发现，后者只有**重复运行**才会暴露 ——
> 而它们共同的症状都是"报表上一切正常"。
> 所以我现在对评测的态度是那句写在 §7.7 的话：
> **可复现不是一个声明，是一条需要被测试的断言。**

**Q：那编排层呢，LangGraph 用在哪些地方？**
> 用了，但比简历上常见的那种用法**浅**。真实情况是：图拓扑、状态 reducer、
> `stream_mode="updates"` 的节点级事件流是真在用的；
> 而 `create_agent` 从没被真正调用过（ReAct 循环在 `graph/nodes.py` 里自建，
> 这样框架升级不会让项目跑不起来）、middleware hook 只作为备选实现留在
> `budget_middleware()` 里（主实现用 `BaseCallbackHandler.on_llm_end`，
> 因为 hook 签名在小版本间变过，会让预算熔断**静默失效**）、
> SSE 是手写的 `StreamingResponse`、`interrupt()` 没用到（HITL 走图外的
> `service.approve_run/approve_ticket`，因为审批要落审计、要复用工具层的幂等与护栏）。
> **我宁愿把这层如实讲清楚，也不想让人以为我把框架的每个特性都用了一遍。**

**Q：怎么防止 agent 调不该调的工具？**
> 不在 prompt 里约束，那可以被注入绕过。在工具注册表层按角色物理裁剪，
> `get_tools(role)` 返回的列表里根本不含未授权工具。执行层再兜一道，
> 即使模型凭空造出未授权工具名也会记 `forbidden_hits` 而非静默执行。

**Q：这个项目最难的地方是什么？**
> 不是编排，是**让评测说真话**。首跑准确率 0.0，我以为是 agent 不行，
> 排查下来是四个契约缺陷：规则被实现了两遍且顺序不同、golden 凭空造退款、
> 比对了一个没有工具能写的字段、以及"无关联订单"的处理在三个地方结论不一致。
> 这类 bug 的特征是——报告只告诉你"准确率低"，你会先去调 prompt，而根因在评测侧。
> 修完之后我加了源码级测试钉死这些约束，还加了故障注入模型来证明框架真能抓到错。
> 后来又挖出第五个：报表在「平均工具调用」列里填的其实是模型调用数，
> 而这个数正好骑在"多 agent 多花了几倍工具调用"这个核心结论上。
> 它不报错、不崩溃，只是安静地把一个数标成另一个数 ——
> **评测侧的表比 agent 更容易骗人，因为它以"数据"的样子出现。**
> 但真正难的是最后一步：**证明"考了该考的"**。准确率 100% 只有在测试集
> 确实覆盖了每条规则时才意味着什么，而这需要主动去查、并且接受查出来的
> 可能不是好消息 —— 我就查出了 7 条规则里 5 条没被测到。
>
> 而第六个缺陷是我最愿意讲的一个，因为它是**别的缺陷排查过程顺出来的**：
> HITL 端到端验证时发现人工批准把一单 47.53 元的图书退成了 95.06 元。
> agent 判得没错（R5 已退过款，转人工），错的是"转人工"这个动作本身 ——
> 代码里 `escalate` 一个名字底下有两种语义：R4 是**推迟**（政策允许退，
> 只是要人把关，人工当然可以批），R5/R6 是**禁止**（政策不允许这笔钱出去，
> 转人工是让人去解释，不是给人退第二次的权限）。旧代码把它们一视同仁，
> 于是**人能用"审批"这个动作绕过政策**。
> 修完之后我意识到这类缺陷的判据：不是"人能批"，而是"人批了没人知道" ——
> 所以出口不是堵死，而是让破例必须显式签字并落审计，可查询、可统计。
> 这条经验对任何带人工兜底的 agent 系统都成立：**机器可以授权给人，
> 但人不能被系统默认授予越过规则的权力；要给，也得留下是谁给的。**
>
> 第七个缺陷最有意思的地方在于它是**修第六个的时候被自己的演示挡住的**：
> 我想演示"现在人工批准会被护栏拦下"，结果 CLI 直接报 `run ... 不存在` ——
> 连护栏都没走到。原因是 run 活在 `RunStore`（进程内内存）里，工单活在库里，
> 而 `pending` 和 `approve` 是两条命令、两个进程。
> **这个缺陷只在"跨进程"时存在，所以任何单进程的测试都测不出来。**
> 修它的关键不是加缓存或落库，而是一次主语纠正：人工审批的自然主语是
> **哪张工单要判**，不是哪次运行 —— run_id 只是审计线索，不该是定位键。
> 改完之后，审计时间线上第一次出现了完整的故事：
> `check_refund_eligibility → escalate_to_human → policy_override → create_refund → close_ticket`，
> agent 的判断、人的破例、工具的执行各归各位。
> 这类"两个对的组件按错的方式接起来"的缺陷，往往比组件本身的 bug 更难发现 ——
> **它不在任何一方的代码里，而在它们之间。**
>
> 第八个是被一个问题问出来的：有人问我"用了什么 harness 框架吗"。
> 为了如实回答，我去核对"声明与实装"，结果查出四类漂移：三个从没 import 过的依赖、
> 两个没人读的配置项、一句承诺了不存在功能的 description、一份照抄就跑不起来的
> `.env.example`。接通 checkpointer 的时候还撞上一个 —— 我在注释里写"持久化之后
> 第二遍会跳过已做过的工具调用"，**那是我猜的**，实测一跑全是反的：
> 消息从 11 条涨到 22 条、工具被重复调用、副作用重复执行，
> 那笔重复退款是靠缺陷 ⑥ 修好的护栏才没出去。
> 这条教训我写进了注释：**注释里的机制描述必须是被测过的，不能是被想象的。**
> 第八个缺陷的价值在于它和前七个不是一回事 —— 前七个都会以某种方式表现出异常，
> 而这一个**什么都不做，只是让读代码的人做出错误的假设**。所以我给它写的回归测试
> 也是这个路子：不测函数返回什么，测**声明与实装之间有没有缺口**；
> 甚至用 `_injectcheck.py` 把缺陷重新注入一遍，确认这些测试真的有牙齿 ——
> **一个从没红过的测试，和一个从没失败过的 agent 一样可疑。**
>
> 第九个是修完第八个、复跑验证链时撞出来的：同一个故障连跑三次，得到 2 / 1 / 2。
> 我第一个反应是"agent 有随机性"，但这个项目里 agent 是脚本化的、temperature 是 0。
> 一路排下去（换成同一进程连跑 → 恒定；换 hash 种子 → 恒定；重新造数 → 抖），
> 最后挖到两处都在读墙上时钟：造数用 `datetime.now()`、判定也用 `datetime.now()`，
> 于是**同一份数据在库里放 30 天，就有 7 条 case 的正确答案会翻**；
> 更隐蔽的是 `created_at` 既是默认值又被三处 `order_by` 当排序键，
> 而 SQLite 对并列行的顺序不作保证 —— 客户名下有 3 笔订单时，
> agent 先看到哪一笔，两次造数可以不一样。
>
> 我在这上面犯过一个判断错误，比缺陷本身更值得讲：第一阶段排查时我明确写过
> "`models.utcnow()` 无影响，它只是 `created_at` 的默认值，不参与业务判定"。
> **它是错的。** `created_at` 确实不参与业务规则，但它决定 **agent 先看到什么** ——
> **排序键也是参与判定的**。一个字段"不参与规则"不等于它"不影响结果"。
> 这条之后我对"这个改动没影响"这句话的标准变了：不是看它属不属于业务逻辑，
> 而是看**它有没有可能改变 agent 的输入**。
>
> 而第九个最重要的一点是：**它让准确率显示成 100%。**
> 前八个都会在某个地方露出马脚，第九个不会 —— 它只是让同一份数据的"正确答案"
> 随时刻改变，而报表依然漂亮。所以我现在看到 100% 的第一反应不是"做对了"，
> 而是去问三个问题：**覆盖度够吗、基准稳吗、指标标签对吗。**
>
> 第十个是做完上面全部验证、随手敲一条工单做演示时冒出来的 ——
> CLI 输出末尾多了一行"结论：不通过 / 问题：工单未关闭却已产生退款记录"，
> 而那条处置**是对的**（历史退款触发 R5 转人工，终态与 golden 一致）。
> 更怪的是同一个节点里，verifier 的自然语言结论写的是"通过" —— **一个节点内两个判定打架**。
> 查下去是 `verifier_node` 读错了 dict：澄清断言读 `out["work_notes"]`，
> 而 `out` 是节点自己的**增量输出**，里面只有 verifier 那份笔记，
> triage 写的"需要向客户澄清订单"根本不在里面 —— 读到空串，于是判"没有澄清依据"。
> **跨节点累积的状态在 `state` 里，不在节点返回值里**；代码不报错、类型也对、
> 测试全绿，因为它读的是一个合法的空字符串。
> 影响面：40 条运行里 2 条中招，都是 `ambiguous` 条件，终态全对 ——
> 也就是说它**不进 `diffs`、不进准确率、在任何数字上都看不见**，
> 只污染给人看的那一行输出。
> 修法是两条：读 `state` 而不是读 `out`；以及**优先用结构化事实**
> （快照里现成的 `order_basis` 是 `ambiguous`/`no_order` 就说明本来没有可处置的订单），
> 而不是在上游的自由文本里找关键词 —— 措辞一换就漏判。
>
> 第十个和第九个合起来，是我现在对"100% 准确率"的完整回答：
> 第九个证明**数字本身可能不可信**（基准在漂），第十个证明**结论本身可能不可信**
> （断言读错了地方）。两者都不报错。所以我现在对任何"判定"都会多问一句：
> **它读的是谁的数据？** —— `state`、节点返回值、`out` 增量，只差几个字符，
> 读错哪一个都不会有异常。

---

## 10. 引用

- τ-bench — https://arxiv.org/abs/2406.12045
- τ²-bench — https://arxiv.org/abs/2506.07982
- CRMArena — https://arxiv.org/abs/2411.02305
- AI Agents That Matter — https://arxiv.org/abs/2407.01502
- LangGraph — https://docs.langchain.com/oss/python/langgraph/overview
