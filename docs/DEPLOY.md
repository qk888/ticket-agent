# 部署

> 本机已确认可用：`Docker version 29.7.2`、`Docker Compose version v5.5.1`。
> 只是本机跑一遍看效果，用仓库根的 `启动.bat` 更快（还能连 hy3）。

## 一、一键起（演示）

```bash
docker compose up -d --build
```

然后浏览器打开 **http://localhost:8000/** ，Token 填 `tok-acme`。

`-d` 是后台跑；不加就在当前窗口看日志（Ctrl+C 停）。

```bash
docker compose logs -f api     # 看日志
docker compose down            # 停（数据在 eval_out/ 卷里，不丢）
```

## 二、里面到底跑了什么

`docker compose up` 只起**一个容器**，容器里跑的就是你本机那条命令：

```
ticket-agent serve --host 0.0.0.0 --port 8000 --seed 60
```

- `--seed 60` = 起服务前灌 60 个客户的演示数据（库里没数据时控制台是空的）
- `--host 0.0.0.0` = 容器必须监听所有网卡，写 `127.0.0.1` 的话你在容器外**连不上**
- 演示库用 `eval_out/demo.db`，**不碰** `eval_out/ticket_agent.db`（那是评测库，别被演示洗掉）

### 镜像为什么这么做（每条都对应踩过的坑）

| 做法 | 理由 |
|---|---|
| 多阶段构建 | builder 装 dev 依赖、最终镜像只留运行时，从 ~1.1GB 降到 ~400MB |
| `python:3.12-slim` 不用 alpine | SQLAlchemy 的 C 扩展和 psycopg 在 alpine 上没有 wheel，要在容器里编译，一次构建十几分钟 |
| `tini` 当 1 号进程 | 没有它 `docker stop` 要等 10 秒才超时强杀；有它信号能正确转给 uvicorn |
| 非 root 用户跑 | uvicorn 不该以 root 跑 |
| 三个卷（eval_out / data / logs） | 不挂卷的话容器一重建，1200 次评测运行全没；语料 100+MB 烧进镜像会让每次改代码都重传 |
| `HEALTHCHECK` 打 `/health` | 能直接看出「服务是真起来了还是只在监听端口」 |

## 三、要连真模型（DeepSeek / 混元）

镜像里**不带** key，也不该带。用环境变量传：

```bash
# 建一个 .env 文件（compose 会自动读）
DEEPSEEK_API_KEY=sk-你的key
```

然后：

```bash
docker compose --env-file .env up -d
```

容器内自检：

```bash
docker compose exec api ticket-agent endpoint --check
docker compose exec api ticket-agent endpoint
```

> hy3 那条路（WorkBuddy sidecar）**不能**进容器 —— 它依赖本机 `.dsh` 里的插件内存凭据，
> 容器里没有那个进程。要真实模型读数就用 `deepseek` / `hunyuan` 档案 + 真 key。
> 没 key 也能正常跑演示和控制台，只是 Agent 会说「端点不可用」。
> 详细的端点档案与价格说明在 [docs/SETUP.md](SETUP.md)。

## 四、可选：PostgreSQL（生产路径，现在不需要）

默认是 SQLite + MemorySaver，单容器演示完全够。

只有一种情况需要它：**HITL 人工审批要跨重启生效**（服务一重启，内存里的待审批会话就没了）。

```bash
docker compose --profile postgres up -d
```

起来后把 `.env` 里 `CHECKPOINTER=postgres` 打开。这一步本项目**还没做完整测试**，
标注为可选增强，不是默认路径。

## 五、在容器里跑测试 / 评测

```bash
docker compose exec api python -m pytest -q          # 单测
docker compose exec api ticket-agent eval --size 6 --k 1 --kind scripted
docker compose exec api ticket-agent report --size 6 --seed 7
```

`exec` 是进已经在跑的容器；想跑一次就退出用 `docker compose run --rm api <命令>`。

## 六、常见问题

**`docker compose up` 报 `bind: address already in use`**
8000 被占了。改 `docker-compose.yml` 里 `"8000:8000"` 左边那个数，比如 `"8080:8000"`，然后开 http://localhost:8080/ 。

**页面能开但工单列表空 / 500**
先看日志 `docker compose logs -f api`。正常情况 `--seed 60` 会自动灌数据；
如果你是手动 `run` 忘了带 `--seed`，执行一次 `docker compose exec api ticket-agent seed --customers 60 --seed 7`。

**构建慢**
第一次要装 langchain/langgraph/fastapi 一整套，约 2–4 分钟，正常。
之后改代码只重建最后一层，几秒钟。

**想改代码看效果**
源码是 `COPY src ./src` 进镜像的，改完必须 `docker compose up -d --build` 才生效
（compose 不会热重载，因为没开 `--reload`）。

**残留 demo.db 导致 IntegrityError**
如果之前跑过旧版本，`eval_out/demo.db` 可能留着几笔订单，`--seed 60` 再灌会被唯一约束挡下一部分。
服务照常起、控制台照常有数据。删掉 `eval_out/demo.db` 重灌即可。

## 七、网页控制台

```bash
ticket-agent serve --host 127.0.0.1 --port 8000            # 起服务（顺带建表）
ticket-agent serve --seed 60                               # 库里没数据时，连数据一起灌
```

浏览器打开 <http://127.0.0.1:8000/> 就是控制台：

- 左栏：待人工裁决 / 工单列表 / 运行记录，点一张工单就选它
- 中间：「处置该工单」→ 走 SSE，**实时**打出每个节点、每次工具调用和耗时
- 终态卡：工单状态、实际写了哪些数据、终态快照、独立复核结论、**过程探针违规**
- 还没有人工裁决的工单，会出现审批面板 —— 金额、备注、`批准退款|驳回诉求|维持挂起` + override 复选框

★ 想看到「护栏真的挡住了人」，随便挑一张已退过款的工单跑一遍，然后**不勾 override** 点「批准退款」：
服务会返回 `409` 并给出政策理由。人工审批也绕不过写路径上的硬校验。

控制台是 `web/index.html`，**单文件、零依赖、无构建步骤**，由 FastAPI 同源挂出去（`GET /`）。
不引 Vue/React 的理由很实际：本项目的卖点是「clone 下来就能验」，
而 `npm install` 会把这一条变成五分钟的等待。
它需要的数据接口一页就能列完（见 [docs/API.md](API.md)），手写 DOM 比配一套构建工具链更短。

## 八、和「双击 启动.bat」怎么选

| | `启动.bat` | `docker compose up` |
|---|---|---|
| 适合 | 本机快速看演示 | 给别人部署 / 上服务器 |
| 依赖 | 本机 Python 3.11+ | 只有 Docker |
| 数据位置 | `eval_out/demo.db` | 卷 `./eval_out/demo.db`（同一份） |
| 真模型 | 可以（sidecar / deepseek） | 只支持 deepseek / hunyuan（带 key） |

**结论：本机演示继续用 `启动.bat`（更快、能连 hy3）；要交付 / 部署用 Docker。**
