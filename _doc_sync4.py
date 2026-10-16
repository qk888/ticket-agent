"""#2 口径变更标注 —— 给「基线读数」与「过期测试数」加注。（v2 重写）

★ v1 为什么不复用
------------------
v1 的替换锚点是**跨行字面量**，本项目在 `_doc_sync2.py` 与 v1 各失败一次
（4 个前导空格 / A5 命中 0）。而 A5 失配的原因更严重：锚点落在 ```` ``` ````
**之间**，按原计划插进去会把 Markdown 说明变成**代码块内容**。
所以 v2 换设计，而不是去调空格数：

  - 三种操作分开：`SUB`（子串替换）/ `INS`（在某行后插入若干行）
    / `REP`（整行替换）。锚点一律取**单行内的子串**，
    绝不跨行、绝不猜缩进 —— 跨行字面量是本项目三次文档事故的共同成因。
  - `INS` 的锚点**必须选在代码围栏外**。
  - 每条都带 `expected`，命中数不符就 abort 且**一个字都不写盘**。
  - 命中数针对**运行中的文本**，不是原文 —— 所以同一串出现多次时
    必须先替长的、再替短的（v1 的「另外 139 条」在 README 命中 2）。

★ 本轮**故意不做**的两件事（都是「不凭猜改」）
----------------------------------------------
  ① README「七个测试文件」→ 十二：需先数这一节实际枚举了几个文件；
  ② 「另外 139 条」→ 改成 224：该句的 N **不是**「总数−1」
     （144 − 139 = 5 ≠ 1），语义要读上下文才知道，不能拿 225−1 去套。

★ 要标的两个维度（缺一个等于没写）
------------------------------------
  A. **基线口径变更**：`agents/scripted.py` 的 `_request_amount_of`
     原先从工单**正文自由文本**重抽赔付/价保金额（`_amount_in_text(body)`），
     现改读**结构化字段 `tickets.request_amount`**，并把该键补进
     `get_ticket` 返回体。实测依据 `_r10diff.py`：千级库 4/4 条
     `R10:compensate` 由「终态卡 open、after_sales=[]、写调用 0」转为
     `passed=True` ⇒ 所有 `kind=scripted` 历史读数**尚未按新口径重跑**。
  B. **评测集换代**：那批读数跑在 `request_kind` 引入**之前**的
     **纯退款线 11 条件**评测集（报表 `eval_out/final-scripted-multi-k3-20261010-090158.md`
     条件表前五条全是 R1–R3，R8–R14 一条没有），与现在
     **241 条 / 24 条件**的新集**不可比**。

用法：
    python _doc_sync4.py           # 只报不改
    python _doc_sync4.py --apply   # 全部命中数 == expected 才写盘
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
README = ROOT / "README.md"
OVERVIEW = ROOT / "交付总览.md"

APPLY = "--apply" in sys.argv

# ══════════════════════════════════════════════════════════════
# 操作表
#   ("SUB", 文件, old子串, new子串, expected, 说明)
#   ("INS", 文件, 锚点子串, [要插入的行...], expected, 说明)
# ══════════════════════════════════════════════════════════════

OPS: list[tuple] = [
    # ── A 组：基线口径变更（两个维度）────────────────────────────────
    (
        "SUB",
        README,
        "**按条件**（11 种条件全覆盖，报表里逐条列出）",
        "**按条件**（**11 种**条件全覆盖 —— 这是 `request_kind` 引入**之前**的"
        "纯退款线评测集，与下文 241 条 / 24 条件的新集**不可比**）",
        1,
        "README 表 B 条件分布头：补『评测集换代』维度",
    ),
    (
        "INS",
        README,
        ">   **这套评分标准下的上界参考**，证明的是**评测链路与编排链路是通的**，不是模型成绩。",
        [
            ">   ★ **口径变更**：下面表 B 的读数取自 2026-10-10，是**改口径之前**的基线。",
            ">     `agents/scripted.py` 的 `_request_amount_of` 原先从**工单正文自由文本**",
            ">     重抽赔付/价保金额（`_amount_in_text(body)`），现已改读**结构化字段",
            ">     `tickets.request_amount`**，并把该键补进 `get_ticket` 的返回体。",
            ">     实测依据 `_r10diff.py`：千级库上 4/4 条 `R10:compensate` 由",
            ">     「终态卡 `open`、`after_sales=[]`、写调用 0」转为 `passed=True`。",
            ">     ⇒ 表 B 那 1,683 次运行**尚未按新口径重跑**，属旧口径读数。",
        ],
        1,
        "README 文首表 B 口径说明块后：追加『基线口径变更』标注",
    ),
    (
        "INS",
        README,
        "命令：`python -m ticket_agent.cli report --size 605 --k 3 --seed 7 --customers 1000`",
        [
            "报表：`eval_out/final-scripted-multi-k3-20261010-090158.md`",
            "      （头部实测：`arm=multi；kind=scripted；size=561；seed=7；",
            "        corpus_sha256=1bb070b09c354582；corpus_unique=11079`）",
            "",
            "> ⚠️ **两个维度都变了**：① 这是**旧基线口径**（`_request_amount_of`",
            ">   改数据源之后未重跑）；② 它覆盖的是 `request_kind` 引入**之前**的",
            ">   **纯退款线 11 条件**评测集（条件表前五条全是 R1–R3，R8–R14 一条没有），",
            ">   与下文 241 条 / 24 条件的新集**不可比**。",
        ],
        1,
        "README 表 B：补报表落盘路径（磁盘上确有此文件）+ 三维标注。"
        "★ 该锚点所在段落未经围栏包裹（README:84-98 为纯文本，:113 才开围栏）",
    ),
    (
        "INS",
        README,
        "R9:reship` 10。",
        [
            "",
            "> ⚠️ 这同样是**旧基线口径**的读数（`_request_amount_of` 改读 "
            "`tickets.request_amount` 之前）；新口径下需重跑才能作为基线引用。",
        ],
        1,
        "README 缺陷 ㉙ 收尾段：241 case 读数补『旧基线口径』标注。"
        "★ 锚点取该段**最后一行**，避免插进句子中间（上一版插在 :2343 会切断 2344-2345 的续行）",
    ),
    (
        "INS",
        OVERVIEW,
        "> ★ **数据规模要用报表自己的种子去核**",
        [
            "> ⚠️ **口径变更（两个维度都要看）**：① 基线 `_request_amount_of` 已从工单正文",
            "> 重抽金额改为读 `tickets.request_amount`（配套把该键补进 `get_ticket` 返回体），",
            "> 这 1,683 次读数**尚未按新口径重跑**；② 它覆盖的是 `request_kind` 引入之前的",
            "> **纯退款线 11 条件**评测集，与 241 条 / 24 条件的新集**不可比**。",
            ">",
        ],
        1,
        "交付总览 第二组之后：补口径变更标注。★ 锚点已从代码块内（v1 用的 :415，"
        "落在 410/416 的围栏之间、命中 0，且按原计划插进去会让说明变成代码块内容）"
        "移到围栏外的 :418 —— 不是排版错，是设计错",
    ),
    (
        "SUB",
        OVERVIEW,
        "**11 种评测条件全覆盖**（",
        "**11 种评测条件全覆盖**（**纯退款线评测集，`request_kind` 引入之前；"
        "与 241 条 / 24 条件的新集不可比**；",
        1,
        "交付总览 第二组条件行：补『评测集换代』维度",
    ),
    # ── C 组：过期测试数（实测 225 collected / 12 个文件）────────────
    (
        "SUB",
        README,
        "144 个测试：契约一致性",
        "225 个测试：契约一致性",
        1,
        "README 目录树：144 → 225（实测 `pytest --collect-only -q` = 225 collected）",
    ),
    (
        "SUB",
        README,
        "# 144 passed",
        "# 225 passed",
        1,
        "README §6 测试代码块的预期输出：144 → 225",
    ),
    (
        "SUB",
        OVERVIEW,
        "144 个单元测试全绿",
        "225 个单元测试全绿",
        1,
        "交付总览 第五章：144 → 225",
    ),
    (
        "SUB",
        OVERVIEW,
        "**144 个**",
        "**225 个**",
        1,
        "交付总览 验证资产表：144 → 225",
    ),
    (
        "SUB",
        OVERVIEW,
        "144 passed |",
        "225 passed |",
        1,
        "交付总览 验证资产表：passed 数 144 → 225",
    ),
    (
        "SUB",
        OVERVIEW,
        "HTTP 服务、144 个测试全绿",
        "HTTP 服务、225 个测试全绿",
        1,
        "交付总览 第五章：144 → 225",
    ),
]


def _apply_ins(lines: list[str], anchor: str, new_lines: list[str]) -> tuple[list[str], int]:
    """在**第一行**包含 anchor 的那一行之后插入 new_lines。返回 (新行表, 命中数)。"""
    hits = [i for i, ln in enumerate(lines) if anchor in ln]
    if len(hits) != 1:
        return lines, len(hits)
    i = hits[0]
    return lines[: i + 1] + list(new_lines) + lines[i + 1 :], 1


# ══════════════════════════════════════════════════════════════
# D 组：实测驱动的三条（本组数字全部来自 `pytest --collect-only -q`
#     的逐文件分组，**不是**从 README 里抄的数做加法 —— 上一轮就是这么
#     错的：拿 README 写的 `test_corpus.py（24 个）` 去加，得出「七个文件
#     正好 144」，而实测那是 **26 个**，小计应为 **146**。）
#
# 实测逐文件（`pytest --collect-only -q` 分组，共 225）：
#   test_clarification_loop.py 21   test_corpus.py 26
#   test_declaration_integrity.py 9   test_endpoints.py 35
#   test_evidence.py 14   test_forbidden_probes.py 25
#   test_pii.py 25   test_policy_consistency.py 13
#   test_rules_consistency.py 23   test_service_api.py 19
#   test_textnorm.py 9   test_three_rates.py 6
#   §6 已枚举的 7 个文件小计 = 146；未枚举的 5 个小计 = 79
# ══════════════════════════════════════════════════════════════

OPS.extend(
    [
        (
            "SUB",
            README,
            "`test_corpus.py`（24 个）",
            "`test_corpus.py`（26 个）",
            1,
            "README §6：test_corpus 条数 24 → 26（实测；文档自 §4.4 那轮语料改动后就没跟上）",
        ),
        (
            "SUB",
            README,
            "七个测试文件，144 个测试，",
            "十二个测试文件，225 个测试，",
            1,
            "README §6 抬头：七个→十二个、144→225。★ 必须与下面那条 INS 配套："
            "正文只展开了 7 个，改成「十二」而不补清单就是虚报",
        ),
        (
            "INS",
            README,
            "十二个测试文件，225 个测试，",
            [
                "",
                "> 上表展开了其中 7 个文件（146 条）。另外 5 个是后加的，条目从简：",
                "> `test_clarification_loop.py`（21 条，多轮澄清闭环）/",
                "> `test_evidence.py`（14 条，凭证与任务输入字段可达性）/"
                "> `test_pii.py`（25 条，脱敏）/"
                "> `test_policy_consistency.py`（13 条，政策文本与代码一致性）/"
                "> `test_three_rates.py`（6 条，三率指标）—— 合计 79 条。",
                "> **146 + 79 = 225**，与 `pytest --collect-only -q` 的实测一致。",
            ],
            1,
            "README §6 抬头后：补列 5 个未展开的测试文件及其**实测**条数，"
            "否则 79 条测试在文档里凭空消失",
        ),
    ]
)

# ── 保留不改、只插说明的两处 ──────────────────────────────────
# 「另外 139 条」这个 N **不可靠**：同一段上一行写的是 `138 = 133 + 5`，
# 而「另外 139 条」比当时总数 138 还多 1 —— 它**写下当天就自相矛盾**，
# 不是后来改测试改坏的。上一轮算的「= 225 − 1 = 224」前提也不成立
# （那等于假设 N 是「总数减本条」，而 144 − 139 = 5 ≠ 1）。
# ⇒ 处置：**数字先不动**，只在其后插一句说明它已失效及为什么；
#   下一轮读到完整上下文后再决定是删数字还是换成不带数字的表述。
OPS.extend(
    [
        (
            "INS",
            README,
            "红的理由就与代码无关了。",
            [
                "",
                "> ⚠️ 那个「139」已失效：同一段上一行写的是 `138 = 133 + 5`"
                "（当时总数 138），139 比它还多 1 —— 这个数写下当天就自相矛盾。"
                "论点本身（这条是整个套件隔离的根基）不受影响。",
            ],
            1,
            "README 之一：给失效的「另外 139 条」加失效说明（不改数字）",
        ),
        (
            "INS",
            README,
            "测试能继续成立的前提**，整套隔离就架在 `_env_file=None` 上）、",
            [
                "  > ⚠️ 那个「139」已失效（上一行写的是 `138 = 133 + 5`，当时总数 138）；",
                "  > 论点本身不受影响。",
            ],
            1,
            "README 之二：同一条失效说明（同一句式出现两次，必须各配一个锚点）",
        ),
    ]
)


def main() -> int:
    texts: dict[Path, str] = {}
    for p in (README, OVERVIEW):
        texts[p] = p.read_text(encoding="utf-8")

    bad = False
    print("=== 替换表（每条必须恰好命中 expected 次）===\n")
    for op in OPS:
        if op[0] == "SUB":
            _, path, old, new, expected, why = op
            n = texts[path].count(old)
            ok = n == expected
            bad = bad or not ok
            print(f"{'OK ' if ok else '!! '}{path.name}  SUB 命中 {n}/{expected} —— {why}")
            print(f"     {old[:72]!r}")
            if ok:
                texts[path] = texts[path].replace(old, new, expected)
        else:
            _, path, anchor, new_lines, expected, why = op
            lines = texts[path].split("\n")
            lines, n = _apply_ins(lines, anchor, new_lines)
            ok = n == expected
            bad = bad or not ok
            print(f"{'OK ' if ok else '!! '}{path.name}  INS 命中 {n}/{expected} —— {why}")
            print(f"     锚点: {anchor[:72]!r}")
            if ok:
                texts[path] = "\n".join(lines)

    if bad:
        print(
            "\n!! 有替换命中数 ≠ expected，已 abort —— **一个字都没写**。\n"
            "   先按上面的锚点核对实际文本（多半是缩进/换行被重排版改过，\n"
            "   或者锚点落在了代码围栏内 —— 后者插入后会变成代码块内容）。\n"
            "   不要绕过断言硬改。参照 `_doc_sync2.py` 与本脚本 v1 的教训。"
        )
        return 1

    if not APPLY:
        print("\n（dry-run，未加 --apply，未写盘）")
        return 0

    for path in (README, OVERVIEW):
        t = texts[path]
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(t)
        print(f"已写入 {path.name}：{len(t)} 字符 / CRLF {t.count(chr(13) + chr(10))}")
    print("\n写盘完成。紧接着跑 `python _readmecheck.py` 验结构（围栏偶数 / CRLF / 行数下限）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())