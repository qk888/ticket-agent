# 数据集线收尾：两份干净报表 + 一个被差点漏掉的数据质量问题

## 一、现在的状态（22:00 那批 1200 run 已全部有干净报表）

| 臂 | 运行数 | pass^3 | 单次准确率 | 平均模型调用 | 平均工具调用 | 平均写操作 | 违规 | 平均延迟 |
|---|---|---|---|---|---|---|---|---|
| `single` | 600 | **40.5%** | 45.8% | 2.77 | 3.21 | 0.53 | 0 | — |
| `multi` | 600 | **27.5%** | 42.3% | 5.22 | 4.10 | 0.45 | 0 | 55.2s |

两份报表：`eval_out/eval-single-llm-200c.md`、`eval_out/eval-multi-llm-200c.md`。
覆盖度审计：**期望的全部条件均有 case 覆盖**（30 个条件）。

## 二、先说最要紧的一件事：报表一度是被污染的，污染源不是 glob

上一轮我以为污染来自 `_prun.py` 的 glob 吞掉上一代 shard2of2 残留（4 条 scripted 记录，`kind=scripted size=8 customers=40`）。**这个判断只对了一半** —— 那 4 条确实该剔，但真正让 multi 报表数字错得离谱的是另一回事：

**出报表时 sidecar 已经挂了，`_resume.py` 照常跑，每条 22 秒快速失败，写出一条 `tools=0 model_calls=3 action=none` 的记录，然后把它当成「已跑完」。**

危险在于进度文件是 append-only 的「已完成」清单。空跑记录一旦写进去，`_resume.py` 永远认为这条跑过了，永远不会重跑；而报表会把它算进分母 —— 一张安静地偏低、且**没有任何模型参与**的报表。

我第一次清理时的判据是「零 token 记录就是污染」，**错了**。实测原始 4 个分片里 320/600 条本来就是零 token（`model_calls=3` 但 `input_tokens=0`，elapsed≈7s）。那是 Hy3 的 reasoning-only 行为：思考 token 不走 usage 字段。**拿一个正常现象当污染，会把 320 条真读数删掉。**

真正的区分办法是**键集合对比**：拿原始 4 个分片的 `(case_id, arm, k_index)` 集合做基准，merged 里多出来的键才是污染（实测多 190 个，全是 `turns=1 tools=0 act=none el≈22s`）。新工具 `_cleanmerge.py` 就是干这个的。

## 三、出报表又卡了两次，最后绕开了数据库

`_resume.py --report` 这条路两次卡死：`shard-multi-0.db` 被一个 15:16 启动的僵尸 python（PID 7312）占着锁，跑了 1 小时 CPU 只涨 6.4 秒 —— 卡在锁等待，不是在算。换 `shard-multi-1.db` 也卡（CPU 停在 5.2s 不涨）。

**墙钟等了 20+ 分钟，零进展。**

新工具 `_report_only.py` 绕开库：`build_report(results, k=...)` 只吃 `CaseResult` 列表，所有需要的字段都在进度文件里。`_resume.py` 之所以去开库，是因为它把「重建评测集」和「出报表」写在同一条路上。拆开后 multi 报表 2 秒出完。

## 四、这两份数字里有一个必须讲清楚的口径问题

**53.3% 的 multi 运行（320/600）工具调用数为 0**，`model_calls=3`，elapsed≈7.3s；其余 47% 工具调用 5–11 次，elapsed≈92.6s。

两件事同时成立，别混为一谈：

1. **其中 190 条是 sidecar 挂掉后的空跑**（elapsed≈22s，已被 `_cleanmerge.py` 剔掉，不在上面两张表里）。
2. **剩下的 130 条 tools=0 是真读数** —— 它们和真跑的区分点是 elapsed≈7.3s、model_calls=3、有真实 diffs（`ticket.status: 期望 'pending_human'，实际 'open'`）。模型确实被调用了，只是**一个工具都没调**。

第 2 点不是数据问题，是**能力问题**，而且是这批数据里最大的一个信号。报表里能直接看到它的后果：

```
multi 臂条件表里 tools=0 最严重的条件
  UNPAID:reject          12/12  100.0%   平均工具调用 0.00   准确率 0.0%
  R13:change_address      9/ 9  100.0%   平均工具调用 0.00   准确率 0.0%
  CANCELLED:reject        3/ 3  100.0%   平均工具调用 0.00   准确率 0.0%
  R2:need_clarification   3/ 3  100.0%   平均工具调用 0.00   准确率 0.0%
  R8:exchange             3/ 3  100.0%   平均工具调用 0.00   准确率 0.0%
  R9:reject               3/ 3  100.0%   平均工具调用 0.00   准确率 0.0%
```

这些条件的共同点：**agent 凭客户原话就直接给动作，没有调 `get_order` / `list_order_items` / `days_since_signed` 验证**。「平均工具调用 0.00」和「准确率 0.0%」在同一行上 —— 这不是巧合。

这正是过程探针想抓的东西，但探针只记 `forbidden_hits`（当时是 0）—— 因为「不查就办」不在四个探针的定义里。**这是探针的又一个盲区，形状与缺陷 ⑲（verifier 被模型散文放行）同族：裁判只判了它知道要判的那几种。**

同一现象在 single 臂也在：316/600（52.7%）tools=0，但 single 的 tools=0 里 `model_calls=1`、elapsed≈2.8s —— single 是「一轮直接答」，multi 是「三轮对话后仍不调工具」。**同一个病，两个臂的表现不同。**

## 五、两臂直接对照（同 600 个 `(case_id, k_index)`）

