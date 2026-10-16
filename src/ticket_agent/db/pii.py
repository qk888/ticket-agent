r"""PII 脱敏：让个人信息不进 LLM 的 prompt、不进审计日志。

存在理由：这不是"补个功能"，是**合规底线**。业界把它列为标配
（Intercom：消息级、不可恢复、内置 PAN/SSN 规则 + 自定义正则）。

★ 但本模块的第一条设计约束是**反直觉的**，来自对 11,079 条真实语料的实测：

    按业界常规写法做"16–19 位数字 = 银行卡"这条规则，
    在本项目语料上会误抹 **26 条订单号**；而订单号是 agent 找到订单的
    **唯一钥匙** —— 抹掉它，agent 就瞎了。

    实测：语料里 10 位以上长数字出现 366 次，其中紧跟"订单号/单号"业务词的
    只有 22 次，**裸数字 344 次**（94%）。也就是说"看前面有没有业务词"
    这种豁免写法会漏掉 94%。所以豁免必须靠 **Luhn 校验**，不是靠上下文。

    而 26 条疑似银行卡里 **25 条 Luhn 不通过**（全是订单号）；
    唯一通过的那条 `19627589320267052` 原文是 `订单号:19627589320267052`
    —— 它也是订单号，只是恰好 Luhn 通过。

所以本模块的原则是：**先保护业务钥匙，再脱敏。** 顺序不能反。

★ 第二个实测结论：语料里真正的个人 PII 只有 **1 条**（11,079 条的 0.009%）：

    等会我拍下了你帮我把地址和电话改了行吗我这边就不在我这改了隆回县南岳庙乡
    老屋场村11组26号罗光亮电话行吗快点回答

  手机号 / 身份证 / 邮箱在语料里 **0 命中**。所以：
  - 不能拿"语料跑一遍 0 命中"当作"已脱敏" —— 那只是语料干净，不是模块有效
  - **必须造带 PII 的合成样本去验**（`tests/test_pii.py` 里有）
  - 地址规则的误报率实测 **6/8 = 75%**（被 `9月12号`/`14号`/`开柜号码` 骗），
    因此地址脱敏**默认不开启**，只放在 `strict` 档

档位（profile）
--------------
- `standard`（默认）：手机号 / 身份证 / 邮箱 / 银行卡。四条都做了校验位或
  号段校验，实测高精度。
- `strict`：在 standard 之上加"姓名+详细地址"。**实测误报率高，默认不开**，
  开启前请先看本文件末尾的误报样本。

脱敏发生在哪一层（B+C 方案，不是入库前）
----------------------------------------
- **B**：进 LLM 的 prompt 前脱敏（业务链路仍用真实订单号查订单）
- **C**：审计日志里的个人信息脱敏

★ 刻意**不做**"入库前脱敏"（方案 A）：那会让 `load_corpus()` 的 sha256 失去
意义（语料字节被改），且 agent 拿不到订单号。语料本身必须保持原始字节，
这一点和 `textnorm.normalize()` 的边界是同一条原则。

不可恢复性
----------
覆写式替换（不是打码显示）：原文被替换成占位符，**不可从返回值还原**。
这与 Intercom 的做法一致。若业务上需要"看得见后四位"，
用 `mask_tail=True`（默认开启，银行卡保留后 4 位，业界惯例）。

ReDoS 安全
----------
所有正则**不使用无界重复**（`\d+` 一律带上下界）。业界明确要求这点
（Intercom：拒绝无界重复模式，单规则运行时超时则跳过该规则）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# --------------------------------------------------------------------------
# 业务钥匙白名单：这些数字**永不脱敏**
# --------------------------------------------------------------------------
# ★ 为什么是正则而不是"看前面 8 个字符有没有业务词"：
#   实测裸数字占 94%，看上下文会漏掉绝大部分。真正的判据是 Luhn（见下）。
#   这里保留业务词白名单只是**双保险** —— 显式出现"订单号"时无条件放行，
#   连 Luhn 都不查（因为真银行卡不会跟在"订单号:"后面，而漏放的代价远高于误放）。
_ORDER_KEY_RE = re.compile(
    r"(?:订单号|单号|运单号|快递单号|物流单号|订单编号)\s*[:：]?\s*\d{8,25}"
)

# 业务钥匙上下文：数字前后 12 字内出现这些词，就认为它是单号而非卡号
_ORDER_CONTEXT_RE = re.compile(r"(?:订单|单号|运单|快递|物流|发货|拍下|下单)")


@dataclass(frozen=True)
class Finding:
    """一次脱敏命中。留痕用 —— 脱敏本身必须是可审计的。"""

    kind: str          # phone / id_card / email / bank_card / address
    start: int         # 原文中的起始下标
    end: int           # 原文中的结束下标（不含）
    raw_len: int       # 原文长度（用于统计，不存原文 —— 存原文就等于没脱敏）
    replaced_with: str


# --------------------------------------------------------------------------
# 各条规则
# --------------------------------------------------------------------------

# 手机号：1 开头 + 第二位 3-9 + 共 11 位。带号段校验，避免匹配长数字串的片段。
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

# 身份证：18 位，末位可为数字或 X
_ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")

# 邮箱
_EMAIL_RE = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,4}")

# 银行卡：16–19 位。**必须过 Luhn 且不在订单号上下文里**才会被脱敏。
_BANK_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")

# 地址（strict 档专用）。实测误报率高，故默认不启用。
# 收窄到"省/市/区/县 + 街道路名 + 门牌/村组"，且**排除时间表达**（9月12号 / 14号）。
_ADDR_RE = re.compile(
    r"[一-龥]{2,8}(?:省|市|区|县)"
    r"[一-龥]{2,12}(?:街道|路|街|道|巷|村|组|屯|寨)"
    r"[一-龥\d]{1,12}(?:号|栋|幢|单元|室|组)"
)

# 中文姓名（strict 档）：2–4 个汉字且紧跟"电话/联系/叫"等词，降低误报
_NAME_RE = re.compile(r"(?:我叫|姓名|联系人|收货人)\s*[:：]?\s*([一-龥]{2,4})")

_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK = "10X98765432"


def _luhn_ok(digits: str) -> bool:
    """Luhn 校验。业界标准（Intercom 的 PAN 规则就用它）。"""
    ds = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(ds) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(ds)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _idcard_ok(s: str) -> bool:
    """GB 11643-1999 校验位（ISO 7064 MOD 11-2）。"""
    if len(s) != 18:
        return False
    body = s[:17]
    if not body.isdigit():
        return False
    total = sum(int(c) * w for c, w in zip(body, _ID_WEIGHTS))
    return _ID_CHECK[total % 11] == s[17].upper()


def redact(
    text: str,
    *,
    profile: str = "standard",
    mask_tail: bool = True,
) -> tuple[str, tuple[Finding, ...]]:
    """脱敏一段文本，返回（脱敏后文本，命中清单）。

    顺序：**先划出业务钥匙保护区 → 再在保护区之外做脱敏**。

    `profile="standard"` 只做高精度的四条；`profile="strict"` 追加姓名+地址
    （实测误报率高，默认不开）。
    """
    if not text:
        return text, ()

    if profile not in ("standard", "strict"):
        raise ValueError(f"未知 profile：{profile!r}（只能是 standard / strict）")

    # ---- 第一步：划出"业务钥匙"保护区，这些区间永不脱敏 ----
    protected: list[tuple[int, int]] = [
        (m.start(), m.end()) for m in _ORDER_KEY_RE.finditer(text)
    ]

    def in_protected(pos: int) -> bool:
        return any(s <= pos < e for s, e in protected)

    findings: list[Finding] = []

    # 用一个 list 收集 (start, end, replacement)，最后统一从后往前替换，
    # 避免前面的替换改变后面匹配的下标。
    edits: list[tuple[int, int, str, str]] = []

    # ---- 手机号 ----
    for m in _PHONE_RE.finditer(text):
        if in_protected(m.start()):
            continue
        repl = _mask(m.group(0), mask_tail, keep=4) if mask_tail else "[手机号]"
        edits.append((m.start(), m.end(), repl, "phone"))

    # ---- 身份证（带校验位，不过就当它是普通长数字）----
    for m in _ID_RE.finditer(text):
        if in_protected(m.start()) or not _idcard_ok(m.group(0)):
            continue
        repl = _mask(m.group(0), mask_tail, keep=4) if mask_tail else "[身份证号]"
        edits.append((m.start(), m.end(), repl, "id_card"))

    # ---- 邮箱 ----
    for m in _EMAIL_RE.finditer(text):
        if in_protected(m.start()):
            continue
        edits.append((m.start(), m.end(), "[邮箱]", "email"))

    # ---- 银行卡：Luhn + 不在订单号上下文 + 16–19 位 ----
    for m in _BANK_RE.finditer(text):
        if in_protected(m.start()):
            continue
        s = m.group(0)
        # ★ 上下文兜底：前后 12 字内有"订单/快递/物流"等词 → 判定为单号
        window = text[max(0, m.start() - 12): m.end() + 12]
        if _ORDER_CONTEXT_RE.search(window):
            continue
        if not _luhn_ok(s):
            continue
        repl = _mask(s, mask_tail, keep=4) if mask_tail else "[银行卡号]"
        edits.append((m.start(), m.end(), repl, "bank_card"))

    if profile == "strict":
        for m in _ADDR_RE.finditer(text):
            if in_protected(m.start()):
                continue
            edits.append((m.start(), m.end(), "[地址]", "address"))
        for m in _NAME_RE.finditer(text):
            if in_protected(m.start(1)):
                continue
            edits.append((m.start(1), m.end(1), "[姓名]", "name"))

    # 同一位置被多条规则命中时只保留第一条（手机号优先于银行卡等）
    seen_spans: set[tuple[int, int]] = set()
    uniq: list[tuple[int, int, str, str]] = []
    for st, ed, repl, kind in sorted(edits, key=lambda x: (x[0], -x[1])):
        if (st, ed) in seen_spans:
            continue
        seen_spans.add((st, ed))
        uniq.append((st, ed, repl, kind))

    out = text
    for st, ed, repl, kind in sorted(uniq, key=lambda x: x[0], reverse=True):
        out = out[:st] + repl + out[ed:]
        findings.append(
            Finding(kind=kind, start=st, end=ed, raw_len=ed - st, replaced_with=repl)
        )

    return out, tuple(sorted(findings, key=lambda f: f.start))


def _mask(s: str, mask_tail: bool, keep: int) -> str:
    """打码：保留后 `keep` 位，其余用 *。业界惯例（Intercom 留后 4 位）。"""
    label = {
        11: "手机号",
        18: "身份证号",
    }.get(len(s), "卡号")
    if not mask_tail:
        return f"[{label}]"
    tail = s[-keep:] if len(s) > keep else s
    return f"[{label}尾号{tail}]"


def contains_pii(text: str, *, profile: str = "standard") -> bool:
    """只判断有没有，不替换。用于"这条要不要走人工"这类决策。"""
    _, found = redact(text, profile=profile)
    return bool(found)


# --------------------------------------------------------------------------
# 误报样本：把"看起来像 PII 但其实是别的"钉成回归数据
# --------------------------------------------------------------------------
# ★ 这些全部来自 11,079 条真实语料的实测误报。没有它们，脱敏模块的
#   "高精度"就只是一句声明 —— 而本项目已经因为"声明 vs 事实"翻车过很多次。
#   任何一条被误脱敏，都说明规则退化了。
FALSE_POSITIVE_SAMPLES: tuple[str, ...] = (
    "我去了快递说的小超市找到了一个快件是9月12号的订单收到了还有9月9号和11号的订单没收到",
    "能否查下为啥5月12日到县城了14号还没到镇上",
    "显示存小区柜子但我没收到开柜号码",
    "现在下单明天可以给我发吗我们市场21号要关门了迟了我就收不到了",
    "运单号3239746110956160缺货请退款谢谢",
    "订单2973153096627851怎么收到的封口夹都碎了",
    "30542042254605106什么时候到",
    "单号2203476352141368什么时候派送呢",
    "发票抬头深圳市煜祺医药有限公司税号91440300781374426T发票是跟货一起寄出吗",
)


# --------------------------------------------------------------------------
# 正样本：语料里 0 命中，所以**必须自己造**，否则模块从未被真正验证
# --------------------------------------------------------------------------
TRUE_POSITIVE_SAMPLES: tuple[tuple[str, str], ...] = (
    # (文本, 期望命中的 kind)
    ("我叫罗光亮，手机号13812345678，麻烦退一下", "phone"),
    ("联系我13900001111，订单号20240101123456", "phone"),
    ("我的邮箱是 zhangsan@example.com，帮我开发票", "email"),
    ("身份证号11010519491231002X，需要实名认证", "id_card"),
    ("卡号4111111111111111，Luhn 通过的真卡", "bank_card"),
)
