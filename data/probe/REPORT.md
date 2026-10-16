# 中文客服 / 工单对话数据集 · 穷尽式调研清单

调研完成时间：本轮会话
产出目录：`ticket-agent/data/probe/`（样本在 `samples/`，脚本在同级 `*.py`）
**未修改 `ticket-agent/` 下任何现有文件。**

---

## 一、可用数据源清单

> 只列**已实际下载或已用 HTTP Range 校验过可下载**的。★ = 推荐度。

| # | 数据集 | 平台 | 场景 | 中文? | 量级 | 格式 | 关键字段 | 下载URL | 备注 |
|---|---|---|---|---|---|---|---|---|---|
| 1 ★★★ | `modelscope_mp_677764216/E_commerce_Customer_Service`（电商客服_单轮对话） | ModelScope | **真实京东电商客服**（售前+售后） | ✅简体 | **226,600 行**（104.7MB）；dev 7.4MB、test 1.5MB | JSONL | `messages[]{role,content}`（system/user/assistant 三段固定） | `https://modelscope.cn/api/v1/datasets/modelscope_mp_677764216/E_commerce_Customer_Service/repo?Revision=master&FilePath=train_clean_v2_small.jsonl` | **已溯源确认是 JDDC 京东客服语料的「去分词自然文本版」**：与 JDDC `question_answer.txt` 重合 42.7%，加多轮末句共 56.6%，覆盖比 JDDC 公开四文件更大。客户话术去重 **86,844** 条，长度 p50=8 / p90=19 / max=295。售后关键词命中 6,769/226,600=3.0% |
| 2 ★★★ | `xiaolinAndy/CSDS` | GitHub | **真实京东客服对话 + 对话摘要**（EMNLP 2021） | ✅简体 | 800 段对话 / 800 条摘要 | TXT（每行一段） | 对话行 = `用户:… 客服:…`；摘要行 = `用户询问…客服回答…` | 对话 `https://cdn.jsdelivr.net/gh/xiaolinAndy/CSDS@master/results/final/ext_gold_refs.txt`（297,138 B）<br>摘要 `https://cdn.jsdelivr.net/gh/xiaolinAndy/CSDS@master/results/final/fast_rl_mod_true_preds.txt`（224,280 B）<br>用户侧摘要 `.../bert_rl_block5_refs.txt`（243,330 B） | **售后占比最高：513/800 = 64.1%**（退款108 售后97 退货96 快递96 发票87 发货73 换货40 价保37 运费37 退回34）。每会话发言 p50=6 / p90=11 / max=27；用户句去空格长度 p50=11。实体已脱敏为 `[数字]/[姓名]/[地址]/[电话]` |
| 3 ★★★ | `smau0441/Intent_Recognition_and_Image_Classification_in_E-commerce_Scenarios` | ModelScope | **真实电商客服多轮对话 + 48 类意图标签**（WWW2025 挑战赛） | ✅简体 | **17,442 条**（22.6MB） | JSON | `id / instruction / input / output / image`；对话原文在 `instruction` 内嵌，标签在 `output` | `https://modelscope.cn/api/v1/datasets/smau0441/Intent_Recognition_and_Image_Classification_in_E-commerce_Scenarios/repo?Revision=master&FilePath=all.json` | 真实生产对话（含颜文字 `/:068~~`）。48 标签含「退款页面 370」「退货页面 240」「发货数量 583」。`input` 全空；含 `<image>/<http>` 占位符，需正则抽对话 |
| 4 ★★ | `albertkingdom/custom-service-ticket-zh-tw` | HF 镜像 | **电商客服工单（六类意图 + 结构化抽取）** | ✅繁体台湾 | **3,148 条**（1.66MB） | JSONL | `ticket{ticket_text,category,primary_intent,order_or_product_info,emotion_intensity,needs_human}` + `is_multi_intent,label_status,validator_agreement_ratio` | `https://hf-mirror.com/datasets/albertkingdom/custom-service-ticket-zh-tw/resolve/main/{train,validation,test}.jsonl` | **唯一「工单形态（非对话）」+ 全标签体系**。合成（teacher LLM 生成，README 明说不含真实工单）。`label_status` 分 `confirmed`/`boundary`，评测须分开计分 |
| 5 ★★ | `zhouyang55666/customer_json` | ModelScope | **任务型对话：意图+槽位+澄清问句+DST** | ✅简体 | ecom 209.7MB（≈**320,637 行**）+ 多行业 101.5MB | JSONL | `messages[]{role,content}`，assistant 输出 JSON（domain/intent/slots 或 DST 状态） | `https://modelscope.cn/api/v1/datasets/zhouyang55666/customer_json/repo?Revision=master&FilePath=data/ecom/ecom_v2_200m.jsonl`<br>标签体系 `...&FilePath=metadata/label_catalogue.json` | **纯合成**（模板+词池，句式机械）。但标签体系最完整：30 domains / 49 slot_labels / **68 scenarios（ecom 34）**，含 `ecom:after_sales:APPLY_RETURN/APPLY_EXCHANGE/APPLY_REPAIR/QUERY_PROGRESS/CANCEL` |
| 6 ★★★ | `Dikea/Dialog-System-with-Task-Retrieval-and-Seq2seq` | GitHub | **JDDC 京东客服原始语料** | ✅简体（空格分词） | QA **100,000 行 / 7,600,478 B**；**context_response 100,000 行 / 16,037,538 B**；**multi_train 500,000 行 / 80,261,749 B**；single_train 38,044,830 B（均已全量下载验证 UTF-8 完整） | TXT（`客户\t客服`） | 两列 `客户\t客服`；多轮用 `<s>` 分隔 | QA `https://cdn.jsdelivr.net/gh/Dikea/Dialog-System-with-Task-Retrieval-and-Seq2seq@master/search_dialog/data/question_answer.txt`<br>多轮 `.../seq2seq_dialog/data/multi_train.txt`<br>单轮 `.../seq2seq_dialog/data/single_train.txt`<br>上下文 `.../search_dialog/data/context_response.txt` | **是空格分词版**（`加在一起 买 不用 邮费 吧`），要用得自己 detokenize。**multi_train 全量**：列数≠2 的行 0，**含 `<s>` 多轮行 408,903 = 81.8%**，每会话客户句数 p50=2 / p90=6 / max=60，**客户句 1,627,366 条 / 去重 163,647**，长度 p50=8 / p90=20 / max=314；售后关键词命中 **33,697 行 = 6.7%**（去重句 6,365）。**context_response 全量**：含 `<s>` 多轮行 **81,877 = 81.9%**，客户侧分句 325,553 条 / 去重 **103,054**，售后去重句 3,797。**question_answer 全量**：客户句 100,000 / 去重 **59,467**，长度 p50=8 / p90=19 / max=295，售后去重句 **2,276 = 3.8%**。大文件 jsdelivr 403，须用 ghapi 或 codeload |
| 7 ★★ | `zhangbo2008/JDDC_for_train_gpt_data` | GitHub | **JDDC chatglm SFT 版（天然多轮）** | ✅简体 | **64,321 条**（53.1MB） | JSON 数组 | `prompt / response / history`（history 为 `[[user,assistant],…]` 逐轮累积） | `https://cdn.jsdelivr.net/gh/zhangbo2008/JDDC_for_train_gpt_data@main/extract_train.json`（jsdelivr 403，走 ghapi 或 `codeload.github.com/{repo}/tar.gz/refs/heads/main`） | **多轮占比 83.0%**（history≥1）；prompt 去重 47,489，长度 p50=11 / p90=46 / max=527；售后关键词命中 4,977/64,321=**7.7%**（比 QA 单轮版高）。实体占位 `[数字x]/[姓名x]` |
| 7b ★★★ | `zhangbo2008/JDDC_for_train_gpt_data` → `data/chat.txt` | GitHub | **JDDC 会话级原始日志（多轮结构最完整）** | ✅简体 | **212,808 条消息 / 10,945 个会话**（20,851,245 B） | TSV | `session_id / user_id / waiter_send / is_transfer / is_repeat / content` | `https://codeload.github.com/zhangbo2008/JDDC_for_train_gpt_data/tar.gz/refs/heads/main` 解出的 `data/chat.txt` | **这是最好用的多轮结构**：`waiter_send=0` 是客户（98,143 条 / 去重 60,244）、`=1` 是客服（114,665 条）；每会话条数 p50=17 / p90=30 / max=79；`is_transfer` 43 条、`is_repeat` 84,357 条；**售后去重客户句 4,875 条**；客户消息长 p50=7 / p90=26 / max=491。会话内消息已按时间排好，**直接就是「工单+多轮上下文」** |
| 8 ★★ | `ygan/Chinese-Ambiguous-Reference` | GitHub | **中文购物场景对话 + 歧义句标注** | ✅简体 | **1,104 段**（2.19MB） | JSON 数组 | `date/location/merchandise/shop/display_method/total/shopBackground/total_split/split_sentence_type` | `https://cdn.jsdelivr.net/gh/ygan/Chinese-Ambiguous-Reference@main/All%20Conversation.json`（**路径含空格必须 URL-encode**） | **真实人工撰写（非合成）**。`display_method=3`（网店客服文字对话）500 段。说话人标注 `顾客：/客服：/售货员：`。**主体是售前选购，售后极少**；`shopBackground` 店铺长描述可当知识库素材 |
| 9 | `icebergyang/decision-model-tutorial` | ModelScope | **中文客服工单 + 标签 + 理由 + 脱敏对照** | ✅简体 | test 21,138 B + calibration 11,535 B（几百条） | JSONL | `id/split/family/text/label/reason/redacted_text/redacted_label/redaction_reason` | `https://modelscope.cn/api/v1/datasets/icebergyang/decision-model-tutorial/repo?Revision=master&FilePath=data/test.jsonl`<br>`...&FilePath=data/calibration.jsonl` | **天然带「信息不足」负样本**（脱敏后判不出队列 → `redacted_label:"信息不足"`），对「何时该反问/转人工」评测极对口。量小 |
| 10 | `QingshanAI/ecom-after-sale-synthetic` | ModelScope | 电商售后维权（合成） | ✅简体 | **997 行**（264,864 B） | JSONL | `scene/user/bot/tone` | `https://modelscope.cn/api/v1/datasets/QingshanAI/ecom-after-sale-synthetic/repo?Revision=master&FilePath=data.jsonl` | scene 集中：退换货550 质量问题117 维权投诉107 差评98 补偿方案86 退款20。**纯合成**、模板化 |
| 11 | `QingshanAI/ecom-logistics-synthetic` | ModelScope | 电商物流（合成） | ✅简体 | **987 行**（261,404 B） | JSONL | `scene/user/bot/tone` | `https://modelscope.cn/api/v1/datasets/QingshanAI/ecom-logistics-synthetic/repo?Revision=master&FilePath=data.jsonl` | 物流停滞/丢件破损/催发货 |
| 12 | `QingshanAI/ecom-customer-service-synthetic` | ModelScope | 电商客服（合成） | ✅简体 | **992 行**（182,808 B） | JSONL | `scene/user/bot/tone` | `https://modelscope.cn/api/v1/datasets/QingshanAI/ecom-customer-service-synthetic/repo?Revision=master&FilePath=data.jsonl` | scene 极碎（59 种） |
| 13 | `DAMO_ConvAI/ZhDoc2BotDialogue` | ModelScope | **文档驱动对话（doc-grounded，医疗）** | ✅简体 | 21.3MB | CSV | `query / passages / response` | `https://modelscope.cn/api/v1/datasets/DAMO_ConvAI/ZhDoc2BotDialogue/repo?Revision=master&FilePath=dialog_zh_train.csv` | **非电商**，但「query + 检索知识 + response」三段式与「工单+知识库+回复」高度同构，可作格式参考。多轮用 `<last_turn>/<agent>/<user>/<response>` 标签 |
| 14 | `thu-coai/kdconv` | HF 镜像 | 中文知识驱动多域对话 | ✅简体 | 121.9MB | JSON | `{film,music,travel}/{train,dev,test}.json` + `kb_*.json` | `https://hf-mirror.com/datasets/thu-coai/kdconv/resolve/main/film/train.json` | 电影/音乐/旅行，**非客服**，带 KB |
| 15 | `GEM/CrossWOZ` | HF 镜像 | 中文多域任务型对话 | ✅简体 | 6K 会话 / 102K 语句 | ZIP | `data.zip` 内 JSON | `https://hf-mirror.com/datasets/GEM/CrossWOZ/resolve/main/data.zip`（16,338,148 B） | 酒店/餐厅/景点，**非电商** |
| 16 | `TingchenFu/ChineseDialogue` | HF 镜像 | 中文对话合辑 | ✅简体 | 7 个 jsonl | JSONL | 每行一个 JSON 数组 = 一段对话 | `https://hf-mirror.com/datasets/TingchenFu/ChineseDialogue/resolve/main/kdconv.jsonl` | 含 douban/duconv/kdconv/lccc/naturalconv/personalized_dialogue/weibo。**通用闲聊，非客服** |

