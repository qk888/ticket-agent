# 三十五个真实缺陷

> 这一份是 35 个缺陷的取证链。每个都按同一个格式写：**现象 → 怎么定位 → 根因 → 修法 → 回归**。
>
> 它们的共同点比各自的技术细节更值钱：**一条流程被拆成两半，一半做了另一半没做，中间没有任何东西会变红。**
> 「默认配置能跑通」不是验证，只是没被验证过。
>
> 想先看整体架构去 [docs/ARCHITECTURE.md](ARCHITECTURE.md)，想看评测怎么设计去 [docs/EVAL.md](EVAL.md)。


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

这个缺陷的特别之处在于：**它是修完 ⑫ 之后紧接着引入的**，
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