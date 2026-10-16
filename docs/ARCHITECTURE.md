# 架构

> 想先跑起来看 [README §30 秒跑起来](../README.md#30-秒跑起来)。
> 想看 35 个缺陷的取证链去 [docs/DEFECTS.md](DEFECTS.md)。
> 想看评测怎么设计、两臂为什么这么比去 [docs/EVAL.md](EVAL.md)。

## 1. 领域为什么选「售后工单」

不是因为它常见，而是它天然满足评测的三个硬条件：

| 条件 | 本项目的体现 |
|---|---|
| **有 ground truth** | 工单该停在哪个状态、该不该产生退款记录，由政策唯一决定，不需要人工判分 |
| **有动作空间** | 13 个工具，其中 7 个写工具真实改动订单 / 退款 / 工单 / 售后表 |
| **有真实规则约束** | 7 天无理由、类目例外、风控阈值、退款次数上限、生鲜硬拒 |

### 1.1 数据模型

```
customers ──┬── orders ──── order_items
            │      │
            │      └────── refunds          （idempotency_key 唯一约束）
            └── tickets ──── audit_log      （agent 每个动作都落这里）
                       └──── after_sales_actions （7 类售后动作统一入口）
policy_articles                              （R1–R14 被切块入表，供 query_policy 检索）
```

`ticket.status` 取值 `open / pending_human / resolved / rejected`。
**这是唯一的终态判定字段**（`TICKET_FIELDS = ("status",)`），其他列只观测不判定
—— 拿 agent 控制不了的字段去判分，等于每条 case 都注定失败（见 DEFECTS ③）。

### 1.2 业务规则（R1–R14，完整文本见 `prompt/policies.md`）

| # | 规则 | 难点在哪 |
|---|---|---|
| R1 | 签收后 7 个自然日内可无理由退货 | 起算点是**签收时间**，必须调 `days_since_signed` 而不能心算 |
| R2 | 内衣 / 定制 / 数字商品不支持无理由 | 要查类目 |
| R3 | 已发货未签收 → 只能拦截物流不能退款 | 动作选择依赖订单状态机 |
| R4 | 金额 > 500 或风险分 > 0.7 或黑名单 → 转人工 | HITL 触发；转人工后工单要留在可被接手的终态 |
| R5 | 同一订单最多退一次 | 幂等 + 历史查询 |
| R6 | 生鲜不支持任何形式退款 | **优先级高于 R4**（生鲜不问金额直接转人工） |
| R7 | 工单关闭规则 | 四种结果对应四种状态；**意图不清时必须保持 open 反问，不得擅自关单** |
| R8 | 换货 | 签收天数 + 换货次数 + 品类 + 凭证 |
| R9 | 补发 | 补发次数上限 + 缺原因 / 缺凭证两条独立澄清 |
| R10 | 赔付 | 上限 30 元；**超限是权限边界不是政策禁止** |
| R11 | 价保 | 要 `current_price` 从 order 回落（见 DEFECTS ㉚） |
| R12 | 开票 | 已开则拒，重开应走红冲 |
| R13 | 改址 | signed / cancelled / shipped 三态三结论 |
| R14 | 催单 | 24 小时冷却 |

`RULE_PRECEDENCE` 固定优先级，`rules.py` 是**零内层依赖的叶子模块**。
这个分层是刻意做的：policy 依赖 storage 会立刻造成循环导入
（`rules → db.models → db.__init__ → db.seed → rules`）。

### 1.3 任务分层（评测按层报，避免总分掩盖「简单题全对难题全错」）

| 层级 | 定义 | 例子 |
|---|---|---|
| **L1** | 单动作，订单明确 | 「我的退款到哪了」→ 查订单 → 应答 |
| **L2** | 多步依赖 / 在途 | 「我要退货」→ 查时效 → 查类目 → 查风控 → 建退款 → 关单 |
| **L3** | 意图不清，需澄清 | 「我要退款。」（客户名下有 4 笔订单）→ **必须先反问是哪一笔** |

难度分层会**抹平**「考的是哪条规则」：`R1:reject` 与 `R3:block_logistics` 都是 L1，
但一个占 40% 一个占 2.7% —— **把每条 R3 全错，总准确率也只掉 2.7 个点。**
所以报表同时按条件分层，不只看难度。

---

## 4. 架构

```
POST /runs/stream
      │
      ▼
┌─────────────┐   set_tool_context() ──► contextvars.ContextVar
│  builder.py │   （并发评测靠它隔离租户，全局变量会串）
└──────┬──────┘
       │
   ┌───┴────┐        ┌──────────────────────────────┐
   │        │        │  registry.py                 │
 triage  resolver    │  ROLE_TOOL_MATRIX 物理裁剪   │
   │        │        │  幂等键 + 账本不变量         │
   │        │        │  _audit() 27 处调用全落审计  │
   └───┬────┘        └──────────────┬───────────────┘
       │                            │
       └────────► verifier ◄────────┘
                    │
                    ▼
            export_snapshot() → 和 golden 对账
```

### 4.1 角色权限矩阵（物理裁剪，不是 prompt 约束）

| 工具 | triage | resolver | verifier | single |
|---|:--:|:--:|:--:|:--:|
| `get_ticket` `get_customer` `list_orders_of_customer` `get_order` `list_order_items` | ✅ | ✅ | | ✅ |
| `list_refunds_of_order` `list_after_sales_of_order` `query_policy` `days_since_signed` | ✅ | ✅ | ✅ | ✅ |
| `check_refund_eligibility` `check_after_sales_eligibility` | | ✅ | | ✅ |
| `create_refund` `block_order` `close_ticket` `escalate_to_human` | | ✅ | | ✅ |
| `submit_after_sales_action` `mark_evidence_received` `ask_customer` | | ✅ | | ✅ |
| `get_snapshot` | | | ✅ | ✅ |

**越权不是拒绝在 prompt 里，是工具根本不在这个角色的清单里。**
调了不在清单里的工具 → `permission_denied`，写审计。
`ask_customer` 只给 `resolver` + `single`：triage / verifier 是只读角色，不该有写库能力。

---

### 4.3 其他工程细节

- **幂等键**：`create_refund` 默认 `md5(tenant:ticket:order:amount)`，DB 唯一约束兜底。
  缺陷 ⑪ 修过「键里混进随机数」—— 那是把幂等键当 trace id 用了。
- **账本不变量**（缺陷 ⑥ 的推广）：`已批准 + 已付出 + 本次 > 订单金额` → 拒。
  赔付与价保和退款是**同一个钱包**，只算 refunds 就是护栏只守住它认识的那种钱。
- **去重缓存**：只读工具按角色隔离缓存，命中时**回放真实结果**而非空壳 stub
  （早期版本命中缓存返回空数据，看起来像查无此单）。
- **预算追踪**：`BudgetTracker` 按 tenant + ticket 累计 token 与成本，
  超 `max_cost_cny` / `max_tokens` 熔断并转人工，不是让任务继续烧钱。
- **tiktoken 冷启动**：首次 BPE 下载可能卡 60 秒以上。
  改为后台线程最多等 5 秒，超时永久退化为字符估算 —— 单 case 39–178ms。
- **Windows 控制台**：`cli.py` 启动时把 stdout/stderr 重配置为 UTF-8 + `errors="replace"`，
  否则 rich 打 `✓ ✗ ⚠` 直接 `UnicodeEncodeError`（cp936），
  更糟的是报告写完了才在最后一个 ✓ 崩。

---

## 4.4 语料层：从 9 条手写模板到 11,079 条真实话术

```
data/raw/jddc_aftersale.jsonl         10,217 行 / 1.68 MB  京东客服真实对话
data/raw/ecom_aftersale.jsonl            997 行              电商售后补充
data/probe/samples/                    99.81 MB + 76.54 MB  下载的原始大文件
                    ms_ecom_train_FULL.jsonl / jddc_multi_train_FULL.txt …
```

构建脚本 `tools/build_corpus_jddc.py`，产物 11,079 条唯一话术，
每条带 `style`（vague / direct / angry / polite）与 `request_kind`（14 类诉求）。

**为什么不用模板编**：模板造出来的句子分布是均匀的，真实客户不是 ——
催件占 71/400，赔付只占 15/400。造数器要复刻的是**难点的稀疏性**，
不是让每个分支都舒服地出现（那只会得到虚高的准确率）。

**一个反直觉的发现**：语料里 26 条「疑似银行卡号」全是订单号 / 运单号
（`订单2973153096627851怎么收到的封口夹都碎了`），25/26 Luhn 不通过。
所以 PII 脱敏的豁免规则不能靠上下文（94% 是裸数字），必须靠 Luhn ——
否则会用合规的名义把 agent 找订单的唯一钥匙抹掉。

---

## 4.5 跨域效度检验：拿一份繁体数据集来验判据

判据不能只在自己的数据上自证。用繁体中文售后数据集跑同一套判据：

- 若它给出接近 100% 的分数 → 说明判据太松，或者任务太简单
- 若它给出接近 0% → 说明判据绑死了本数据集的偶然特征

这一步的价值不在分数本身，在**它逼着你把「难」和「判据错」分开**。

---

## 5. 目录结构

```
ticket-agent/
├── src/ticket_agent/
│   ├── rules.py              政策引擎（叶子模块，零内层依赖）
│   ├── config.py             端点档案 / 价格档案 / 设置
│   ├── clock.py              ★ 全项目唯一的「现在」（换时钟才能复现历史读数）
│   ├── db/                   models / session / snapshot / seed / corpus / pii / textnorm
│   ├── tools/registry.py     13 个工具 + 权限矩阵 + 幂等 + 审计
│   ├── graph/                state / nodes / builder / middleware / prompts / checkpoint
│   ├── agents/scripted.py    基线 + 10 个故障注入模型
│   ├── eval/                 harness / report
│   ├── api/app.py            FastAPI（模块级 `app`，不是 create_app）
│   └── cli.py                typer CLI（serve / eval / report / endpoint / demo …）
├── prompt/policies.md        R1–R14 政策全文（给人看的，机器读 rules.py）
├── tests/                    264 个用例
├── tools/                    wb_sidecar.mjs 等 node 侧工具
├── diagnostics/              161 个取证脚本（一个文件只答一问）
├── docs/                     本目录
├── web/index.html            单文件控制台，零前端依赖
├── data/                     语料（raw 不提交）
├── eval_out/                 报表与数据库（不提交）
├── Dockerfile / docker-compose.yml
└── README.md
```

**关于 `diagnostics/` 里的 161 个脚本**：README 里写着「实测依据 `_r10diag.py`」，
那个文件就必须真的在仓库里。「git 历史里有」和「clone 下来能看见」是两件事。
它们**不是垃圾**，是 35 条缺陷各自的取证现场。