---

## 二、推荐前 3 名及理由

### 🥇 第一名：`modelscope_mp_677764216/E_commerce_Customer_Service`（电商客服_单轮对话）

**适合用来做什么**：**主力造数**（替换 `seed.py` 的手写模板）+ 售前/售后意图分层的底料。

**为什么是它**：
1. **真实**——不是合成。已用逐行比对溯源证明它就是 JDDC 京东客服语料，只是把 `加在一起 买 不用 邮费 吧` 还原成了 `加在一起买不用邮费吧`，**省掉了自己 detokenize 的活**。
2. **规模够**——226,600 行、客户话术去重 86,844 条，单文件就能撑起「大量数据集」的要求。
3. **字段直接可用**——`{"messages":[{system},{user},{assistant}]}` 就是标准的 chat 格式，不用写解析器。
4. **覆盖比公开 JDDC 更大**——未被 JDDC 公开四文件覆盖的客户话术有 49,775 条（57.3%），说明它含京东更多分片。

**字段映射**：
| 目标字段 | 来源 | 说明 |
|---|---|---|
| 客户诉求 | `messages[1].content`（role=user） | 直接就是客户原话 |
| 意图标签 | ❌ 无 | 需自己用关键词/模型打标（售后关键词命中 3.0%） |
| 订单 | ❌ 无结构化订单号 | 正文里偶有「订单」「单号」字样，需正则抽 |
| 客服回复 | `messages[2].content`（role=assistant） | 可当回复参考 |
| system prompt | `messages[0].content` | 是客服人设，**不是业务字段，入库时要丢掉** |

