# 接哪个模型跑：Hy3 / 混元 / DeepSeek / 本地

你问的是「如果直接接入这个模型跑怎么样 预留出真实apikey和本地大模型接口」。
三件事分开答：**接口已经预留好了**（四条路都能走）、
**Hy3 已经接通并跑出了真实数据**（走本机 WorkBuddy，见第五节）、
其余三条路各自有各自的坑。

> ★ **本文档已按实况更新。** 早先版本把 Hy3 当成"第三方中转站"，
> 要你自己去填 `LLM_PROFILE=relay` + 中转地址 —— 那个判断**已经被推翻了**：
> Hy3 不在任何中转站，它就是 **DeepSeek Harness 里登录的 WorkBuddy** 里的模型。
> 现在它已经是一个**内置档案 `hy3`**，跑起来只要两行配置。第五节是完整接法。

---

## 一、先说结论

| 路线 | 能不能跑 | 建议 |
|---|---|---|
| **Hy3**（本机 WorkBuddy，档案 `hy3`） | ★ **已跑通并出过报表** | **零成本**（限时免费），适合反复迭代；单轮慢（~28s/条） |
| **DeepSeek**（默认） | 能，已验证 | 要**大规模正式报表**就用它，一轮千级 16.14 元 / 十几分钟 |
| **腾讯混元**（官方） | 能，已配好档案 | 可用，但**并发上限 5**，跑整轮要串行、会很慢 |
| **本地 Ollama** | 能，但**只能冒烟** | 6GB 显存是死结，跑评测要 15~30 小时，不值 |

**一句话**：Hy3 已经接好了、也真跑过一轮了（数字见 `eval_out/eval-single-*.md`）；
要正式规模报表就换 DeepSeek 填个 key。
但换任何端点**之前**，先跑一次自检 —— 下面的第三节说明了为什么这一步不能省。

---

## 二、Hy3 那个「限时免费」到底怎么算钱

WorkBuddy 模型面板里 Hy3 标的是 `Hy3 · x0.00 · 限时免费`。

★ **别信截图，信上游自己说的话。** 我查了三处**独立**证据（`tools/wb_credit_probe.mjs`）：

1. **模型名册**里 Hy3 的条目自带 `billing: {"credits": "x0.00 credits", "free": true}`；
   对照 Hy3-X 是 `{"credits": "x0.05 credits", "free": false}` —— 所以这个字段**真的会区分**，不是摆设。
2. **单次调用的 usage 回传** `credit: 0`。
3. **调用前后的额度快照完全一致**（`total: 1358`，`remain`/`size` 一个数都没动）——
   说明这次调用确实没扣额度，不是"扣了但没显示"。

三处对上，所以档案 `hy3` 的 `price_in`/`price_out` 填 `0.0`。
这和 `ollama` 档案口径一致：**0 = 边际成本为 0**，不是"不要钱"（电费、机器折旧、时间不在这张表里）。

★ **但 `free: true` 是限时的**（面板上自己写着"限时免费"）。
活动一结束，上游 `credit` 就不再是 0，**而本项目的成本列会继续按 0 算** ——
这属于缺陷 ⑭ 同族（成本列安静地说谎）。届时的处置：

```bash
# .env —— 显式声明单价，覆盖档案里的 0
LLM_PRICE_IN_PER_1K_CNY=0.00x
LLM_PRICE_OUT_PER_1K_CNY=0.00y
```

★ 这个"0 与不知道的区别"是本项目**故意做**的硬约束：
对未声明单价的端点（`relay` / `openai` / `custom`）宁可留空：

```
price_known = False
   ↓
Budget.price_unknown → CaseResult.price_unknown
   ↓
报表里出现告警块：
  ⚠️ 成本列不可用：100% 的运行落在没有声明单价的端点上
     上表的金额是按 0 累加出来的，不是免费，是不知道
```

`0` 在报表里长得跟"免费"一模一样。宁可写着"不知道"，也不要一个看起来合理、实际是编的 0。

---

## 三、为什么换端点必须自检（这一步不能省）

本项目的 agent 只有一种推进方式：**模型发出 `tool_calls`**。

```python
# graph/nodes.py 的 ReAct 循环
calls = response.tool_calls
if not calls:
    break          # ← "没工具调用"和"任务完成"是同一个分支
```

而**转发层**（中转站、本地 shim、自建网关）丢掉 `tool_calls` 字段是**常见故障**。
症状是：

> 接口一切正常。HTTP 200、返回了内容、token 也计费了。
> 只是 agent 一步都走不动，而它**不会报错**。

后果：评测照常跑完、报表照常生成，只是准确率很低 —— **而那时钱已经花了**。
一轮千级评测要几千次模型调用，自检只要 4 次请求。

