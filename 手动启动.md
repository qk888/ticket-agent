# 手动启动说明

> 这份文档假设你**什么都没跑过**，从零开始照着敲。
> 每一段都标了「会发生什么」和「怎么知道成功了」。
> 全部命令都在 `ticket-agent\` 目录下执行。

---

## ★ 最省事的一条路：双击 `启动.bat`

**如果你不想敲命令，只想要一个能点的东西：双击项目根目录的 `启动.bat`。**

会弹出一个中文菜单，输入数字回车就行：

```
╔════════════════════════════════════════════════════════════╗
║  售后工单处置 Agent —— 一键启动                            ║
╚════════════════════════════════════════════════════════════╝

  忘了命令怎么敲没关系，选数字就行。
  第一次用请选 [1]，大约 10 秒，不需要任何 API Key。

  项目位置：C:\...\ticket-agent
  演示数据：C:\...\ticket-agent\eval_out\demo.db
  ★ 演示用的是独立数据库，不会动到你的评测数据。

   [1] 首次体验：生成数据 → 跑一张工单给你看
   [2] 跑一张工单（可以选工单号、选单/多智能体）
   [3] 人工审批：扮演“人工”裁决一笔退款
   [4] 跑测试，确认代码没坏（144 个，约 6 秒）
   [5] 跑评测，出一份准确率报告（约 1 分钟）
   [6] 打开 HTTP 服务（网页控制台 + 人工审批按钮）
   [7] 接真模型（本机 Hy3，零成本，但慢）
   [8] 看当前配置和可用模型
   [0] 退出
```

**推荐的第一次顺序**：`1` → `6`。

- 输入 `1`：生成演示数据，跑一张真实工单，把每个节点、每次工具调用都打出来（约 10 秒）。这张单会命中 R6 生鲜规则，自动转人工 —— 你正好能看到 HITL 是怎么停下来的。
- 输入 `6`：起服务，**浏览器会自动打开控制台**（<http://127.0.0.1:8000/>）。页面左上角 Token 填 `tok-acme`，点一张工单 → 「处置该工单」，就能看着它一步步跑完。

★ 菜单 [6] 用**独立数据库**（`eval_out/demo.db`），所以随便你怎么点都不会影响评测数据。

> 下面 §§1–8 是**逐步照敲版**：如果你想知道每一步到底在干什么、
> 或者菜单里的某一步失败了想自己排查，就看下面。

---

## 0. 先搞清楚你要做哪件事

| 你想做的事 | 走哪一节 | 需要联网/登录吗 |
|---|---|---|
| 只想看看这个项目能跑 | §1 + §2 | 不需要，内置脚本化模型 |
| 想用**真模型**跑出评测数字 | §1 + §3 + §4 | 需要（本机 Hy3 或你自己的 key） |
| 想自己起 HTTP 服务调接口 / 用网页控制台 | §1 + §5 | 不需要 |
| 只想重跑出问题的那几条 case | §6 | 需要 |

★ **第一次上手建议先走 §1 + §2**，不碰任何 API Key，五分钟就能看到这张单是怎么被办掉的。

---

## 1. 环境准备（只做一次）

### 1.1 检查 Python

```powershell
python --version
```

需要 **3.11 或更高**（本机实测 3.12.3）。低了先装 Python。

### 1.2 装依赖

```powershell
cd C:\Users\Administrator\Documents\deepseek-harness\default-workspace\ticket-agent
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

★ **本机现状提醒**：这台机器上**已经装好了**（全局 Python 里有 `ticket-agent.exe`），
你可以跳过 1.1–1.2，直接进 §2。上面这段是给换机器/重装用的。

验证装好了：

```powershell
ticket-agent --help
```

**期望**：列出 13 个子命令（`seed / tickets / run / pending / approve / show / eval / report / endpoint / smoke / info / model-kind / serve`）。

### 1.3 确认中文不乱码（Windows 必须）

PowerShell 默认是 GBK 控制台，Rich 打 `✓ ✗ ▶ ⚠` 会抛 `UnicodeEncodeError`。

```powershell
$env:PYTHONIOENCODING = "utf-8"
```

★ 项目里 `cli.py` 已经把 stdout/stderr 强制重配成 UTF-8 了，所以**通常不用**手动设；
但跑自己写的探针脚本（`_*.py`）时必须设，否则中文直接崩。

---

## 2. 不花钱跑通全流程（内置脚本化模型）

这一段**不需要任何 API Key**，用的是内置的 `ScriptedChatModel`（按规则扮演 agent）。

```powershell
# 1) 生成合成业务数据
ticket-agent seed --customers 60 --seed 7 --drop
```