**局限**：
- 客户话术很短（p50=8 字），像「工单正文」需要把多轮拼起来，不能直接单句当工单。
- 售后占比只有 3.0%，售前咨询（「两种是一样的吗」「这个是干湿两用吗」）占绝大多数。
- 含真实口播噪声：错别字、无标点（例：`没回复你也没给我说啊而且又是我再次找你们你们这也不负责啊我昨天才做的剖腹产还要忍着巨疼天天追件`）——**这是优点（真实）也是难点（脏）**。

---

### 🥈 第二名：`xiaolinAndy/CSDS`（EMNLP 2021 中文客服对话摘要）

**适合用来做什么**：**售后工单正文 + 工单摘要的配对训练/评测**。

**为什么是它**：
1. **售后占比碾压所有其他候选：64.1%**（513/800 段会话含售后关键词）。退货、退款、换货、价保、发票修改、补发、维修——正是「售后工单处置 Agent」的目标场景。
2. **论文级真实语料**——EMNLP 2021 主会论文，京东真实客服对话，实体已脱敏成 `[数字]/[姓名]/[地址]/[电话]`，能直接进库不涉隐私。
3. **自带「对话 → 第三人称摘要」配对**——800 段对话 + 800 条摘要一一对应，y 摘要长这样：`用户需要手机绑定手机号。客服回答升级专员会在明天[数字]点前回电…`——这就是「工单摘要」的现成模板。

