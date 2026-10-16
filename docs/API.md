# HTTP 接口

> 起服务：`ticket-agent serve --seed 60`，然后浏览器开 `http://127.0.0.1:8000/`
> 就是网页控制台（[docs/DEPLOY.md](DEPLOY.md)）。

## 鉴权

演示用租户鉴权：`Authorization: Bearer <token>`，token → 租户的映射在 `TENANT_TOKENS`。
**请求体里没有 `tenant_id` 字段** —— 租户只能来自 token，这是防越权的第一道闸。

★ 注意是 `Bearer tok-acme`，不是 `authorization: tok-acme`
（服务端检查 `startswith("bearer ")`，写少了返回 401 `缺少 Bearer token`）。

## 端点一览

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
| POST | `/tickets/{id}/customer-reply` | 提交客户答复，落库后可选择重跑（多轮澄清闭环） |
| POST | `/admin/model-kind` | 运行时切换 `scripted` / `llm` / 六个故障注入模型 |
| GET | `/eval/cases/{case_id}/verify` | 用 golden 校验某个 case 的当前终态 |

## 四条刻意的设计

- **跨租户访问返回 404 而不是 403。** 403 等于告诉对方「这个 id 存在，只是你没权限」，可以用来枚举。
- **SSE 用 `event:` + `data:` 双行 + 空行分帧**，事件序列契约是
  `run_started → (node_started / tool_call / node_finished)* → final → done`，前端按事件名分流。
  响应头带 `X-Accel-Buffering: no` 关掉 nginx 缓冲，否则流会被攒着一次性吐出。
- **人工审批默认受政策约束**（见 [DEFECTS ⑥](DEFECTS.md)）。R5/R6 这类政策禁止的退款会返回 **409**，
  必须显式带 `"override": true` 才能破例，且破例会在 `AuditLog` 里留下 `action="policy_override"` 记录。
  人不能因为「是人工」就获得了越过规则的默认权力。
- **人工裁决有两个入口，按工单的那个才是主力**（见 [DEFECTS ⑦](DEFECTS.md)）。run 记录存在进程内内存里，
  而工单存在库里 —— 只认 `run_id` 的接口在「跑 agent 的进程」与「处理审批的进程」不是同一个时
  会直接报「run 不存在」，而工单其实好端端躺在待人工队列里。
  人工审批的自然主语是**哪张工单要判**，不是哪次运行。

## 一个坑：`ApproveRequest.decision` 的正则

```
^(approve_refund|reject|keep_pending)$
```

写 `approve` 会得到 422，不是 400 —— 校验失败发生在请求模型层。

## 前端为什么要手写 SSE

原生 `EventSource` 只支持 GET，而后端流式是 `POST /runs/stream`。
所以 `web/index.html` 用 `fetch + ReadableStream` 手写帧解析
（`parseSSE()`）。整个控制台是**单文件零依赖**，不引 Vue/React+Vite ——
这个项目的主线是「Agent 能不能在生产里用」，不是「前端工程化」。