**期望输出**（实测）：
```
{'customers': 60, 'orders': 142, 'items': 277, 'tickets': 33, 'policies': 9, 'refunds': 13, 'text_source_corpus': 33}
```

★ 数字随 seed 逻辑演进过（早期版本是 149 订单 / 35 工单，没有 `refunds` 和
`text_source_corpus` 两项）。**关键是 `customers: 60` 对得上、没报错**；
具体条数不必和我这里逐字一致。

```powershell
# 2) 看当前配置（模型种类、角色工具矩阵、当前端点）
ticket-agent info
```

```powershell
# 3) 列工单。★ 注意 order_basis 列：linked / sole_order / ambiguous / no_order
ticket-agent tickets --tenant acme --limit 10
```

```powershell
# 4) 真跑一张工单，逐节点播报工具调用
ticket-agent run 11 --tenant acme --stream
```

**期望**（类似）：
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

★ **注意 `arm=multi` 是默认值**。想跑单 agent 那条路径加 `--arm single`。

```powershell
# 5) 人工待办队列
ticket-agent pending --tenant acme
```

```powershell
# 6) 裁决一条转人工的工单（★ 用 --ticket，跨进程可用）
ticket-agent approve --ticket 17 --tenant acme --decision approve_refund
```

**期望**：政策禁止的（R5 已退过 / R6 生鲜）会**以退出码 1 拒绝**并打印政策原因。
确需破例才加 `--override`（会写审计记录）：

```powershell
ticket-agent approve --ticket 17 --tenant acme --decision approve_refund --override
```

```powershell
# 7) 出一份正式报告（两臂 + pass^3 + 分层 + 成本轴）
ticket-agent report --size 40 --k 3 --seed 7 --customers 60
```

**期望**：终端打印对照表，同时在 `eval_out\` 落下 `.json` + `.md` 两份报告。

```powershell
# 8) 跑测试
python -m pytest -q
```

**期望**：`144 passed in ~5s`。

```powershell
# 9) 文档结构体检
python _readmecheck.py
```

**期望**：`✓ 结构体检通过`（会打印字符数 / 标题数 / 围栏配对 / 缺陷标记 ①–⑲ / CRLF 数）。

**走到这里链路就通了。** 下面的段落都是"接真模型"。

---

## 3. 接真模型

有两条路，**任选一条**。

### 路线 A：本机 WorkBuddy 的 Hy3（零成本，但慢）

**前置条件**：DeepSeek Harness 里 WorkBuddy 插件是登录状态（`copilot.tencent.com`）。
Hy3 目前**限时免费**（实测 `usage.credit: 0`，额度快照前后不变）。

#### A-1 起 sidecar

sidecar 的作用是把 WorkBuddy 插件**内存里的登录凭据**暴露成一个 OpenAI 兼容接口
（凭据本身不落盘，也不进 `.env`，只有一把自签的 bearer token 进 `.env`）。

```powershell
cd C:\Users\Administrator\Documents\deepseek-harness\default-workspace\ticket-agent
node tools\wb_sidecar.mjs --port 8787
```

★ **这是一个常驻前台进程**，它会一直占着这个终端。
想后台跑就开**另一个** PowerShell 窗口执行上面的命令。

> ★★ **千万别用 `| Select-Object -First/Last` 截断它的输出** ——
> 那会连带把 node 进程一起杀掉。想看日志就重定向到文件。

#### A-2 确认它活着

```powershell
$tok = (Get-Content .env | Where-Object { $_ -match '^WORKBUDDY_SIDECAR_TOKEN=' }) -replace '^WORKBUDDY_SIDECAR_TOKEN=', ''
Invoke-RestMethod -Uri 'http://127.0.0.1:8787/healthz' -Headers @{ Authorization = "Bearer $tok" }
```

**期望**：
```
ok=True  model=hy3  state=signed-in  domain=copilot.tencent.com  minMaxTokens=2048
```

★ **不带 token 返回 401 是正常的**（那正是在鉴权）。
如果 `state` 不是 `signed-in`，说明 WorkBuddy 没登录，去 DSH 里登录后重启 sidecar。

#### A-3 自检端点

```powershell
ticket-agent endpoint --profile hy3 --check
```

**期望**：四项全过，并打印「已记下这次自检（.endpoint_check.json），eval / report 不会再拦。」

★ **这一步不能跳**。`eval` / `report` 有一道闸门，没通过自检会直接拒绝运行
—— 因为转发层有真实故障会**静默丢掉 `tool_calls`**，那样跑出来的准确率毫无意义。
自检就是专门查这个的。

#### A-4 切到真模型

```powershell
ticket-agent model-kind llm      # 切成真模型（切回内置：model-kind scripted）
ticket-agent smoke               # 单条冒烟，★ 看轨迹里有没有 tool_calls
```

**期望**：`smoke` 能打印出带工具调用的轨迹。**如果工具调用是空的，别往下跑**，
先回 A-3 重跑自检。

### 路线 B：用你自己的 API Key（任何 OpenAI 兼容端点）

```powershell
copy .env.example .env
notepad .env
```

填**其中一组**：

```ini
# —— 老路径（DeepSeek 默认）——
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_BASE_URL=https://api.deepseek.com/v1
MODEL_NAME=deepseek-chat