**字段映射**：
| 目标字段 | 来源 | 说明 |
|---|---|---|
| 客户诉求 | 对话行里 `用户:` 之后的全部句子 | 一段会话含多条用户发言，可整段作为工单正文，也可只取首句 |
| 意图标签 | ❌ 无显式 category | 但可从摘要文本推（「退款」「发票」「换货」等词）
| 订单 | 摘要里出现的商品/订单描述 | 如「手机绑定手机号」「发票开明细」 |
| 工单摘要 | `fast_rl_mod_true_preds.txt` 同序号行 | 一行对话对应一行摘要 |
| 转人工标记 | 无 | 需自建 |

**局限**：
- **只有 800 段**，做造数不够，做评测集正好。
- 无显式类目标签（不像第 4 名有 `category`），得自己打。
- 原始标注数据（含更多字段）在百度网盘，本环境不可达；仓库里的 txt 是评测输出格式。

---

### 🥉 第三名：`smau0441/Intent_Recognition_and_Image_Classification_in_E-commerce_Scenarios`（WWW2025 电商意图识别）

**适合用来做什么**：**意图层评测**（48 类细粒度标签，有标准答案）。

**为什么是它**：
1. **唯一一个规模大 + 有真实意图标签的数据集**：17,442 条真实电商客服多轮对话，每条带 48 类标签之一的 ground truth。
2. **标签含售后类**：「退款页面」370 条、「退货页面」240 条、「发货数量」583 条、「实物拍摄(含售后)」1279 条——够撑起售后意图的评测子集。
3. 标签是**页面/行为级**而非泛泛的「咨询/投诉」，粒度细到能区分「订单详情页面」和「账单/账户页面」。

**字段映射**：
| 目标字段 | 来源 | 说明 |
|---|---|---|
| 客户诉求 | `instruction` 里 `<用户与客服的对话 START>…END` 之间的 `用户:` 行 | 需正则抽取，一条含多轮 |
| 意图标签 | `output` | 48 类枚举，**直接可用** |
| 订单 | ❌ 无 | 对话里可能提到页面，但无订单号 |
| 客服回复 | `instruction` 里的 `客服:` 行 | 可作回复参考 |

**局限**：
- 是「多模态赛题」产物：对话里混着 `<image>` / `<http>` 占位符，**图片实体（7GB `images.zip`）与文本分离**，本任务不用图片的话要先把占位符清掉。
- 只有 `output` 一个标签，**无槽位、无订单字段**。
- 对话是「用户发图 → 客服说识别不了图」这种多模态噪声对话占比不小。

---

### 🏅 补充推荐：`data/chat.txt`（JDDC 会话级原始日志）——**要「完整多轮上下文」就用它**

**适合用来做什么**：**构造真实的多轮工单上下文**（测「信息不足时该反问」「何时该转人工」）。

**为什么值得单独列**：前面 16 个数据源里，只有它是**真正把一次客服会话按时间顺序完整串起来**的——10,945 个会话、212,808 条消息，每行带 `session_id` 分组、`waiter_send` 显式区分客户/客服（不用猜）、`is_repeat` 标记重复消息、`is_transfer` 标记转接。前面第 1 名的 `E_commerce_Customer_Service` 虽然量大，但已经被压成「单轮 user→assistant」；第 7 名的 `extract_train.json` 的 `history` 是逐轮累积的**快照**（同一会话重复出现多次）；只有 `chat.txt` 是**一次会话一行一条、去重前**的原貌。

**字段映射**：
| 目标字段 | 来源 | 说明 |
|---|---|---|
| 会话 id | `session_id` | 直接当工单 id / 会话 id |
| 客户诉求（多轮） | `content` where `waiter_send=0` | 按 `session_id` 聚合即得该会话全部客户发言 |
| 客服回复 | `content` where `waiter_send=1` | 对应的客服侧发言 |
| 顺序 | 文件内自然顺序 | 已按会话内时间排好，**无需排序** |

**局限**：① 客户消息只有 13,522 条去重前 / **1,858 条去重后**（大面积重复发送，`is_repeat=1` 占 84,357 条），实际信息量小于行数第一印象；② 长度极短，p50=7 字；③ 仍是**空格分词**版，要自建 detokenize；④ 会话数 10,945 比 `multi_train` 的 500,000 行小得多。

---


### 样例 1 — 来自 `modelscope_mp_677764216/E_commerce_Customer_Service`
文件：`ticket-agent/data/probe/samples/ms_ecom_train_FULL.jsonl` 第 1 行
```json
{"messages": [{"role": "system", "content": "你是一个专业的电商客服。严格遵守：1) 仅以‘assistant’角色回答一次；2) 不要提出新问题，不要延长对话；3) 不要生成‘user’或其他角色的内容；4) 输出只包含答案正文，无前后缀角色标签、无思考过程。"}, {"role": "user", "content": "两种是一样的吗"}, {"role": "assistant", "content": "一样的呢就是数量不同哦亲"}]}
```

同集**售后类**客户原话（证明售后话术真实存在，全部为 `messages[1].content` 原文）：
```
退货地址给我
没有办法就退款吧
我拍错了可以退货吗在吗亲
昨天不是补发了运单号为什么要明天给
要么我还是退了重拍我怕发错就麻烦了帮人家买的
早知道你这边发货这么慢我早退款了也不会拖这好几天说了发货也没发这不是信你说的了么说会发货
你把对快递公司的投诉给人家解决了
之前给我漏掉一件防溢乳垫有没有帮我补发
不要了把钱退回来
```