| | single 过 / multi 挂 | multi 过 / single 挂 |
|---|---|---|
| 条数 | 130 | 109 |

按条件看 multi 净胜的：

```
  R1:reject            multi赢 20  single赢  7  净 +13
  R2:reject            multi赢 11  single赢  2  净  +9
  R13:reject           multi赢  8  single赢  3  净  +5
  R14:urge_logistics   multi赢  7  single赢  3  净  +4
  R12:reject           multi赢  3  single赢  0  净  +3
  R11:reject           multi赢  3  single赢  0  净  +3
```

按条件看 single 净胜的：

```
  no_order            multi赢  5  single赢 16  净 -11
  R4:escalate         multi赢  3  single赢 12  净  -9
  R9:reship           multi赢  1  single赢 10  净  -9
  R8:reject           multi赢  2  single赢 11  净  -9
  R5:escalate         multi赢  4  single赢  8  净  -4
  R9:need_clarification multi赢 0 single赢 4  净  -4
```

**读法**：multi 在「该驳回」类条件上明显更好（verifier 真的起了作用，把 single 会误放的行拉回来）；在「该转人工 / 该补发 / 无订单」类条件上明显更差 —— 这些恰恰是最该起第二个 agent 的场景，而现在第二个 agent 只做只读复核，不做补救。

这与 40 条小样本上 multi 净负面（65.0% vs 90.0%）是**同一个方向，但幅度小得多**：扩到 200 条后 multi 从「净负 25 个点」变成「净负 13 个点」（单次 42.3% vs 45.8%）。样本变大，多智能体的真实增益才显出来 —— 它确实在它擅长的条件上赢，在它不擅长的条件上输，小样本上看不到这个结构。

## 六、多模型并行加速：这条路当前走不通，说清楚为什么

有人提议「把 opencode 里那 9 个免费模型都用上，各跑一部分，不就快了吗」。**方向对，但当前不可行**，而且有一条是即使可行也不该做的。

### 1. 事实层：sidecar 只吐一个模型

`tools/_modelprobe.mjs` 打 sidecar `/v1/models`：**共 1 个模型**（`hy3`）。

所以「把能跑通的模型都用上」第一步就过不去 —— 手上只有 hy3 一个可用端点。

### 2. 语义层：把同批 case 拆给不同模型会毁掉指标

这条比第一条更要紧。评测读数是「**这个模型**在这个任务集上的能力」。

- 把 `acme-t100` 给 hy3、`acme-t101` 给 longcat，出来的 `single` 42.3% 是什么？
  它不是任何一个模型的读数，是 9 个模型的混合分布。
- 按条件分层时更糟：如果某个条件恰好全分给了弱模型，那个条件的 0% 会被读成「这个条件难」，实际是「这个模型弱」。
- `pass^3` 也被毁：同一条 case 的 k=0/1/2 若分给不同模型，「三次全对才算过」量的是模型间方差，不是模型内一致性。

**结论：并行度可以加在「同一个模型的多个进程」上，不能加在「多个模型分摊同一批 case」上。**

### 3. 可行的加速（都已实测）

| 手段 | 实测结论 |
|---|---|
| 同模型多进程分片 | 8 路（4 片 × 2 臂）是甜点。并发 4/8/16 的中位延迟 4861/3982/3938ms（未饱和），但最慢值 4862→11473→7181ms 随并发线性拉长 —— 再加只让慢 case 更慢 |
| 断点续跑 | `_resume.py`：每条 case 开头 `apply_snapshot` 重置回 `initial_state`，**每条自洽幂等**，续跑与一口气跑完逐位相同；断电最多丢正在跑那一条（≈80s） |
| GPU | **用不上** —— hy3 是 WorkBuddy 插件在本机服务的远程模型，瓶颈在等模型回，`wb_sidecar.mjs` 只是 SSE 转发器 |

### 4. 如果将来真有多模型：两个必须先做的前置

1. `run_sig` 指纹**不含 model** —— 多模型共用一个进度文件会互相认成「已跑完」。必须先把模型名编进指纹与进度文件名。
2. 正确的做法是**每个模型各出一份完整报表，再横向对照**（那本身就是「多模型选型」这个新指标），而不是把一个模型的报表拆成 9 份。

## 六、sidecar 还挂着

```
Cannot find package '@deepseek-ai/dsh-home-paths'
imported from ...\node_modules\dsh-workbuddy-connect\lib\host-heartbeat-L93zSVB5.js
```

`C:\Users\Administrator\.dsh\profiles\desktop\node_modules\dsh-workbuddy-connect\node_modules\` 下 6 个包目录**全是空目录**（有目录无 package.json）：`dsh-atomic-write`、`dsh-home-paths`、`dsh-llm`、`dsh-llm-pi-ai`、`schemastery`、`@earendil-works/pi-ai`。

已写三个脚手架（`ticket-agent/tools/`）：`_sidecar_boot.mjs`（boot 入口 + resolve hook）、`_atomic_hook.mjs`（只拦指定 specifier）、`_atomic_stub.mjs`（`dsh-atomic-write` 的等价 stub，rename 实现原子写）。

**还没解决**：`dsh-home-paths`、`dsh-llm`、`dsh-llm-pi-ai`、`pi-ai` 这四个 stub 不出来 —— `resolveDshHome` 决定 `~/.dsh` 路径、`PiAiAdapter` 是真的模型适配器，乱写等于伪造推理路径。

**不救 sidecar 也能做的事**：所有 `kind=scripted` 的跑批（规则基线，零 LLM，本来就不需要 sidecar）。