# —— 或者用端点档案（更清晰，本机当前用的是这个）——
LLM_PROFILE=hunyuan
HUNYUAN_API_KEY=sk-xxxxxxxx
```

可用档案（`ticket-agent endpoint --list` 看全部七个）：

| 档案 | 地址 | 单价（元/千 token） | 说明 |
|---|---|---|---|
| `deepseek` | `api.deepseek.com/v1` | 入 0.001 / 出 0.002 | 默认端点，支持 tool calling |
| `hunyuan` | 腾讯混元官方 | 入 0.0005 / 出 0.002 | ★ 默认并发上限 5，跑评测会被限流 |
| `hy3` | `127.0.0.1:8787/v1` | 入 0 / 出 0 | 本机 sidecar，**限时免费**，慢（~28s/条） |
| `ollama` | `localhost:11434/v1` | 入 0 / 出 0 | 本地推理，不需要 key |
| `relay` | 须填 | 未声明 | 第三方中转，★ 必须自检（转发层常丢 tool_calls） |
| `openai` | `api.openai.com/v1` | 未声明 | ★ 要去官网查价后填 `LLM_PRICE_*` |
| `custom` | 须填 | 未声明 | 四个值全靠 `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` 给 |

★ **单价不是必须填的**。不填就是"未声明"，报表会显式告警说成本列不可用
（**那表示"不知道"，不表示"免费"**）。填错了比留空更糟。

然后同样要自检 + 冒烟：

```powershell
ticket-agent endpoint --check
ticket-agent model-kind llm
ticket-agent smoke
```

---

## 4. 用真模型跑评测

```powershell
# 先探一条小的（2 条 case，约 1 分钟）
ticket-agent eval --size 2 --k 1 --arms single --kind llm --seed 7 --customers 30
```

**期望**：打印准确率。同时留意那行**成本告警** ——
如果打「有 N% 的 case 端点单价未声明」，说明你走的是未声明单价的档案，成本列是 0 但那是"不知道"。

```powershell
# 正式一点：40 条 × k=3 × 两臂
ticket-agent eval --size 40 --k 3 --arms single,multi --kind llm --seed 11 --customers 300 --report
```

**耗时预期**（实测）：

| 端点 | 单 run 延迟 | 40 条 × k=3 × 单臂 | 两臂 |
|---|---|---|---|
| `hy3`（本机） | ~28–90 秒 | **约 1 小时** | **约 2.5–3 小时** |
| `deepseek` | ~2–4 秒 | 约 3 分钟 | 约 6 分钟 |

★ Hy3 慢是因为它是 reasoning 模型，每轮都要先"想"一段。
**先用它把链路跑顺，要出正式规模报表再换 DeepSeek。**

```powershell
# 满规模（561 条 case × k=3 × 两臂 = 3,366 次运行）
ticket-agent report --size 605 --k 3 --seed 7 --customers 1000 --kind llm
```

**成本**：DeepSeek 约 **¥16**；Hy3 **¥0** 但约 **14 小时**。

### 报告落在哪

```
eval_out\eval-single-<时间戳>.json / .md
eval_out\eval-multi-<时间戳>.json  / .md
```

看 Markdown 那份最直观（有对照表、分层表、条件表、失败归因）。

---

## 5. 起 HTTP 服务 + 网页控制台

```powershell
ticket-agent serve --host 127.0.0.1 --port 8000            # 起服务（顺带建表）
ticket-agent serve --host 127.0.0.1 --port 8000 --seed 60  # 库里没数据时，连数据一起灌
```

**会发生什么**（实测）：

```
✓ 数据库已就绪：C:\...\eval_out\ticket_agent.db
  控制台：http://127.0.0.1:8000/   （浏览器直接打开）
  接口文档：http://127.0.0.1:8000/docs