### 样例 2 — 来自 `xiaolinAndy/CSDS`
文件：`ticket-agent/data/probe/samples/gh_csds_goldrefs_FULL.txt` 第 1 行（**对话**）
```
用户:以前的手机号码销号了，密码也忘了。用户:改密码需要手机验证码。客服:这边帮您升级专员会在明天[数字]点前给您回电方便提供下您要绑定的手机号码吗。用户:嗯，我现在终于想起密码了，登录进来了，但还是要验证我以前的手机号码。客服:我们这边已经帮您反馈专员了。用户:不验证现在可以买东西吗。客服:还请您耐心等待下。
```
文件：`ticket-agent/data/probe/samples/gh_csds_truepreds_FULL.txt` **同序号第 1 行（对应摘要）**
```
用户需要手机绑定手机号。客服回答升级专员会在明天[数字]点前回电方便提供下绑定的手机号码。用户询问不能邮箱改密码，是否可以买东西。客服回答需要按照流程走。用户表示现在终于想起密码，登录进来了，但还是要验证我以前的手机号码。客服回答已经反馈专员。用户询问是否可以看见之前反馈的问题。客服回复可以。
```

### 样例 3 — 来自 `albertkingdom/custom-service-ticket-zh-tw`
文件：`ticket-agent/data/probe/samples/hf_zhtw_ticket_train_FULL.jsonl` 第 1 行
```json
{"ticket":{"ticket_text":"你好，我的服飾配件包裹已經到超商快接近保留期限了，但我還沒收到任何取貨通知耶，想請問是正常流程嗎？謝謝！","category":"物流查詢","primary_intent":"詢問超商取貨通知狀況","order_or_product_info":"服飾配件","emotion_intensity":1,"needs_human":false},"is_multi_intent":false,"label_status":"confirmed","validator_agreement_ratio":1.0,"needs_human_agreement_ratio":1.0,"emotion_dispersion":0.0}
```

### 样例 4 — 来自 `smau0441/Intent_Recognition_and_Image_Classification_in_E-commerce_Scenarios`
文件：`ticket-agent/data/probe/samples/ms_www2025plus_FULL.json` 第 1 条（`instruction` 截断展示；`output` 为完整标签）
```json
{"id":"213e438b17282904772446704d0b28","instruction":"你是一个电商客服专家，请根据用户与客服的多轮对话判断用户的意图分类标签。\n<用户与客服的对话 START>\n用户: <image>\n客服: 抱歉哦，小富目前还不能识别图片，您可以简单描述一下遇到的问题吗？/:068~~\n用户: <http>\n客服: 若您仍有疑问，可回复“客服”以便进一步协助您~\n用户: 内胆使用的是316不锈钢吗\n客服: 亲爱的，升级版的抗菌不锈钢是在304不锈钢的基础上改进的，它有抗菌功能…（略）\n<用户与客服的对话 END>\n请直接只输出分类标签结果，不需要其他多余的话。以下是可以参考的分类标签为：[\"反馈密封性不好\",\"是否好用\",…]\n","input":"","output":"商品材质","image":["213e438b172829047728..."]}
```

### 样例 5 — 来自 `icebergyang/decision-model-tutorial`（**「信息不足」负样本**）
文件：`ticket-agent/data/probe/samples/ms_decisiongongdan_test.jsonl` 第 1 行
```json
{"id":"test-001","split":"test","family":"约定入户的大件被留在楼下","text":"这次的处理与之前说好的不一致。大件家具约定送货入户，配送员却只肯放在楼下门厅，请安排完成入户配送。请核对约定后再联系我。","label":"物流","reason":"争议是大件配送未履行入户约定，当前要求完成送货服务。","redacted_text":"这次的处理与之前说好的不一致。请核对约定后再联系我。","redacted_label":"信息不足","redaction_reason":"删除家具、配送员、楼下门厅及入户配送要求所在的整句；只剩未指明业务的约定不一致，无法识别队列。"}
```

### 样例 6 — 来自 `zhouyang55666/customer_json`（**澄清问题生成**）
文件：`ticket-agent/data/probe/samples/ms_customerjson_ecom200m_head.jsonl`
```json
{"messages":[{"content":"你是一个澄清问题生成助手。给定用户模糊的初始请求及其主题、所需信息方面，生成一个具体的澄清问题以消除歧义。只输出澄清问题本身。","role":"system"},{"content":"用户请求: 请帮忙咨询商品信息\n主题: 用户希望咨询商品信息。\n所需信息: 需要补充商品名称、颜色。","role":"user"},{"content":"请补充一下商品名称、颜色，可以吗？","role":"assistant"}]}
```

### 样例 7 — 来自 `zhangbo2008/JDDC_for_train_gpt_data`（**天然多轮**）
文件：`ticket-agent/data/probe/samples_targz/extract_train.json`（JSON 数组）第 1 条
```json
{"prompt":"什么时候能发货啊什么时候能发货啊得等多久啊我家狗狗等着吃饭呢?","response":"请您稍等一下，正在为您核实处理中哦~有什么问题我可以帮您处理或解决呢?[数字x] 耐威克(Navarch) 宠物天然粮[姓名x]犬成犬狗粮[数字x]kg","history":[]}
```