所以 `eval` / `report` 会拦：

```bash
$ ticket-agent report --kind llm
✗ 端点还没自检通过，先跑：
  python -m ticket_agent.cli endpoint --check   （4 次请求，几秒钟）
```

自检四项：**① 连通与鉴权 ② `tool_calls` 是否被保留 ③ 多轮工具循环
④ usage 是否上报**。前两项是"能跑"的必需项，后两项只出警告 ——
"能跑但成本不准"不该被误判成"不能跑"。

★ 逃生开关：`LLM_ALLOW_UNVERIFIED=1`（你知道自己在干什么时用）。
★ 自检通过后会写 `eval_out/.endpoint_check.json`，闸门就开了（缺陷 ⑯ 修好后自动落盘）。

---

## 四、五条路怎么配（照抄）

### A. Hy3（本机 WorkBuddy，档案 `hy3`）★ 你问的这个，已经接好了

**不需要任何 API key。** 它复用你已登录的 WorkBuddy 桌面端，
经一层本地 sidecar 暴露成 OpenAI 兼容接口。两步：

```bash
# 1) 起 sidecar（前台常驻；★ 别用 Select-Object 截断它的输出，会带走 node 进程）
cd ticket-agent
node tools/wb_sidecar.mjs --port 8787

# 2) .env 里两行（token 从 sidecar 启动横幅或 .workbuddy-sidecar.json 里抄）
#    LLM_PROFILE=hy3
#    WORKBUDDY_SIDECAR_TOKEN=<43 字符 bearer>

# 3) 自检
python -m ticket_agent.cli endpoint --profile hy3 --check

# 4) 小规模跑
python -m ticket_agent.cli eval --size 40 --k 3 --arms single,multi \
  --kind llm --seed 11 --customers 300
```

★ **为什么中间必须有一层 sidecar**（三条理由，都不是"顺手加的"）：

1. 插件的 `SHARED_SECRET` **只活在插件进程内存里、从不落盘**，
   外部拿不到 bearer —— 没它一个请求都发不出去；
2. shim **只吐 SSE 从不聚合**，而本项目用 LangChain `ChatOpenAI` 默认非流式，
   直连必定解析失败；
3. Hy3 是**纯 reasoning 模型**（名册 `onlyReasoning: true`、`canDisableThinking: false`），
   **思考 token 计入 `max_tokens`** —— 端点自检发的 `max_tokens=16` 会被思考吃光、
   正文为空但 HTTP 200。sidecar 会把上限抬到 2048（`--min-max-tokens` 可调）。

实测代价：**单条 ~28 秒**（40 条 × k=3 = 120 次运行跑了约 70 分钟）。
零成本，适合反复迭代；要正式大规模报表请走 B。

### B. DeepSeek（默认，推荐出正式报表）

```bash
# .env
OPENAI_API_KEY=sk-你的key
OPENAI_BASE_URL=https://api.deepseek.com/v1
MODEL_NAME=deepseek-chat
```

内置单价已带（入 0.001 / 出 0.002 元/千 token），不用手填。

### C. 腾讯混元（官方）

```bash
# .env
LLM_PROFILE=hunyuan
HUNYUAN_API_KEY=你的key
```

已带官方价（入 0.0005 / 出 0.002 元/千 token），地址与模型名都配好了。

★ **但要注意并发上限**：混元默认并发 5（主子账号共享）。
本项目一轮千级评测要跑几千次调用，直接上会被限流。要么：

```bash
# 降规模先把链路跑通
ticket-agent report --size 60 --k 1 --kind llm
```

要么接受它慢慢跑（串行几千次调用，按每次 2 秒算也要几小时）。
**混元适合"换个模型看看效果"，不适合出正式报表。**

另外混元的 context 上限较小，而本项目单工单平均 **4.3~10.4 次模型调用**
（多轮 ReAct，每轮都要带上完整工具定义与历史），长工单可能超限。

### D. 任意第三方中转（档案 `relay` / `custom`）

```bash
# 1) 先自检，看清楚它到底保不保留 tool_calls
python -m ticket_agent.cli endpoint --profile relay \
  --base-url https://中转站地址/v1 \
  --model 模型名

# 2) 自检通过后写进 .env
LLM_PROFILE=relay
LLM_BASE_URL=https://中转站地址/v1
LLM_MODEL=模型名
RELAY_API_KEY=你的key

# 3) ★ 单价必须填 —— 未声明的话成本列会显示"不知道"
LLM_PRICE_IN_PER_1K_CNY=0.00x
LLM_PRICE_OUT_PER_1K_CNY=0.00y
```