INFO:     Uvicorn running on http://127.0.0.1:8000
```

★ 如果库是空的，它会先打一段人话提示，而不是让你面对一页空白：

```
⚠ 数据库里还没有工单，控制台会是空的。
  先灌数据：  ticket-agent seed --customers 60 --seed 7
  或直接起带数据的服务：  ticket-agent serve --seed 60
```

（这是**缺陷 ⑳**：`serve` 原先只管起服务、**从不建表**，
于是新克隆的仓库起完服务，页面上每个列表都是
`no such table: tickets`。现在起服务前会先建表并检查有没有数据。）

### 5.1 网页控制台（浏览器打开 http://127.0.0.1:8000/）

不用敲 curl，直接点：

1. 页面左上角填租户 Token：`tok-acme`
2. 左栏出现工单列表 → 点一张
3. 点「**处置该工单**」→ 右边**实时**打出处置轨迹：
   每个节点（supervisor / triage / resolver / verifier）、每次工具调用和耗时
4. 跑完出现终态卡：工单状态、**实际写了哪些数据**、终态快照、独立复核结论、
   以及**过程探针**有没有抓到违规

**★ 想亲眼看到"护栏挡住了人"**：找一张已退过款的工单跑一遍（会转人工），
然后在审批面板里**不勾 override** 点「批准退款」—— 页面会显示：

```
⛔ 政策拒绝（HTTP 409）
政策禁止该订单退款（R5）：该订单已有 1 条退款记录，不得再次退款，需人工处理。
人工审批不能绕过政策；如确需破例，请显式传 override=True（会记入审计）。
```

这就是缺陷 ⑥ 修好之后的效果：**人工审批也绕不过写路径上的硬校验**。
（勾上 override 再试一次就会成功，但会写进审计日志，且仍受账本不变量约束。）

控制台是 `web\index.html`，**单文件、零依赖、不用 npm 构建**，由后端同源挂出去。

### 5.2 接口一览

全部路由（实测 13 个）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查，**不需要鉴权** |
| GET | `/meta` | 自述：当前模型、角色工具矩阵、阈值。**演示时先打这个** |
| GET | `/tickets` | 列工单 |
| GET | `/tickets/{id}` | 工单上下文 |
| GET | `/pending` | 人工待办队列 |
| POST | `/runs/stream` | 跑工单，**SSE 流式** |
| POST | `/runs` | 跑工单，非流式 |
| GET | `/runs` | 列历史 run |
| GET | `/runs/{run_id}` | 看单个 run |
| POST | `/runs/{run_id}/approve` | 人工裁决（按 run，★ 只限同一进程） |
| POST | `/tickets/{id}/approve` | 人工裁决（★ 按工单，跨进程可用） |
| POST | `/admin/model-kind` | 切模型种类 |
| GET | `/eval/cases/{case_id}/verify` | 核验一条评测 case |

除去 `/health`，其余都需要 `Authorization: Bearer <租户token>`。
token 在 `.env` 里是一行 JSON（**实测默认值就是下面这两个**）：

```ini
TENANT_TOKENS={"tok-acme":"acme","tok-globex":"globex"}
```

★ 请求体里**没有** `tenant_id` 字段 —— 租户由 token 在服务端解出来，
客户端永远不能"指定"自己要操作哪个租户。所以下面所有例子都要带 `-Headers $h`。

```powershell
$h = @{ Authorization = "Bearer tok-acme" }

# 健康检查（无需鉴权）
Invoke-RestMethod http://127.0.0.1:8000/health

# 自述：先打这个，确认模型和角色工具矩阵
Invoke-RestMethod -Uri http://127.0.0.1:8000/meta -Headers $h

# 列工单
Invoke-RestMethod -Uri http://127.0.0.1:8000/tickets -Headers $h

# 跑一张工单（非流式）
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/runs -Headers $h `
  -ContentType 'application/json' -Body '{"ticket_id":11,"arm":"multi"}'

# 人工待办队列
Invoke-RestMethod -Uri http://127.0.0.1:8000/pending -Headers $h

# 人工裁决（★ 按工单裁决，跨进程可用）
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/tickets/17/approve -Headers $h `
  -ContentType 'application/json' -Body '{"decision":"approve_refund"}'

# 切模型种类
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/admin/model-kind -Headers $h `
  -ContentType 'application/json' -Body '{"kind":"llm"}'
```

★ 政策禁止的裁决会返回 **409 + policy 详情**。确需破例加 `"override": true`（会写审计）。

★ **`/runs` 与 `/tickets/{id}/approve` 的区别就是缺陷 ⑦**：
run 记录活在进程内内存里，工单活在库里。所以**跨进程**（CLI 生成、另一个进程审批）
必须用 `/tickets/{id}/approve`；`/runs/{run_id}/approve` 只限同一进程。