### 样例 8 — 来自 `zhangbo2008/JDDC_for_train_gpt_data` 的 `data/chat.txt`（**会话级原始日志，多轮上下文完整**）
文件：`ticket-agent/data/probe/samples_targz/data__chat.txt`，表头 `session_id	user_id	waiter_send	is_transfer	is_repeat	content`（TSV，`waiter_send=0` 客户 / `=1` 客服）。同一 `session_id` 的前 22 条原文：
```
[客户]什么时候能发货啊
[客户]什么时候能发货啊
[客户]得等多久啊
[客户]我家狗狗等着吃饭呢
[客户]?
[客服]请您稍等一下，正在为您核实处理中哦~
[客服]有什么问题我可以帮您处理或解决呢?
[客服][数字x] 耐威克(Navarch) 宠物天然粮[姓名x]犬成犬狗粮[数字x]kg
[客户]对的。
[客服]这款商品库房还没有到货’
[客户]什么时候能到货啊?
[客服]可以给您操作有货的先发
[客户]零食那个有货呗?
[客户]狗粮一袋都没有么?
[客户]有的话都给我先发吧。
[客户]?
[客服]稍等哈
[客户]恩
[客服]查询以操作了有货先发
[客服]您的订单现在是两个订单
[客服]建议您关注一下订单的物流信息哈
[客户]狗粮啥时候能有货?
```
另一个会话（**售后：退货/退款撤销**，节选）：
```
[客户]咨询订单号:[ORDERID_10005629] 订单金额:[金额x] 下单时间:[日期x]
[客服]有什么问题我可以帮您处理或解决呢?
[客服]亲爱的，耽误您的使用 了非常抱歉哈
[客户]我这个一直没有配送信息，着急用，我可以退货吗
[客服]如果您着急使用
[客服]可以取消订单哦
[客服]耽误这么久的时间，真的灰常抱歉#E-s[数字x]
[客服]还请您谅解哦
[客服]由于一大波包裹袭击快递站点，正在积极送货过程中，还请耐心等待。
[客户]另外我再拍一个别的牌子气筒，显示明天送到，您那能查是否能及时配送吗
[客服]亲爱的，还麻烦您提供下订单号，妹子这边给您查询哦~
[客户]稍等
[客服]#E-s[数字x]
[客户]算了我不退了，估计也就这两天到，再拍不到也麻烦
[客户]但是我点申请退款了，怎么恢复呢
[客服]亲，实在抱歉，让您久等了
[客服]亲，退款申请是无法取消的，系统已经下发拦截了，这个如没有拦截到，正常签收就行呢
[客户]知道了
[客服]妹子这边也会帮您跟进直到您签收为止哦#E-s[数字x]
[客服]还请您放心哈’
[客服]非常抱歉，耽误您的时间了，这次给您添麻烦了，其实，我们也挺不好意思，#E-s[数字x]
[客户]好的，麻烦您也帮忙备注下不取消订单了
```
同集**售后类客户原话**（`waiter_send=0` 且含售后关键词，原文）：
```
你好是这样的今天上午呢我收到了买的那双熟悉的鞋子啊因为不合适所以我下午退货了但退货的时候快递员说是那个信息里面显示的是除了鞋和鞋子之外还有干燥剂和售后卡但是我收到的鞋子里面没有干燥剂和售后款麻烦把这个事情给处理一下谢谢
我之前买了九牧两套花洒，由于家里水龙头原因，只能安装一个，要退货一个，昨日我已申请退货，师傅今天上门收件发现我没把发票放一起，晚间我刚到家已找到发票，货物将明天由快递师傅取件，但是我现在看售后，是申请退货状态，能帮我查看下吗?能否顺利退货呢?
我退货的物品没有退货地址呢
我昨天申请的退货什么时候能审核通过啊?
想问一下换货，要明天货才到手吗?
```

---

## 四、踩到的坑

### A. 看着极好、实际下不了
| 数据集 | 陷阱 |
|---|---|
| `DatatangBeijing/90000sets-Multi-domainCustomerServiceDialogueTextData` | 标称 **9 万组/1,826,837 条**中文多领域客服对话，dl=3100 loves=40，Apache-2.0，看起来是完美答案。**实际仓库里只有 5 张 jpg 示例图 + README.md + dataset_infos.json(169 B)，零数据文件。** |
| `haitianruisheng/ChineseMulti-TurnConversationDataset` | 海天瑞声超大规模中文多轮对话，dl=465。仓库**只有 `样例.xlsx` 9,751 B + README**，全量需商业采购 |
| `Jack00223/ServiceChatData` | 「客服对话数据」，实际只有 `service.txt` **27 字节** |
| `vxdass/help`（售后帮助）、`Robert666888/gongdanyaoshutiqu`（工单要素提取） | 只有 README，无数据 |
| `WhitzardIndex/WhitzardBench-2025-CMT` | zip 只有 **2,552 B**（空壳） |
| `haimianxing/CCSE-CS-Empathetic-Culture-AI` | 只有 scripts/tex/prompts，**无数据文件** |
| `zengbin93/jddc_solution_4th`（JDDC 方案第4名 ★237） | 全仓仅 4.1 MB（方案 PDF + ipynb），**无原始语料**——明星仓库未必带数据，必须拉 tree 看 size |
| `xzwj/JDDC`、`Dukehan1/JDDC`、`hrlinlp/jddc2.1` | 全仓 0.1MB，仅 README |

