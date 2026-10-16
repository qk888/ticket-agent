"""真实中文客服语料接入层。

★ 为什么要有这个模块（而不是继续用 `seed.py` 里那 9 条手写模板）：

手写模板一共 9 句（L1/L2/L3 各 3 句）。把造数扩到 `n_customers=2000`
时，评测集里的工单正文会**反复出现同样的 9 句话** —— 于是"准确率"
衡量的越来越像是"agent 有没有记住这 9 句"，而不是"它能不能读懂客户
在说什么"。这是本项目所有指标里最容易被质疑的一点，也是唯一一个
**靠改代码解决不了**的问题：语料的多样性只能来自真实语料。

★ 语料与正误的分工（这是本模块最重要的设计决定）：

这份语料提供的是**语言**（客户怎么说），不提供**正确答案**。
数据集的 `scene` 标签（"退换货" / "质量问题"）看着像标签，但它
**映射不到我们的动作集**：同样是 `退换货`，订单刚签收就该退（R1），
签收 8 天就该驳（R1 超期），生鲜类目根本不能退（R6），已退过一次
必须转人工（R5）—— 决定权在**订单数据**，不在客户怎么说。

所以分工是：**语言来自语料，正误来自 `rules.decide()`**。
如果反过来，拿语料的 scene 当 golden，就等于把别人一套任意的标注
体系引进自己的政策里，而且它和 R1–R7 冲突时无从裁决。

★ 这里的分层叫 `style`（话术风格），**不叫 L1/L2/L3**：

一开始我把语料按文本特征分成 L1/L2/L3，与 `build_eval_cases` 里的
业务难度分层**同名**。这是个陷阱：两者含义不同，恰好重名会让人
（包括我自己）以为它们是一回事。实测分布立刻暴露了问题 ——
含糊态占六成，而业务上 L3（要澄清/无从处置）只占 15%。
如果按语料分布去决定业务分布，评测集就会变成六成都是"该反问"
的工单，与真实业务形态相差四倍。

所以两个维度用两套名字：**`style` 说客户怎么讲话，`level` 说这条
case 要 agent 做多难的事**。二者的关系由 `level_to_styles()` 显式
写出来，而非靠同名默认。

★ 本模块已知的局限（写在这里，因为它会被问）：

1. 以**真实**语料为主（`jddc_aftersale.jsonl` 5,776 条，来自京东客服
   对话），另加一份合成语料（862 条）互补。真实那份带来了手写模板
   永远造不出来的东西：错别字（"啥时侯"、"崔一下"）、无标点长句、
   中途改口。代价是它也带来了**噪声** —— 门禁只能保证"这句话有一个
   售后事由且能独立成立"，不能保证它准确描述了订单实情。
2. 84% 的文本用泛称（"商品" / "东西"），不点具体品类。好处是不会
   出现"衣服"挂在一笔数码订单上的假冲突；代价是**语料的品类信息
   无法与订单类目交叉验证**，R2/R6 的类目规则只能靠订单侧驱动。
3. 语料只含客户单句，不含历史工单的处置结论 —— 所以 `golden` 仍然
   全部由规则推导（见 `_decide_golden`）。
4. 去重后 6,638 条（真实 5,776 + 合成 862）。造 2000 客户（约 1200
   工单）时同一句话平均出现 0.18 次 —— 比 9 条模板（平均 130 次）好
   近三个数量级，但依然不是
   "无限不重复"。真正要消除重复只能上真实工单日志。
5. 真实语料的**事由分布**与本系统的**动作集**不重合：物流类占 40.7%，
   而系统没有任何查件/催件的动作（见 `_REQUEST_RE` 上方的说明）。
   这不是缺陷，恰恰是评测集该有的样子 —— 真实工单里本来就有一半
   是系统办不到、只能澄清或转人工的事。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from ..config import PROJECT_ROOT
from .textnorm import normalize

CORPUS_DIR = PROJECT_ROOT / "data" / "raw"

# 语料来源与用途。sha256 是**溯源的一部分**：报表里的每个数字都来自
# 某个具体版本的语料，只写文件名不足以固定"当时读的是哪些字节"。
# 换了语料版本，指标就不再可比 —— 这跟缺陷 ⑨（基准漂移）是同一类问题，
# 只是漂移源从时钟换成了数据文件。
CORPUS_SOURCES: dict[str, dict[str, str]] = {
    "jddc_aftersale.jsonl": {
        "repo": "JDDC / 京东客服对话（同一批数据的**两种切法**的合并产物："
                "上游切好的单轮对 `ms_ecom_train_FULL.jsonl` + 原始上下文版 "
                "`jddc_multi_train_FULL.txt`）",
        "platform": "HuggingFace 镜像 · 本地构建",
        "bytes": "1642.4 KB",
        "rows": "10,217（单轮源 226,600 行 + 多轮源 500,000 行 → 合并去重 "
                "139,432 条 → 经九道闸门筛出；其中 8,521 条带客服回复）",
        "fields": "scene / user / cause_all / bot",
        "role": "工单正文语料（本模块的主输入）",
        "sha256": "f9100d09cbfbda1472180f50a50281d8",
        "license_note": "原始数据集许可以其发布页为准；本文件是派生产物，"
                        "可复核：`python tools/build_corpus_jddc.py`",
    },
    "ecom_aftersale.jsonl": {
        "repo": "QingshanAI/ecom-after-sale-synthetic",
        "platform": "ModelScope",
        "bytes": "258.7 KB",
        "rows": "997（去重后 862）",
        "fields": "scene / user / bot / tone",
        "role": "工单正文语料（补充：真实语料缺的『含糊态』主要靠它）",
        "license_note": "数据集页面的许可条款以其仓库为准，此处仅作来源标注",
    },
    "www2025_intent.json": {
        "repo": "smau0441/www2025-train",
        "platform": "ModelScope",
        "bytes": "1278.3 KB",
        "rows": "1000",
        "fields": "id / instruction / input / output(意图标签) / image",
        "role": "意图层评测（其话术是产品咨询，不是售后诉求，故不进工单正文）",
        "license_note": "同上",
    },
}

# 本模块消费的语料文件。**多份**：真实语料（jddc）为主，合成语料（ecom）补充
# 真实语料里稀缺的"含糊态"话术 —— 真实客服对话里客户多半是说清了事由才来找的，
# 而政策上需要"反问澄清"的那类工单（L3）恰恰要求客户没说清。
PRIMARY_CORPUS = "jddc_aftersale.jsonl"
CORPUS_FILES: tuple[str, ...] = ("jddc_aftersale.jsonl", "ecom_aftersale.jsonl")

# ── 话术风格（style）──────────────────────────────────────────
# ★ 注意 `vague` 的准确含义：**客户要的动作不在系统能力内**，
#   不是"客户没说清"。真实语料里 40.7% 是物流类（"帮我催下快递"），
#   客户说得非常清楚 —— 只是系统没有查件/催件的动作。详见 `_REQUEST_RE`。
STYLE_VAGUE = "vague"        # 诉求不在动作集内（要查件/补发/开票/改址，或只是发泄不满）
STYLE_DEFECT = "defect"      # 诉求在动作集内 + 提到质量瑕疵（政策上要走质量投诉、要凭证）
STYLE_EXPLICIT = "explicit"  # 诉求在动作集内，无质量描述

ALL_STYLES = (STYLE_EXPLICIT, STYLE_DEFECT, STYLE_VAGUE)

# 业务难度层 → **允许**的话术风格集合。
#
# ★ 这个映射是**显式**的，就是为了让"为什么 L3 工单配含糊话术"有一个
#   可被审阅的落点，而不是散在造数循环里的 if。
#
# ★ 它是**集合**，不是优先级排序 —— 集合内各风格按**语料里的自然条数**
#   分配权重（见 `pick`）。这一条是实测改出来的：上一版把它当成**有序回退**
#   （"第一个非空池通吃"），而 L2 的首选是只有 172 条的 defect、L1+L2 又占
#   样本 82% —— 于是那 172 条瑕疵话术被反复抽中，报表上 defect 占 43.0%
#   而语料里只有 2.6%（**放大 18.7 倍**），vague 则从 62% 被压到 17.2%。
#   有序回退还让 L1/L2 的实际选择完全由"哪个池子排前面"决定，等于给一个
#   与订单无关的维度人为加了权。
#
# 约束的由来只有一条：**正文要与正解相称**。
#   L1 规则直判 / L2 要算时效与物流 → 客户必须**点名了动作集内可执行的诉求**
#      （explicit 或 defect）。否则会出现正文说"帮我催下快递"、正解却是 R1
#      退款这类案子 —— 正文与答案打架，case 不公平。
#   L3 要澄清/无从处置 → 客户要的**不在动作集内**（vague），正解本就该是
#      "保持 open、反问澄清"或"转人工"，两者语义一致。
#
# ★ L3 与 vague 的语义对齐（已核对 `seed.py`）：L3 的 `order_basis` 只有
#   两种来源 —— `ambiguous`（候选订单多于一个 → 保持 open、反问澄清、
#   `intent=need_clarification`）与 `no_order`（客户一笔订单都没有 → 转人工）。
#   两者共同点正是"系统当前办不了" —— 与 vague 的含义（诉求不在动作集内）
#   在同一语义区间。这不是巧合：`decide()` 从不返回 `need_clarification`，
#   它由造数侧独立指定，而指定条件就是"订单侧无从处置"。
#
# ★ L1 与 L2 取值**故意完全相同**：风格说的是"客户怎么讲话"，而 L1/L2 的
#   区别在订单状态（规则直判 vs 要算时效与物流），与客户怎么讲话无关。
#   给它们配不同的风格集合，等于把一个订单维度的区分伪装成语言维度的区分 ——
#   上一版正是这么错的。
#
# ★ 已知代价（诚实记录）：真实语料里在途订单的客户十有八九说的是
#   "什么时候能到 / 帮我催下"（判为 vague），而 L2 现在不收 vague。
#   这让 L2 的话术比真实情况"整齐"一些。取舍是**公平优先**：那些话术
#   配给 L2 会让正解（R3 拦截，即取消订单）与客户所问（催一催）相反。
_LEVEL_TO_STYLES: dict[str, tuple[str, ...]] = {
    "L1": (STYLE_EXPLICIT, STYLE_DEFECT),
    "L2": (STYLE_EXPLICIT, STYLE_DEFECT),
    "L3": (STYLE_VAGUE,),
}


def level_to_styles(level: str) -> tuple[str, ...]:
    """业务层 → **允许**的话术风格集合。未知层退回全部风格。"""
    return _LEVEL_TO_STYLES.get(level, ALL_STYLES)


# ── 场景归一化 ────────────────────────────────────────────────
# 原始 scene 有 14 个取值，多数是同一件事的不同写法
# （"质量" / "质量投诉" / "质量反馈" / "质量问题"）。
# ★ 后半段（物流/补发/赔付/发票/改址）来自真实语料 jddc —— 它们是真实售后里
#   的常见事由，而原有合成语料的 14 个取值里根本没有这几类。这不影响判定：
#   scene 只用于生成工单标题，正误由 `rules.decide()` 按订单数据推出。
_SCENE_CANON: dict[str, str] = {
    "退换货": "退换货",
    "退货": "退换货",
    "换货": "退换货",
    "质量问题": "质量问题",
    "质量": "质量问题",
    "质量投诉": "质量问题",
    "质量反馈": "质量问题",
    "维权投诉": "维权投诉",
    "差评": "差评",
    "差评处理": "差评",
    "补偿方案": "补偿方案",
    "补偿": "补偿方案",
    "退款": "退款",
    # 多标签取值（"退换货,质量问题"）只取其首个标签，不发明第三种类别
    "退换货,质量问题": "退换货",
    # 真实语料（jddc）的事由键
    "物流": "物流异常",
    "补发": "补发申请",
    "赔付": "补偿方案",
    "发票": "发票开具",
    "改址": "改收货地址",
}

# scene → 工单标题。标题要像工单系统里的一行摘要，而不是分类名。
_SUBJECT: dict[str, str] = {
    "退换货": "退换货申请",
    "质量问题": "商品质量问题",
    "维权投诉": "客户投诉维权",
    "差评": "差评风险",
    "补偿方案": "补偿诉求",
    "退款": "退款申请",
    "物流异常": "物流异常",
    "补发申请": "补发申请",
    "发票开具": "发票开具",
    "改收货地址": "改收货地址",
}

# ── 风格判定规则（可单测，不依赖人工标注）──────────────────────
#
# ★ 判据的基准是**系统的动作集**，不是"这句话像不像投诉"。
#
# `rules.decide()` 只产出四种动作（`refund` / `reject` / `block_logistics` /
# `escalate`），客户侧能被满足的诉求因此只有三类：
#   ① 要钱          → refund
#   ② 要退货/换货   → refund（规则侧不区分退与换，见下）
#   ③ 要拦截/拒收   → block_logistics（R3）
# 系统**没有**"查件 / 催件 / 补发 / 开票 / 改址"任何动作。
#
# 所以：
#   explicit = 客户点名了**动作集内可执行**的诉求
#   vague    = 其余（要么只说不满，要么要的是系统办不到的事）
#
# ★ 这里踩过一次坑，记下来：最初 `_REQUEST_RE` 只认"退/换"家族，于是
#   「帮我催下快递」被判 vague。直觉上"这句话说得挺清楚啊"，但它的清楚
#   是对人的清楚，不是对本系统的清楚 —— 判它 explicit 会让工单正文点着
#   一个办不到的动作，而 golden 由订单数据推出（可能是 R1 退款），
#   正文与答案直接打架。实测真实语料里这类"办不到"的占 51.5%（物流类），
#   若把它们都算 explicit，等于给自己造一半语义错位的评测样本。
#
# ★★ 上面那条判据在**动作集扩充后会自动翻转** —— 这正是设计时的预期
#   （见 `classify_style` docstring）。原先系统只有 5 个动作，物流类
#   （39.3%）、补发类（5.6%）、赔付、发票、改址合计约 56% 的真实诉求
#   都落进 vague；现在 `rules.REQUEST_KINDS` 有了九类诉求、系统也真的
#   能办（`submit_after_sales_action`），这些句子就应当合法地变成 explicit。
#   所以下面的词表跟着扩了 —— 报表上的风格分布会变，这是**能力变强的
#   正确投影**，不是标注漂移。旧读数与新读数之间不可直接比较风格维度。
#
# ★ 换货为何并入退货：grep 全项目，规则侧只按订单状态/类目/天数判，
#   **没有任何区分"退"还是"换"的逻辑**（`rules.py` 全文无换货分支）。
#   在当前系统里换货就是走退货流程，故同属"动作集内可执行"。
#   （九类诉求引入后换货已有独立规则 R8，此行保留是因为它记录的
#   是"词表当初为什么这么写"的历史事实。）
_REQUEST_RE = re.compile(
    r"退款|退货|退钱|退换|换货|换新|换一个|换个|能退|要退|想退|申请退|帮我退|退一下|"
    r"拒收|不要了|不想要|退掉|拦截|"
    # ★ `寄回`/`退回` 在中文里既表示"退回给商家"，也表示"寄往某地"。
    #   实测误判原文：「那我买8包寄回老家邮政是吗」—— 那是**收件地址**是老家。
    #   故排掉后面直接跟目的地的情形。
    r"(?<!寄)退回|寄回(?!老家|家|家里|地址|公司|单位|学校|老家)|"
    # ── 以下为动作集扩充后新增（见 `rules.REQUEST_KINDS` 的九类诉求）──
    # ★ 这次扩充不是"顺手多加几个词"，而是 `classify_style` docstring 里
    #   预告过的那次改判：该函数的判据是「客户要的动作**在不在动作集内**」，
    #   而系统原先只有 5 个动作（退款/驳回/拦物流/转人工/反问），
    #   于是真实语料里占 39.3% 的物流类、5.6% 的补发类全被判成 `vague` ——
    #   不是客户说得不清楚，是**系统办不到**。现在系统能办了，
    #   它们就该合法地变成 `explicit`。报表风格分布因此会变，
    #   这是"能力变强"在数据上的正确投影，不是指标漂移。
    r"补发|重发|漏发|少发|没发|发错|错发|发的不一样|不是我买|拍的是|"
    r"赔付|补偿|赔偿|赔我|"
    r"价保|退差|保价|降价|"
    r"发票|开票|开个票|要发票|"
    r"改地址|改收货|改下地址|换个地址|地址写错|"
    r"催|加急|什么时候发|什么时候到|多久能到|到哪了|物流|快递|"
    r"运费|邮费|"
    # ★ 换货家族的量词要穷举 —— 与 `_EXCHANGE_RE` 保持同一套判据。
    #   初版只写到 `换一个|换个`，漏了「我要换一件别的尺码」这类，
    #   于是它被判成 vague，**而它要的 exchange 明明已经在动作集里**。
    #   两处词表不一致会让 style 与 request_kind 互相打架（一个说
    #   "系统办不到"、另一个说"就走 exchange 线"），故一并补齐。
    r"换一[件条双台款式种]|换件|换同款|换别的|换其他|想换|要换|能换|可以换|再换"
)
_DEFECT_RE = re.compile(
    r"瑕疵|破损|破洞|破了|划痕|开胶|开线|线头|次品|假货|变质|损坏|脏|裂|掉色|褪色|质量"
)

# ── 文本的事实前提：客户是在**收到货之后**说的吗 ────────────────
#
# ★ 这与 `style` 是两个维度：`style` 说"诉求在不在动作集内"，
#   这里说"客户的话预设了什么事实"。「收到的衣服有瑕疵」预设货已到手，
#   「什么时候能收到」预设货还在路上 —— **订单状态必须与之相容**，
#   否则样本自相矛盾。
#
# ★ 为什么需要它（实测数字）：造数时正文按 `level` 选、订单另选，
#   两者互不知情。605 条 case 里 **84 条（13.9%）** 出现
#   「正文说已到手，订单却是未付款/已取消/未签收」——
#   例如 `[L2 pending_paid] 收到的衣服有瑕疵，能帮我处理换货吗？`。
#   一笔没付款的订单不可能已经收到货，读到这条失败样本的人只会问
#   "这是造数造错了吧"，而不会去看 agent 哪里错了。
#
# ★ 判据**刻意保守**，只认几乎无歧义的表达 —— 这是被前几轮教训
#   换来的：词典一旦放宽，误召回比真召回多。两条具体取舍：
#   ① `签收` 不收：「显示已签收但我没收到」是**未到手**，收它会把意思判反。
#   ② 先查"未到手"再查"已到手"：前者更具体，能先接住上面那类句子。
_PRE_RECEIPT_RE = re.compile(
    r"还没收到|没有收到|没收到|未收到|什么时候能收到|什么时候到|多久能到|"
    r"什么时候发货|还没发货|没发货|发货了吗|显示已签收|已签收但|"
    r"在路上|在途|派送|揽件|催|加急|"
    # ★ 下面这批是补漏时按实测原文加的（见 `_POST_RECEIPT_RE` 的说明）：
    #   放宽了"已到手"的判据，就必须同步放宽"未到手"的那一侧，否则
    #   「多久能收到」这类问句会因为句中有"收到"而被误判成货已到手。
    r"多久能收到|几天能收到|什么时候收到|能收到吗|能否收到|收不到|"
    r"没到货|未到货|还没到货|到货了吗|还没到|没有到|到了吗|"
    # ★ 「拒收 / 不签收」是**语义上**的"货没到手"，不是词形巧合：
    #   拒收的定义就是拒绝签收，那笔货从未进入客户手里。
    #   实测原文：`快递到了我不想要了可以拒收吗`（配 shipped 订单走 R3 拦截）。
    #   这句里同时有"到了"和"拒收"，语义上"拒收"更具体、更晚发生 ——
    #   客户是在说"我要拒绝这笔货"，不是在说"我拿到了"。
    #   这条取舍由测试钉住（`test_presupposes_receipt_history_and_transit`）：
    #   上一版把裸的"到了"收进 `_POST_RECEIPT_RE`，这句就被判成已到手，
    #   让 R3（拦截）的样本没法配在途订单 —— 一个词毁掉一条规则的可测性。
    r"拒收|不签收|没签收|未签收"
)
_POST_RECEIPT_RE = re.compile(
    r"收到的|我收到|收到货|收到商品|收到了|拆开|打开|拆了|用了|吃了|喝了|"
    r"试了|穿上|发来|寄来|送来|漏发|少发|发错|到手|"
    r"碎了|坏了|破损|变质|压坏|压扁|掉色|褪色|开线|开胶|划痕|破洞|"
    # ★ 下面这批是 `_compatprobe.py` 的宽尺子把残留样本筛出来之后**逐条读原文**
    #   加的。加之前生产判据报 0 违例，但宽尺子指出 4 条真冲突是漏网：
    #     `收到有质量问题给退换吗`        ← 只有裸的"收到"能命中
    #     `里面每个都有点状线我担心不卫生了` ← "里面"没进词典
    #     `要不你们自己负担邮费给你们寄回来重发过来` ← "寄回来"≠"寄来"
    #     `怎么包装退回去变成碎片我不管`    ← "碎片"≠"碎了"
    #   这四句配的都是 `pending_paid`（未付款）/`shipped`（在途）订单，
    #   都是真矛盾 —— **两把尺子的差额本身就是线索**：生产判据说 0，
    #   宽尺子说有，差额就该被逐条读掉，而不是挑一把信。
    #   ★ 这里**故意不收裸的 `到了`**：它在中文里既表示"货已抵达"
    #   （「快递到了我不想要了可以拒收吗」），也表示"款项到了/时间到了"，
    #   假阳性太高。实测那一句正好是 R3 拦截的样本，收了它会让拦截类
    #   工单配不上在途订单 —— 宁可漏判，不可把意思判反。
    r"收到|里面|到货|碎片|寄回来|退回去|寄回去|压碎"
)


def presupposes_receipt(text: str) -> bool:
    """客户的话是否**假定货已到手**。

    True 表示"必须先签收才说得通"，用于过滤"正文与订单状态打架"的样本。
    `_PRE_RECEIPT_RE` 优先 —— 它更具体，且「显示已签收但我没收到」
    这类句子会被它先接住，不会被句中的"签收"误导。

    返回 False 的含义是"**没有**假定已到手"（可能是在途，也可能没提），
    不是"确定了货没到" —— 后者我判不了，也不该假装判得了。

    ★ 入口先过 `textnorm.normalize`：判据词典是简体字面量，遇上繁体字形会
    **把结论判反**（`還沒收到` 里的 `收到` 会命中 `_POST_RECEIPT_RE`，
    于是一句"货没到手"被判成"已到手"）。实测同一批繁体句里
    `presupposes_receipt` 判反 159 条（6.4%）。
    """
    text = normalize(text)
    if _PRE_RECEIPT_RE.search(text):
        return False
    return bool(_POST_RECEIPT_RE.search(text))


@dataclass(frozen=True)
class CorpusTicket:
    """一条语料工单。"""

    subject: str
    body: str
    scene: str
    tone: str
    style: str

    @property
    def explicit(self) -> bool:
        """客户是否点名了系统能执行的动作（= style 不是 vague）。"""
        return self.style != STYLE_VAGUE

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "body": self.body,
            "scene": self.scene,
            "tone": self.tone,
            "style": self.style,
        }


def has_explicit_request(text: str) -> bool:
    """客户是否点名了**动作集内可执行**的诉求（退款/退货/拦截拒收）。

    ★ 不是"有没有提要求" —— 「帮我催下快递」提了要求，但系统没有催件动作。

    ★ 入口先过 `textnorm.normalize`，理由同 `presupposes_receipt`：
    `我要退貨` 里的 `退貨` 若不归一，`_REQUEST_RE` 一个词条都匹配不上，
    这句会被判成 vague（客户明明点名了退款）。
    """
    return bool(_REQUEST_RE.search(normalize(text)))


def classify_style(text: str) -> str:
    """按文本判定话术风格。规则见上方注释。

    ★ 这个函数存在的意义是让"分类"成为一个**可复现的判定**：
    同一个句子任何时候都得到同一个 style，且任何一次分类变化都能
    在 diff 里看见。手工贴标签做不到这两点 —— 而且一旦标签是手工的，
    它就不再是数据集的属性，而成了我的个人解读。

    ★ 判据是**相对系统能力**的，不是相对"这句话读起来清不清楚"：
    `vague` 的含义是"客户要的动作不在动作集内"，所以同一个句子在
    动作集扩充后（比如将来加了补发工具）会合法地改判 —— 那种改动
    会体现在这个函数的 diff 里，这正是要的效果。

    ★ 归一化在这里做**一次**（`has_explicit_request` 与 `_DEFECT_RE`
    共用同一个归一化结果），避免同一句话被转两遍、也避免将来有人
    只给其中一条加了归一化而导致两条判据看到不同字形的文本。
    """
    text = normalize(text)
    if not _REQUEST_RE.search(text):
        return STYLE_VAGUE
    if _DEFECT_RE.search(text):
        return STYLE_DEFECT
    return STYLE_EXPLICIT


#: 语料 `scene` → 客户诉求（`rules.REQUEST_KINDS` 九类之一）。
#:
#: ★★ 这张表要解决本模块开头那条"scene 映射不到我们的动作集"的说法 ——
#:   两者说的其实是**不同维度**，别混：
#:
#:     scene  →  诉求（客户要什么）  ← 这张表能映射
#:     诉求   →  动作（我们做什么）  ← 由 rules.decide() 按订单数据决定
#:
#:   scene 确实映射不到**动作**（同样一句"退换货"：刚签收该退、签收 8 天该驳、
#:   生鲜根本不能退、退过一次要转人工），但它能映射到**诉求** ——
#:   "退换货"就是"客户在要求退货退款或换货"，这件事与订单状态无关。
#:   把两者分开之后，本模块开头那条警告仍然成立：**正误由订单数据推出**，
#:   scene 只告诉我们"客户想干什么"，不告诉我们"该不该给"。
#:
#: ★ 为什么分类质量不必完美（这一点很重要）：
#:   `request_kind` 最终写进 `Ticket.request_kind` 一列，**golden 与 agent
#:   读的是同一个字段**。所以分类哪怕"分错了"，只要它可复现，
#:   golden 和 agent 看到的就仍然是同一个前提，case 依然可赢、可判。
#:   这与 `classify_style` 只用于报表分桶（不参与判定）是不同的用途，
#:   但同样受益于"纯函数、可复现"这条性质。
#:
#: ★ `None` 表示**不映射**（保留默认 `refund`）。语料里 scene 缺失、
#:   或落在"维权投诉/差评/补偿方案/其他"这些**表达情绪而非诉求**的类目上时，
#:   我们无从知道客户要什么 —— 这时退回默认值，让这条工单走退款线，
#:   而不是硬猜一个诉求去改变它的 golden。**猜错比不知道更糟**：
#:   不知道时行为与改动前完全一致（可回归），猜错则会让旧 case 的
#:   golden 悄悄变样。
_SCENE_TO_REQUEST: dict[str, str | None] = {
    # 退换货这一大类要细分：语料把"退货""换货"都归到"退换货"，但两者诉求不同
    # （是否寄回商品、是否要回钱）。细分判据见 `_classify_request`（读正文）。
    "退换货": None,          # 由正文细分 refund / return_refund / exchange
    "退款": "refund",
    # ★★ 缺陷 ㉗：这一行原先写的是 `"质量问题": "refund"`，是个**建模错误**。
    #
    #   "质量问题"是**原因**（cause），不是**诉求**（request kind）。
    #   客户说"有质量问题"时可能想退款、想换货、想要赔偿、想要价保 ——
    #   而这一行把 792 条全部直接钉成 `refund`，
    #   `classify_request` 的正文分支**再也不会被调用**。
    #
    #   实测（`_scenemap.py`）：只看正文能认出 **223 条 price_protect（2.0%）**，
    #   带上 scene 只剩 **2 条** —— 差的 221 条全被这一行吃掉了。
    #   于是 `R11:price_protect` 这条条件永远是 0 条 case，
    #   而规则 R11 写得好好的、`_gapdiag.py` 手工构造也能判对。
    #
    #   这与整个重构要修的那类错误**完全同形**：
    #   把"客户要什么"和"我们归档成什么"压成同一个字段。
    #   和"退换货"同理 → 改成 None，交给正文细分。
    "质量问题": None,
    "物流异常": "urge",
    "补发申请": "reship",
    "补偿方案": "compensate",
    "发票开具": "invoice",
    "改收货地址": "address_change",
    # 表达情绪/评价的，不含可执行诉求 —— 交给正文判，判不出就用默认
    "维权投诉": None,
    "差评": None,
}

#: 语料 scene **词表里没有对应类目**的诉求 —— 只能靠正文认。
#:
#: ★★ 缺陷 ㉘：这是一条关于**语料本身**的事实，不是关于分类器的。
#:   见 `_SCENE_CANON`：标注方案按"事由"分了退换货/质量问题/维权投诉/
#:   差评/补偿方案/退款/物流异常/补发申请/发票开具/改收货地址 十类，
#:   **其中没有任何一类是"价保"**（客户要求退还降价差额）。
#:
#:   后果：客户说"退差价"时，标注者只能按**事由**归档 ——
#:   实测 244 条含价保关键词的语料，scene 分布是：
#:       补偿方案 171 / 物流异常 27 / 退款 26 / 退换货 14 / 补发申请 5 / 其他 1
#:   而"补偿方案"被直接映射成 `compensate` → 170 条价保诉求
#:   全部被判成"索赔"。**只要 scene 优先，价保就永远不可能被表达出来**：
#:   实测 scene 优先得 2 条 price_protect，只看正文得 223 条。
#:
#:   ★ 这不是"文字正则比人工标注更准"，而是**两个维度在打架**：
#:     · scene 标注的是"这通会话在谈什么事"（事由）
#:     · `request_kind` 要的是"客户要我们做什么"（诉求）
#:     谈补偿的会话里，客户要的可能是赔钱（compensate）、
#:     也可能是退差价（price_protect）—— 后者语料侧无从标注。
#:
#:   ★ 所以修法是**窄的**：只让正文补出 scene 词表**表达不了**的那几个诉求，
#:     在 scene 映射**之前**判。scene 能表达的一律仍以 scene 为准 ——
#:     那 7892 条标注是有价值的人工信号，不该被我的正则推翻。
#:
#:   ★ 加新诉求进来时，先问一句：**语料的 scene 词表里有它吗？**
#:     没有 → 加进这个集合；有 → 走 `_SCENE_TO_REQUEST`。
#:     判断错了不会崩，只会让这个诉求在评测里恒为 0 条 case ——
#:     而"0 条 case"和"规则没写"在报表上长得一模一样。
_BODY_ONLY_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("price_protect", re.compile(r"价保|保价|退差|差价|降价|便宜了")),
)


def classify_request(text: str, scene: str = "") -> str:
    """判定**客户要什么**（`rules.REQUEST_KINDS` 之一）。

    ★ 优先用语料的 `scene`（那是数据集自带的真实标注），scene 不足以区分时
    再看正文。两段的分工：

      1. `scene` 能直接给出诉求的（补发/赔付/发票/改址/物流）→ 直接用。
         这些类是语料标注者按**事由**分的，比正则可靠。
      2. `scene` 说"退换货"时**必须细分** —— 客户要的是"把钱退我"
         （`refund`）、"寄回去再退钱"（`return_refund`）、还是"换一件"
         （`exchange`）？三者在系统里的动作不同（是否生成逆向物流、
         是否走 R8 换货窗口），不能一律当退款。
         细分判据就是正文里有没有"换"字/有没有"寄回"意思，与
         `_REQUEST_RE` 用的是同一套归一化，避免字形问题。
      3. 其余（scene 缺失 / 情绪类）→ 再看正文能不能识别，
         识别不出**返回默认 `refund`**，与改动前的行为一致（见上表说明）。

    ★ 这个函数是**纯函数**：同 (text, scene) 永远同结果。与 `classify_style`
      一样，改动的效果会体现在 diff 里，而不是靠人记得某条标签是怎么来的。
    """
    t = normalize(text)
    mapped = _SCENE_TO_REQUEST.get(scene, None) if scene else None

    # ── ★★ 缺陷 ㉘：先让正文补出 **scene 词表表达不了** 的诉求 ──────
    #
    # 必须在 `mapped` 之前判，否则永远轮不到：含价保关键词的 244 条语料里
    # 有 171 条 scene 是"补偿方案"→`compensate`，mapped 一返回就结束了。
    #
    # ★ 但也必须**排在退换货家族之后**（下面两个正则）。
    #   「我要退货，但是降价了很生气」里的"降价"是**理由**不是诉求，
    #   客户要的是退货 —— 正文里的退换货信号优先级高于其他所有正文信号。
    #   这条规则在 `_REQUEST_HINT_PATTERNS` 那里也是一样的处理，不能只在
    #   一处生效，否则同一个句子的判定会随走哪条路径而变。
    if not (_EXCHANGE_RE.search(t) or _RETURN_RE.search(t)):
        for kind, pat in _BODY_ONLY_KINDS:
            if pat.search(t):
                return kind

    # scene 直接给出明确诉求（非退换货类）→ 采用
    #
    # ★ `None` 有**两种**来源，含义相同：scene 是"表达情绪/原因"的类目
    #   （维权投诉 / 差评 / 退换货 / 质量问题），或 scene 根本不认识。
    #   两者都表示"scene 不足以确定客户要什么" → 交给正文。
    if mapped is not None:
        return mapped

    # ── scene 是"退换货"或缺失 → 看正文 ──────────────────────
    #
    # ★ 顺序有讲究：**先判退换货家族，再判其余诉求**。
    #   反过来会让「我要退货，但是降价了很生气」被判成 price_protect ——
    #   客户说的是"退"，降价只是情绪/理由。
    if _EXCHANGE_RE.search(t):
        return "exchange"
    if _RETURN_RE.search(t):
        return "return_refund"

    # ★ 缺陷 ㉔：非退换货的诉求必须也能从正文认出来。
    #   原先这一段不存在，于是"价保/赔付/开票/改址/补发/催单"六类
    #   只能靠语料 scene 给；正文里写得很清楚也认不出来。
    for kind, pat in _REQUEST_HINT_PATTERNS:
        if pat.search(t):
            return kind

    # ★ 认不出诉求 → 返回默认 `refund`（**不是空串**）。
    #   空串会让下游 `expected_action_for("")` 抛 ValueError，
    #   也就是"分类失败"会升级成"造数崩溃"。而这里想要的语义只是
    #   "不知道客户要什么，按老行为走退款线" —— 默认值恰好表达它，
    #   且保证与改动前的 golden 完全一致（可回归）。
    return "refund"


#: 客户在正文里报出的金额（赔付/价保要它）。
#:
#: ★ 只认带"元/块/￥"等**明确币种标记**的数字。
#:   宁可取不到返回 0（→ R10 正确地要求先澄清金额），也不猜 ——
#:   猜错的代价是**静默地多赔一笔钱**，而这类错误在报表上看不出来。
#:   实测排除：「我买的时候 199，现在 149」里的 199/149 是价格对比，
#:   不是索赔金额；它们没有"元"字，所以不会被误取。
_AMOUNT_RE = re.compile(r"(?:¥|￥)?\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块钱|块|人民币)")


def extract_amount(text: str) -> float:
    """从正文里取客户报出的第一个金额；取不到返回 `0.0`。

    与 `rules.decide` 的 `request_amount` 参数配对使用：
    这里负责"从真实文本里读出数字"，那里负责"这个数字允不允许给"。
    """
    m = _AMOUNT_RE.search(normalize(text))
    if not m:
        return 0.0
    try:
        return round(float(m.group(1)), 2)
    except ValueError:  # pragma: no cover - 正则已保证可转
        return 0.0


def classify_cause(text: str, request_kind: str = "") -> str:
    """判定**诉求原因**（`rules.REQUEST_CAUSES` 之一）。

    少数规则要区分"为什么要换/补"：同是补发，`missing`（少发漏发）与
    `lost`（运输丢件）的处置不同；同是退货退款，`no_reason`（七天无理由）
    与 `quality`（质量问题）的运费承担方不同（见 policies R13）。

    ★ 判不出返回 `""`（"未指明"），不做猜测 —— 与 `classify_request`
      同一原则：不知道时保持默认，不改变旧行为。
    """
    t = normalize(text)
    for cause, pat in _CAUSE_PATTERNS:
        if pat.search(t):
            return cause
    return ""


#: 诉求原因的判据。**顺序即优先级** —— 越具体的越靠前
#: （"收到了破损的"既命中 damaged 也命中 quality，但破损更具体）。
_CAUSE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("wrong_item", re.compile(r"发错|错发|发的不对|不是我买|拍的是|买的.{0,4}发的")),
    ("missing", re.compile(r"少发|漏发|少了一|少件|缺件|没收到.{0,6}(赠品|配件|件)")),
    ("damaged", re.compile(r"破损|破了|压坏|碎了|损坏|变形|漏水")),
    ("lost", re.compile(r"丢件|丢了|丢失|快递不见")),
    ("quality", _DEFECT_RE),
    ("size", re.compile(r"尺码|大小不合|穿不上|不合身|偏大|偏小")),
    ("price_drop", re.compile(r"降价|便宜了|价保|退差|保价")),
    ("shipping_fee", re.compile(r"运费|邮费|快递费")),
    ("late", re.compile(r"太慢|超时|多久了|还没到|一直不|催|加急")),
    ("no_reason", re.compile(r"不想要|不喜欢|买错|多买|不需要")),
)

#: 换货判据：客户明确说"换"。用于把"退换货"细分成 exchange。
#: ★ `换一件` 是实测补上的：初版只写到 `换一个|换个`，而语料里
#:   「我要换一件别的尺码」用的是"一件"，被漏判成 refund。
#:   量词要穷举（个/件/条/双/台/款/种），因为中文里它随商品类别变。
_EXCHANGE_RE = re.compile(
    r"换货|换新|换一个|换个|换件|换同款|换别的|换其他|再换|"
    r"换一[件条双台款式种]|换一[下批]|想换|要换|能换|可以换"
)

#: 退货退款判据：客户要**把钱拿回来**且涉及寄回。用于细分 return_refund。
_RETURN_RE = re.compile(r"退货|退回|寄回|退回去|退掉|退了吧|申请退")

#: ★ 正文里的**非退换货诉求**判据（缺陷 ㉔）。
#:
#: 存在的理由：`classify_request` 原先只查 `_EXCHANGE_RE` / `_RETURN_RE`
#: 两个正则，于是**其余七类诉求只能靠语料的 `scene` 标签给**。
#: 而 `_REQUEST_RE` 里明明写着 `价保|退差|保价|降价` —— 它却只被
#: `classify_style` 用，从没参与判 kind。
#: 后果实测：`刚买就降价了，能价保吗` → `kind=refund`。
#: 于是 R11 的 `price_protect` 分支**永远不可达**，`R11:price_protect`
#: 这条评测条件永远是 0 条 case —— 而"0 条 case 的规则"和"没写的规则"
#: 在报表上完全同形。这是缺陷 ⑧ 的形状：**能力只存在于声明里**。
#:
#: 顺序即优先级（越具体的越靠前）。`refund`/`return_refund`/`exchange`
#: 不在此表内 —— 它们由上面两个正则和 `_REQUEST_RE` 家族处理。
_REQUEST_HINT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 价保：客户说的是"现在降价了，退我差价"，不是"我要退货"
    ("price_protect", re.compile(r"价保|保价|退差|差价|降价|便宜了")),
    # 赔付：要的是补偿，不是退商品款
    ("compensate", re.compile(r"赔付|赔偿|补偿|赔我|赔一下")),
    # 开票
    ("invoice", re.compile(r"发票|开票|开个票|要票|税票")),
    # 改址
    ("address_change", re.compile(r"改地址|改下地址|改收货|换个地址|地址写错|换地址")),
    # 补发
    ("reship", re.compile(r"补发|重发|漏发|少发|没发|少了一|少件|缺件|丢件|丢了")),
    # 催单：物流类在真实语料里占 39.3%，是最大的一类
    ("urge", re.compile(r"催|加急|什么时候发|什么时候到|多久能到|到哪了|物流|快递|发货")),
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


@lru_cache(maxsize=1)
def load_corpus() -> tuple[CorpusTicket, ...]:
    """读入并归一化**全部**语料（`CORPUS_FILES`）。全部缺失时返回空元组。

    去重按**正文**（不是整行）：同一句话在语料里重复会让它在评测集里
    占比超过应有的权重。跨文件也去重 —— 两份语料若有重叠句子，只留先读到的。

    坏行跳过（`json.JSONDecodeError`），不让一行脏数据废掉整份语料。
    """
    seen: set[str] = set()
    out: list[CorpusTicket] = []
    for name in CORPUS_FILES:
        path = CORPUS_DIR / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            body = str(row.get("user") or "").strip()
            if not body or body in seen:
                continue
            seen.add(body)
            scene = _SCENE_CANON.get(str(row.get("scene") or "").strip(), "其他")
            out.append(
                CorpusTicket(
                    subject=_SUBJECT.get(scene, "售后咨询"),
                    body=body,
                    scene=scene,
                    tone=str(row.get("tone") or "").strip(),
                    style=classify_style(body),
                )
            )
    return tuple(out)


def pool(style: str) -> list[CorpusTicket]:
    """取某一风格的语料池。"""
    return [t for t in load_corpus() if t.style == style]


def pick(
    rng: random.Random,
    level: str,
    *,
    predicate: Callable[[str], bool] | None = None,
    kinds: frozenset[str] | None = None,
) -> CorpusTicket | None:
    """按**业务层**抽一条话术。允许的风格全被筛空则返回 None。

    注意入参是业务层（L1/L2/L3），不是风格 —— 允许哪些风格由 `level_to_styles`
    决定，调用方不需要知道这层映射。

    ★ **在允许风格的并集里均匀抽**，不是"按顺序逐池回退"。并集里各风格各占
    自己那部分条数，于是权重天然等于语料里的自然占比（`explicit` 2327 条、
    `defect` 172 条 → 并集里 defect 占 6.9%）。改成并集抽是为了修一处实测
    失真：上一版是"第一个非空池通吃"，而 L2 的首选是只有 172 条的 defect、
    L1+L2 又占样本 82% —— 那 172 条被反复抽中，报表上 defect 占 43.0%，
    而语料里只有 2.6%（**放大 18.7 倍**），vague 则从 62% 被压到 17.2%。
    逐池回退还有一个更隐蔽的毛病：L1/L2 的实际选择完全由"哪个池子排前面"
    决定，等于给一个与订单无关的维度人为加了权。

    `predicate` 收**正文**、返回是否可用，用来表达"这句话必须与订单相容"
    这类约束（见 `presupposes_receipt`）。它在并集上过滤 —— 被筛掉的句子
    不会歪曲整体占比，因为剩下的仍是各风格按自身条数参与。

    ★ **故意不做"重抽 N 次"**：重抽在池子被筛得只剩几条时会退化成硬塞，
    而且调用方看不出约束到底生效没有。改成"筛空即 None"，让调用方明确
    知道约束没被满足，从而自己决定该换话题还是保留原样 —— 拿到 None 是
    一条**可处理的事实**，拿到一句勉强合格的句子则是**看不见的谎言**。

    ★ `kinds` 是**诉求维度**的筛选（缺陷 ㉙）。它存在的理由与 `predicate`
    不同：`predicate` 说"这句不能用"，`kinds` 说"我要的就是这几类"。

    背景：造数原先只有"按业务层抽"这一个维度，于是**诉求分布完全等于
    语料的自然分布**。实测 `compensate` 只占 0.6%，`price_protect` 更少 ——
    评测集里就永远抽不到它们，`R10` / `R11` 恒为 0 条 case。
    **而"0 条 case"和"规则没写"在报表上长得一模一样。**

    评测集和真实流量对分布的要求相反：真实流量要自然分布，评测集要
    **按维度覆盖**（这正是 `_stratified_sample` 已经在做的事，只是它当时
    只覆盖"条件"，没覆盖"诉求"）。所以这里给调用方一个显式的分布杠杆，
    而不是继续靠运气。
    """
    candidates = [
        t
        for style in level_to_styles(level)
        for t in pool(style)
        if (predicate is None or predicate(t.body))
        and (
            kinds is None
            or classify_request(t.body, t.scene) in kinds
        )
    ]
    return rng.choice(candidates) if candidates else None


def corpus_stats() -> dict[str, Any]:
    """语料体检数据 —— 供 README / 报表 / 自检脚本引用。

    `sha256` 是刻意暴露的：指标要能追到具体字节。多份语料时给出**合并指纹**
    （各文件 sha256 按名字排序拼接后再哈希），单文件指纹列在 `per_file` 里。
    """
    items = load_corpus()
    by_style: dict[str, int] = {}
    by_scene: dict[str, int] = {}
    for t in items:
        by_style[t.style] = by_style.get(t.style, 0) + 1
        by_scene[t.scene] = by_scene.get(t.scene, 0) + 1

    per_file: dict[str, dict[str, Any]] = {}
    for name in CORPUS_FILES:
        p = CORPUS_DIR / name
        per_file[name] = {
            "exists": p.exists(),
            "bytes": p.stat().st_size if p.exists() else 0,
            "sha256": _sha256(p) if p.exists() else "",
        }
    combined = hashlib.sha256(
        "".join(f"{n}:{per_file[n]['sha256']}" for n in sorted(per_file)).encode()
    ).hexdigest()

    return {
        "file": str(CORPUS_DIR / PRIMARY_CORPUS),
        "files": [str(CORPUS_DIR / n) for n in CORPUS_FILES],
        "exists": all(per_file[n]["exists"] for n in CORPUS_FILES),
        "sha256": combined,
        "per_file": per_file,
        "unique_phrasings": len(items),
        "by_style": dict(sorted(by_style.items())),
        "by_scene": dict(sorted(by_scene.items(), key=lambda kv: -kv[1])),
        # 业务层实际能抽到多少种话术 —— 直接回答"扩到 2000 客户会重复吗"
        "by_business_level": {
            lv: len({t.body for s in level_to_styles(lv) for t in pool(s)})
            for lv in ("L1", "L2", "L3")
        },
        "source": CORPUS_SOURCES.get(PRIMARY_CORPUS, {}),
        "sources": {n: CORPUS_SOURCES.get(n, {}) for n in CORPUS_FILES},
    }
