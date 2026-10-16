"""把 `_AFTER_SALES_KIND_LABEL` 提到模块级（它在 `_expected_action` 里也要用）。

上一版把它写在 `_summarize_action` 函数体内，`_expected_action` 引用时
NameError。这暴露的正是缺陷 ㉓「两份实现各自漂移」的入口：**同一个映射
被两处引用就必须只有一处定义**。

本脚本做三件事：
  1. 从函数体内取出该 dict，连同缺陷 ㉟ 的注释上移到模块级
     （放在 `PROBE_NAMES` 附近，同类常量放一起）；
  2. `_summarize_action` / `_expected_action` 里的引用改为模块级名；
  3. 顺手把 docstring 里"函数内"的说法改准。

用法：python _fixkindmap.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent
HARNESS = ROOT / "src" / "ticket_agent" / "eval" / "harness.py"

BLOCK = '''    #: 缺陷 ㉟：`submit_after_sales_action` 是七个售后动作的**统一入口**
    #: （赔付/补发/换货/价保/开票/改址/催单），而本函数此前只认
    #: `create_refund / block_order / escalate_to_human / close_ticket` 四个名字。
    #: 后果：agent 真的写了售后动作（审计与库里都有记录），报表却写
    #: `observed_action='none'` —— 排查时会去查 agent，而真相是汇总函数
    #: 少认一个工具。七个售后动作全中，200 case 那批实测 11 条。
    #:
    #: ★ 为什么按 `args.kind` 而不是按工具名：这七个动作在轨迹里是同一个
    #:   工具名，只有参数不同。按工具名映射只能给出 "submit_after_sales_action"
    #:   这个标签，与 `_expected_action` 的 "resolve" 不可比。
    _AFTER_SALES_KIND_LABEL: dict[str, str] = {
        "exchange": "exchange",
        "reship": "reship",
        "compensate": "compensate",
        "price_protect": "price_protect",
        "issue_invoice": "invoice",
        "change_address": "change_address",
        "urge": "urge",
        "refund": "refund",
    }
'''


def main() -> int:
    h = HARNESS.read_text(encoding="utf-8")
    assert h.count(BLOCK) == 1, f"定位块命中 {h.count(BLOCK)} 次"

    # 1. 从函数体内移除
    h = h.replace(BLOCK, "")

    # 2. 插到模块级（PROBE_NAMES 定义之后）
    anchor = "PROBE_NAMES: tuple[str, ...] = _PROBE_NAMES"
    assert h.count(anchor) == 1
    module_level = BLOCK.rstrip("\n") + "\n"
    h = h.replace(
        anchor,
        anchor
        + "\n\n\n"
        + module_level,
    )

    # 3. `_summarize_action` docstring 里补一句指向模块级常量
    h = h.replace(
        "    两个问题不能用一个字段回答。\n    \"\"\"\n    for t in trace:",
        "    两个问题不能用一个字段回答。\n"
        "    \"\"\"\n"
        "    # ★ 缺陷 ㉟：七个售后动作的 kind → 标签映射在模块级\n"
        "    #   （`_AFTER_SALES_KIND_LABEL`），`_expected_action` 用同一张表\n"
        "    #   从 golden 反推 —— 两处一张表，加 kind 时不会只改一边。\n"
        "    for t in trace:",
    )
    HARNESS.write_text(h, encoding="utf-8")
    print(f"harness.py 改完（{len(h)} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