### B. 是客服数据但**不是中文**
| 数据集 | 事实 |
|---|---|
| `bitext/Bitext-customer-support-llm-chatbot-training-dataset`（dl=10638 likes=199） | tags 明确 `language:en`，**纯英文** |
| `Tobi-Bueck/customer-support-tickets`（dl=5269 likes=42） | 标称多语言，实测 `dataset-tickets-multi-lang-4-20k.csv` 前 400KB **只有 de:142 / en:292，零中文**；`aa_..._5-2-50-version.csv`（标称 50 语言）前 300KB 同样只有 en/de。列结构 `subject/body/answer/type/queue/priority/language/tag_1..tag_8` 很对口，可惜没中文 |
| `W-L/Customer-service-tickets-qwen-qa`、`bdragun/service-desk-tickets`、`disi-unibo-nlp/lam-customer-service-tickets`、`Lakshan2003/customer_service_200k_client_agent_conversations`（24.4 万条、243MB parquet）、`synthdataq9x260918/...-100k`、`AngieYYF/SPADE-customer-service-dialogue` | 全部英文 |

### C. 标题像客服、实际不是
| 数据集 | 实际是什么 |
|---|---|
| `zhangzhihao/Agent-AI-customer-service`（dl=380，标题「大模型与智能体&智能客服系统开发实验数据集」） | 文件是 `en-cn.txt` + `MNIST.zip` + `moe.pt` + `ThuCnews.zip` + `transformer.pt` + `西游记片段.txt` —— **模型训练课素材包** |
| `smau0441/www2025-train`（dl=52882，下载量极高） | 非图像文件只有 `README.md` 4,647 B，**100 张 images/*.jpg 是唯一实体内容**，文本仅 1000 条标注 |
| `InfiniFlow/Ecommerce-Customer-Service-Workflow` | `product_infomation.zip` 里是 **Philips 吹风机 PDF 说明书**，不是对话数据 |
| `su/jingdong`（HF，名字最像京东） | 打开是 **NER 标注格式**（`手 B-40` / `机 I-40`），商品标题序列标注，不是对话 |
| `zyznull/Multi-CPR-ecom`（94MB） | 电商 **passage 检索语料**，非客服对话 |
| `Magic_Data/MagicData-CLAM-Conversation_CN`、`MagicDataTech/multi-stream-spontaneous-conversation-...`、`MagicData/A_CHINESE_CONVERSATIONAL_SPEECH_CORPUS` | **语音转写**对话，非客服 |
| `meadhu/taobao-positive-sentence`（dl=1219） | 淘宝**好评生成**，26KB，且是评论非诉求 |
| `nlcvcln/dialogue_zh`（428MB，标题「中文对话数据集」） | LCCC/人格对话/RLHF **混合闲聊**，非客服 |
| `TingchenFu/ChineseDialogue`（dl=141，中文对话合辑） | douban/kdconv/weibo 等**通用闲聊**；`douban.jsonl` 甚至是**空格分词**的（`昆明 那里 配 眼镜 比较 便宜`） |

### D. 平台 / 协议层的坑
1. **ModelScope `/oss/tree` 空清单假成功**：返回 HTTP 200 但 `Data:[]`、`TotalCount:0`，会让你以为仓库是空的。**唯一可用路径是 `/repo/tree?Revision=master&Recursive=true`**。`/repo/files` 返回 405，`/files` 返回 404。
2. **ModelScope 中文长尾关键词全军覆没**：`客服 数据集`、`客诉`、`投诉 处理`、`退货 换货`、`物流 查询`、`意图 分类 中文`、`槽位 抽取`、`电商 售后`、`服务 质检`、`京东 客服`、`JDDC` —— **17 个里只有「工单」和「满意度」有命中**。只有单字/双字宽泛词（`客服`、`对话`、`售后`、`工单`）能搜出东西。**不能靠搜索做穷尽式发现，只能靠头部数据集横向扩展。**
3. **ModelScope 数据集重复搬运极常见**：`cyy1dingsheng/customer_service_chat`、`xuri2004/dianshang_dataset`、`cyyyyyyyy1/dianshangduihua` 三个不同账号、不同名字、不同下载量的仓库，文件 **md5 全为 `00a532352a09`、都只有 100 行**。**必须比对 size/md5。**
4. **HF 镜像的中文关键词完全搜不到**：`客服对话 中文`、`中文 客服`、`电商 对话`、`售后` 以及 `chinese customer service`、`task oriented dialogue chinese`、`ECDT`、`e-commerce dialogue corpus`、`taobao customer service`、`complaint dataset chinese`、`after sales` 全部**返回 0 条**。**HF 镜像上中文客服语料基本不存在**，只能靠英文泛词（`customer service conversation`、`service ticket`）碰运气。
5. **hf-mirror 详情 API 对部分仓库返回 401**：`Johnson8187/Chinese_Multi_Emotion_Dialogue_Dataset`、`DAMO_ConvAI/ZhDoc2BotDialogue`（**后者在 ModelScope 上可以正常取到**）。
6. **`api.github.com` 限流极严**：连发 6 个搜索查询就开始 `HTTPError 403: rate limit exceeded`；后续 26 次重试全失败。**`search/repositories` 与 `contents` 共用一个额度。**
7. **jsdelivr 单文件上限约 20MB，超了直接 403**：`multi_train.txt`(80MB)、`single_train.txt`(38MB)、`extract_train.json`(53MB) 全部 `<HTTPError 403: 'Forbidden'>`，而 `question_answer.txt`(7.6MB)、`context_response.txt`(...) 正常。
8. **ghapi 大文件会 `IncompleteRead`，但 `e.partial` 里是已收到的数据，别丢**：`IncompleteRead(8332994 bytes read, 55614 more expected)` 在 8MB 分块下几乎每块必现。**只捕获异常后 `time.sleep` 重试 = 每块白丢 8MB**（本任务最初就是这样卡在 34.8% 反复重试）。正解见 `fetch2.py`：捕获 `IncompleteRead` → 取 `e.partial` 落盘 → 从新长度续。改用 partial-aware 后 `multi_train.txt` 剩余 34.8%（27.9MB→80.3MB）零浪费落地。
9. **`codeload.github.com/{repo}/tar.gz/refs/heads/{branch}` 是绕开 API 限流的正解**：不限流、不需要 token，且可用 `tarfile.open(fileobj=r, mode='r|gz')` 流式解包只取需要的成员（无需先落整仓 222.9MB）。但**整仓 tarball 的流式读取极慢**（JDDC 整仓跑了 40 分钟仍未出首个命中成员），如果只要单个大文件，**用 ghapi 分块 Range + `fetch2.py` 更快**。
10. **文件名拼错会被当成 404 而不是纠错**：`fast_rl_mod_truepreds.txt`（少一个下划线）返回 404，正确名是 `fast_rl_mod_true_preds.txt`。**看到 404 先怀疑文件名，别急着判定数据集没了。**
11. **GitHub 路径含空格不 encode 会报 `InvalidURL`**：`All Conversation.json` 直接拼进 URL 会 `InvalidURL("URL can't contain control characters...")`，必须 `urllib.parse.quote` 成 `All%20Conversation.json`。
12. **PowerShell `ConvertFrom-Json` 会因为键只有大小写差异而报错**（`包含具有不同大小写的键`）——本任务所有 JSON 解析都改用 Python。
13. **目录名带 `.`（`capzy85/customer-service-training-dataset2.0`）** 在 ModelScope API 路径里可以正常用，但要注意 URL 编码。

### E. 写了但不够用的
- `RyanMeg123/ShopBot-SFT-Dataset`（电商客服对话数据集 ★6）：`data/sft/train_v1.jsonl` 仅 **5,638 B**、rlhf 4,486 B —— 太小。
- `newhwa/dialog_test_tmall`（天猫对话测试集，dl=423）：`train.csv` 296,573 B / 14,520 行，但**去重后只有 337 条唯一 prompt**，且 `history` 列全为空、`response` 里混着「诛仙3幻世无双多少级能进」「招商银行可以申请几张信用卡」这类非客服内容，**是坏数据**。

---

## 附：本目录产出物索引（`ticket-agent/data/probe/`）

**样本 `samples/`**：`ms_ecom_train_FULL.jsonl`(104.7MB/226,600行)、`hf_zhtw_ticket_{train,val,test}_FULL.jsonl`(3,148条)、`ms_www2025plus_FULL.json`(17,442条)、`gh_csds_goldrefs_FULL.txt`+`gh_csds_truepreds_FULL.txt`+`gh_csds_usersummary_FULL.txt`(800+800+800)、`jddc_question_answer_FULL.txt`(100,000行)、**`jddc_multi_train_FULL.txt`(80,261,749 B / 500,000 行，已校验 UTF-8 完整、列数异常 0)**、`jddc_multi_train_head.txt`、`jddc_single_train_head.txt`、`jddc_context_response_head.txt`、`gh_ambiguous_conv_head.json`(1,104段)、`ms_customerjson_ecom200m_head.jsonl`、`ms_customerjson_ecom_examples.jsonl`、`ms_customerjson_labels.json`、`ms_decisiongongdan_test.jsonl`、`ms_qingshan_{aftersale,logistics,ecom}.jsonl`、`ms_tmall_train.csv`、`ms_zhdoc2bot_head.csv`、`hf_chinesedialogue_{douban,kdconv}_head.jsonl`、`hf_tobibueck_head.csv` 等
**全量多轮 JDDC `samples_targz/`**：`extract_train.json`(53,148,481 B / 64,321 条)、**`data__chat.txt`(20,851,245 B / 212,808 条消息 / 10,945 会话，会话级原始日志)**、**`search_dialog__data__context_response.txt`(16,037,538 B / 100,000 行)**、**`search_dialog__data__question_answer.txt`(7,600,478 B / 100,000 行)**
**脚本（可复用）**：`dl.py`(统一下载器 gh/hf/url，jsdelivr→ghapi 自动回退)、**`fetch2.py`（GitHub Contents API 分块续传下载器，partial-aware：捕获 `IncompleteRead` 取 `e.partial` 落盘续传，不丢已收字节）**、`resume.py`(Range 4MB 分块断点续传)、`targz.py`(**codeload tar.gz 整仓通道，绕开 API 限流**)、`ms.py`(ModelScope 搜索/详情/下载)、`hfdetail.py`、`ghtree.py`、`search.py`/`search2.py`/`mss.py`/`mss3.py`、`peek.py`、`fetch.py`、`verify.py`(批量 URL 可下载性校验)
**报告**：`REPORT.md`（本文件）