★ 自检四项里，**第 2 项就是专门分辨"转发层丢字段"与"模型不支持 tools"的**。
如果是后者，换模型；如果是前者，换中转站。`smoke` 的判据也在这：

> 正常：轨迹里有 `get_ticket` → `check_refund_eligibility` → ...
>
> 异常：`模型没有发起任何工具调用` —— 最可能的原因就是这个端点没有保留 `tool_calls`。

### E. 本地模型（只能冒烟）

```bash
winget install Ollama.Ollama
ollama pull qwen2.5:7b-instruct
ollama serve

# .env
LLM_PROFILE=ollama          # 地址、模型名、价格都配好了
# 无需 key
```

★ **但别指望它出报表。** 本机 RTX 3060 Laptop 只有 6GB 显存，
`qwen2.5:7b`（4.7GB 权重）装得下权重，装不下 KV cache，会退到 CPU：

| | 云端（DeepSeek） | 本地 7B Q4 | 本机 Hy3 |
|---|---|---|---|
| 一轮千级评测 | **16.14 元 / 十几分钟** | 15~30 小时 | 0 元 / 数十小时（限时免费） |

本地路线本来的卖点是"省钱"，但**时间成本远大于省下的钱**。
而且 7B 级别模型在多轮 ReAct 里容易断链 —— 那正是"agent 一步都不动"的失效模式。

本地端点适合 `cli smoke` 验证接线（这条路完全通，已实测），
不适合 `cli report` 出报表。

---

## 五、Hy3 的实测结果（跑过了，不是推测）

```
命令：python -m ticket_agent.cli eval --size 40 --k 3 --arms single,multi \
        --kind llm --seed 11 --customers 300
报表：eval_out/eval-single-20261010-113840.md
```

| 对照臂 | 运行数 | pass^3 | 单次准确率 | 平均模型调用 | 平均工具调用 | 违规 |
|---|---|---|---|---|---|---|
| `single` | 120 | **90.0%** | 96.7% | 4.30 | 5.84 | **0** |

★ **真正值钱的是 `pass^3 90.0%` 与 `96.7%` 之间那 6.7 个点**：
说明模型不是"某类题不会"，而是"同一道题三次里有一次没收好尾"。
逐条取证后 4 条失败**全是"判对了没执行完"**（该关单却停在 `open`、该退款却转人工），
**没有一条退错钱、没有一条触发 `forbidden`**。

接这条路的**附带产出**是三个真缺陷（README §4.2 的 ⑮⑯⑰）——
它们是"一直用默认配置从没被验证过"的典型：配置层断裂、自检与闸门断链、键名漂移。
**接一个真模型进来花了半小时，赚回来三个缺陷。**

---

## 六、换端点前必须知道的两件事

### ① 报表里会记下你用的端点，不然两轮数据不可比

就像语料指纹（`corpus_sha256`）一样，端点也是**漂移源**：

```
meta["endpoint_profile"]  = "hy3"
meta["endpoint_model"]    = "hy3"
meta["endpoint_base_url"] = "http://127.0.0.1:8787/v1"
meta["price_in_per_1k"]   = 0.0
meta["price_known"]       = True
```

★ 端点是**比语料更隐蔽**的漂移源：换模型往往连单价一起变了，
但成本列的代码没变、数字量级也照常合理 ——
于是"这次比上次便宜"这种结论会建立在两套单价上。

### ② `kind=scripted` 不调模型，端点信息不写入

脚本臂（`scripted`）是纯规则驱动的确定性 agent，用来验证评测框架本身。
它不调模型，所以换端点对它没有影响，报表里也不会写端点信息。
**要看模型能力必须用 `kind=llm`。**

---

## 七、成本预算（跑之前心里有数）

按实测的报表数字（587 条 case、k=3、输入 8,790 / 输出 401 token/次）：

| 规模 | 运行次数 | DeepSeek 成本 | Hy3 成本 | Hy3 耗时（实测外推） |
|---|---|---|---|---|
| `--size 40 --k 3` | 120×2 臂 | 约 0.7 元 | **0 元** | 约 70 分钟/臂 |
| `--size 60 --k 1` | ~120 | 约 1 元 | 0 元 | 约 1 小时 |
| `--size 605 --k 3` | 1,761 | **16.8 元** | 0 元 | 约 14 小时 |

★ 建议路径：**先用 Hy3 把链路与评测集跑顺**（零成本，慢点没关系），
确认报表正常、违规数合理，再换 DeepSeek 上规模出正式报表。

用第三方中转的话按它的实际倍率折算 —— 但记住倍率是活动，
**不要在报表里假装这个价格是长期可比的价格**。
