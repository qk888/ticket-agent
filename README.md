# ticket-agent · 售后工单处置 Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-263%20passed-brightgreen.svg)](docs/TESTING.md)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](docs/DEPLOY.md)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://docs.astral.sh/ruff/)

> **一个会自己发现「我做错了」的 Agent。**
>
> 它处置售后工单：查订单、判资格、退款 / 补发 / 赔付 / 关单，高风险动作必须人工点「批准」。
> 每一次调用都落审计日志，每一轮处置都被拿去和**数据库终态**对账 —— 不是和它自己说的话对账。

---

## 一眼看它长什么样

![网页控制台](docs/screenshots/console.png)

左边是工单列表，中间是 agent 一步步调工具，右下是它给这个工单的结论。
注意中间那栏里 `verifier` 的最后一段：

```
结论：不通过
问题：工单已关闭为 resolved，但既无退款记录、订单也未取消 —— 属于只关单不办事
建议：请 resolver 复核上述问题后重新处置。
```

**这个 `verifier` 是独立的一票，专门抓「活儿没干完但话说得漂亮」。**
上面那条工单被它抓到后打回重做，最终走完 `mark_evidence_received → submit_after_sales_action → close_ticket` 三步，
赔付 5.0 元（在 30 元权限上限内），工单 `resolved`。全过程 127ms。

这就是本项目的全部主张：**agent 说得对不算对，数据库变对了才算对。**

---

## 30 秒跑起来

```bash
pip install -e ".[dev]"
ticket-agent serve --seed 60
# 浏览器打开 http://127.0.0.1:8000/ ，点左边任意一张工单，按「▶ 处置该工单」
```

不需要 API key，不需要 GPU。

---

## 它解决什么问题

电商客服里 **78.6% 的诉求不是退款**（催件、改址、开票、价保、补发、换货、赔付）。
通用 chatbot 只会说「请谅解」，然后转人工。

这个项目做的是**真正把事办掉的那一层**：

```
客户来信 → 定位订单 → 判定资格（R1–R14）→ 按结论分四路：
  ├─ 能办     → 调写工具执行 → 关单
  ├─ 不能办   → 驳回 + 给理由
  ├─ 信息不足 → ask_customer 追问（不写在散文里，落库）
  └─ 超权限   → escalate_to_human → 人工审批面板
```

14 类诉求、13 个工具、4 个会写库。政策（R1–R14）写在**代码里由工具层强制执行**，
不是写在 prompt 里请求模型遵守 —— prompt 约束可以被绕过，代码约束绕不过去。

---

## 实测读数

| | 规则基线（scripted） | 真实模型（Hy3，200 case × k=3） |
|---|---|---|
| 单次准确率 | 100%† | single 45.8% / multi 42.3% |
| pass^3 | 100%† | single 40.5% / multi 27.5% |
| **违规动作** | **0** | **0** |
| 成本 | ¥0.0036 / ¥0.0096 | ¥0（限时免费） |

† 规则基线直调 `rules.decide()`，而 golden 由同一套规则推导 —— 它的 100% 是
**评分标准下的上界参考**，证明评测链路是通的，不是模型成绩。

**最该注意的不是那两个百分数，是 `违规动作 0`。**
1200 次真模型运行，没有一次把钱退错、没有一次越权调用、没有一次跳过资格判定就关单。
它失败的方式全部是「**判对了但没收尾**」—— 工单结论正确，`ticket.status` 停在 `open`。

这也是核心方法论：**只看终态才能抓到这种东西。**
如果只看 agent 的回复文本，这 1200 次里它说的话几乎全都对。

---

## 文档

README 只讲「它是什么、怎么跑、读数是多少」。细节按主题分文件，各带「当时答的是哪一问」：

| 想知道 | 看 |
|---|---|
| 架构与设计取舍 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 从零照着敲一遍 | [docs/RUNBOOK.md](docs/RUNBOOK.md) |
| 部署（Docker / 本机） | [docs/DEPLOY.md](docs/DEPLOY.md) |
| 测试怎么跑、测什么 | [docs/TESTING.md](docs/TESTING.md) |
| 评测方法论（终态 / pass^k / 消融） | [docs/EVAL.md](docs/EVAL.md) |
| HTTP 接口 | [docs/API.md](docs/API.md) |
| 接哪个模型、成本怎么算 | [docs/ENDPOINTS.md](docs/ENDPOINTS.md) |
| 做过什么、你该做什么 | [docs/OVERVIEW.md](docs/OVERVIEW.md) |

---

## License

[MIT](LICENSE) © 2026 青凯 (qk888)

> `data/raw/` 下的语料来自多个公开数据集，各自带自己的许可，**不随本仓库的 MIT 授权分发**。
> 本仓库只提交语料的构建脚本与说明，不提交语料本体（见 `.gitignore`）。