---

## 6. 跑那几只诊断脚本

项目根目录的 `_*.py` 是**取证脚本**，不是示例代码 ——
它们是为复现某个具体缺陷写的，每个都在 README §4.2 里有对应段落。
目前有 **65 个**，下面只列你真正会用到的那几个：

```powershell
$env:PYTHONIOENCODING = "utf-8"

python _endpointcheck.py      # 七个端点解析实测（八项）——换端点前先跑这个
python _readmecheck.py        # README 结构体检（最快，随时可跑）
python _conditions.py         # 看评测集的条件分布（跑之前先知道测了哪些规则）
python _progresscheck.py      # 数 audit_log 估算已跑多少 run
python _faultcheck.py         # 故障注入：验证探针抓不抓得住
python _sensitivity.py        # 定向敏感性分析（机会数 + 条件准确率）
python _p19after.py           # ⑲ 回归：只重跑 multi 臂失败过的那批 case
```

### ★ 跑 `_p19after.py` 之前必须知道的

它有两件副作用，都会干扰你正在跑的东西：

1. **它会 `init_db(drop=True)` 重建整个数据库** ——
   如果你有评测/服务正在跑，**会被打断**。
2. 它读 `eval_out\_failed_cases.json`（失败 case 清单）。
   这个文件是**某一次失败评测的产物**，跑过一次新评测后可能被覆盖或不在。
   **文件不在它会直接抛 `FileNotFoundError`。**

所以它的正确用法是：**确认没有别的任务在跑 → 先跑一次 multi 评测拿到失败清单 →
再跑它**。它是取证脚本，不是回归测试；日常验证请用 `pytest`。

### 其它常用校验

```powershell
python -m pytest -q                      # 144 个测试
node tools\wb_sidecar_check.mjs          # sidecar 28 项验收（需 sidecar 在跑）
```


---

## 7. 常见问题

### Q：`eval` 报「端点还没通过自检」

```powershell
ticket-agent endpoint --profile hy3 --check
```

★ **根因通常是 cwd 不对**：`Settings.eval_dir` 是**相对路径** `eval_out`，
自检记录写在 `<cwd>\eval_out\.endpoint_check.json`。
你在别的目录跑就找不到它。**先 `cd` 进 `ticket-agent\` 再跑。**

### Q：sidecar 起了但 `smoke` 里没有 tool_calls

1. 确认 `healthz` 的 `state` 是 `signed-in`；
2. 确认 `.env` 里的 `WORKBUDDY_SIDECAR_TOKEN` 和 sidecar 启动时打印的那把一致；
3. 重跑 `ticket-agent endpoint --profile hy3 --check` 看是哪一项没过。

### Q：Hy3 返回空正文、`finish_reason=length`

它把**思考 token 也算进 `max_tokens`**，传小了就会"想完就没额度写正文"。
sidecar 已经把下限抬到 **2048**，正常不用管；自己手写请求时要留意这点。

### Q：控制台中文乱码 / `UnicodeEncodeError`

```powershell
$env:PYTHONIOENCODING = "utf-8"
```

### Q：后台跑评测，但日志文件是空的

PowerShell 里 `2>&1 | Out-File` 有时不落盘，改用：

```powershell
python -m ticket_agent.cli eval ... *>&1 | Tee-Object -FilePath eval_out\mylog.log
```

★ 还有一条：`pwsh` 的**后台任务不继承工作目录**，
后台跑的时候必须在命令里显式 `Set-Location '...\ticket-agent'`。

### Q：想彻底重来

```powershell
ticket-agent seed --customers 60 --seed 7 --drop     # 重建业务数据
Remove-Item eval_out\* -Recurse -Force               # 清评测产物（含自检记录）
```

★ 清了 `eval_out\` 之后记得**重新自检**，否则闸门会拦。

---

## 8. 最短路径速查

```powershell
cd C:\Users\Administrator\Documents\deepseek-harness\default-workspace\ticket-agent
$env:PYTHONIOENCODING = "utf-8"

# 不花钱版本
ticket-agent seed --customers 60 --seed 7 --drop
ticket-agent run 11 --tenant acme --stream
ticket-agent report --size 40 --k 3 --seed 7 --customers 60

# 真模型版本（另一个窗口先起 sidecar：node tools\wb_sidecar.mjs --port 8787）
ticket-agent endpoint --profile hy3 --check
ticket-agent model-kind llm
ticket-agent smoke
ticket-agent eval --size 40 --k 3 --arms single,multi --kind llm --seed 11 --customers 300 --report
```
