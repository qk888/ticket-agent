# 端点档案与模型接入

> 想直接跑起来（不需要 API key）看 [README §30 秒跑起来](../README.md#30-秒跑起来)。
> 部署方式看 [docs/DEPLOY.md](DEPLOY.md)。
> 七个档案的完整价格表与「值不值得跑」在仓库根的 [docs/ENDPOINTS.md](ENDPOINTS.md)。

## 1. 接真 LLM

复制 `.env.example` 为 `.env` 并填写：

```
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com/v1     # 或任意 OpenAI 兼容端点
MODEL_NAME=deepseek-chat
CHEAP_MODEL_NAME=deepseek-chat                  # 预算降级后切到它
```

然后 `ticket-agent model-kind llm` 切过去，或起服务后 `POST /admin/model-kind`。
也可以指向本地 Ollama（`http://localhost:11434/v1`）把成本压到 0。

★ **换端点之前先跑自检**（理由见第 3 节）：

```bash
ticket-agent endpoint --check          # 3 次请求，约 5 秒
ticket-agent smoke                     # 跑一张真工单，看轨迹里有没有工具调用
```

## 2. 端点档案 + 覆盖

一个开关不够用：同一份评测要能在「云端真 API」和「本地 Ollama」之间来回跑，
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
| `hy3` | **127.0.0.1:8787**（本机 sidecar） | hy3 | 0 / 0 | WorkBuddy 桌面端登录态桥接，见第 5 节 |
| `ollama` | localhost:11434 | qwen2.5:7b-instruct | 0 / 0 | 本地，见「值不值得跑」 |
| `openai` | api.openai.com | gpt-4o-mini | **未声明** | 官方端点 |
| `relay` | **留空** | **留空** | **未声明** | 第三方中转 / 聚合 |
| `custom` | 全靠 `LLM_*` | 全靠 `LLM_*` | **未声明** | 任意兼容端点 |

## 3. 三条设计决定

**① 自检不是可选项，`eval` / `report` 会拦你。**

`preflight()` 在评测前跑一次连通性检查，失败直接拒绝开跑。
否则你会在跑到第 800 条时才发现端点从一开始就是不通的 ——
1200 次运行的价值是零，而这件事**看起来完全正常**（每条 case 都在正常失败）。

**② 单价必须跟着端点走，认不出来就明说「不知道」。**

`price_input_per_1k_cny` 的默认值曾经是 `0.001`（DeepSeek 的价），
于是**任何**端点都被记成 DeepSeek 的价，成本列安静地算错账。
现在默认 `None`，按地址反查档案认领；认不出来 → `price_known=False`，
报表上写「单价未声明（成本列不可用，不是免费）」（见 [DEFECTS ⑭](DEFECTS.md)）。

**③ 同一个地址下的不同模型，单价要按模型核对，不做前缀匹配。**

sidecar 在 `127.0.0.1:8787` 一个地址后面挂了 16 个模型，`hy3` 免费而 `hy3-x` 收费。
只按 `base_url` 认领会让**任何模型都记成 ¥0 且 `price_known=True`**。
所以档案有 `price_scope`：`address`（本地自建推理边际成本恒 0，ollama 用）vs
`model`（必须核对模型名，deepseek / hunyuan / hy3 / openai / relay 用）。
★ **不做前缀匹配**：`"hy3-x".startswith("hy3")` 为真，
而这两个正好是免费 vs 收费（见 [DEFECTS ⑱](DEFECTS.md)）。

## 4. 一个坑：`.env` 里的 key 到不了解析器

`SettingsConfigDict(env_file=..., extra="ignore")` 只让 pydantic 填**已声明字段**；
`.env` 从不被写进 `os.environ`；而端点解析器的 `env_get` 只读 `os.environ`。
所以 `WORKBUDDY_SIDECAR_TOKEN` / `HUNYUAN_API_KEY` / `RELAY_API_KEY`
这些**档案专用 key 两条路径都到不了**（见 [DEFECTS ⑮](DEFECTS.md)）。

修法：`Settings.__init__` 记录本次实际用的 dotenv 层，解析时
「进程环境优先、`.env` 兜底」，并在 source 文案里写清 key 来自哪里。
**不把 `.env` 写进 `os.environ`** —— 那会污染进程、破坏测试隔离。

## 5. 接本机 WorkBuddy 的 Hy3

如果你本机装着 WorkBuddy 桌面端且已登录，它插件内存里有一批模型（含 `hy3`，
插件标注 `billing.free=true`，实测单次 `usage.credit: 0`、额度快照前后不变）。

`tools/wb_sidecar.mjs` 把这些桥接成一个 OpenAI 兼容端点：

```
WorkBuddy 插件（内存里的 bearer）
      │  wb_sidecar.mjs
      ▼
http://127.0.0.1:8787/v1     ← LangChain 打这里
      │
      └─ LLM_PROFILE=hy3
```

三条存在理由（写在文件头）：

1. **bearer 只在插件内存里**，没有别的办法拿到
2. **LangChain 默认非流式，而 shim 只吐 SSE**，必须聚合
3. **hy3 的思考 token 吃 `max_tokens`**，所以 `max_tokens` 下限抬到 2048
   （传 64 会 `finish=length` 且正文为空）

启动与自检：

```bash
node tools/wb_sidecar.mjs --port 8787          # 常驻
node tools/wb_sidecar_check.mjs                # 28 项验收
ticket-agent endpoint --profile hy3 --check
```

★ hy3 是**远程模型**，GPU 用不上 —— sidecar 只是 SSE 转发器，
瓶颈在等模型回。并发 4/8/16 实测中位延迟 4861/3982/3938ms 基本不变（未饱和），
但最慢值 4862→11473→7181ms 随并发线性拉长，所以 8 路是甜点。
