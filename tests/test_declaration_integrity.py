"""依赖与配置的"声明-实装"一致性测试（缺陷 ⑧ 的回归护栏）。

★ 为什么需要这个文件：

缺陷 ⑧ 不是崩溃、不是错数，而是**声明了却不存在的能力**：

    pyproject.toml 里躺着 sse-starlette / aiosqlite / structlog 三个
    从不 import 的依赖；config.py 里躺着 checkpointer 这个从没人读的
    配置项；description 里写着"AgentScope 对照实现"而它只存在于
    依赖声明里。它们的共同点是：**读起来像能力，跑起来是空的。**

比崩溃更安静 —— 崩溃会有人来修，"以后要做"不会。

所以这里不测"某个函数返回什么"，而是测**声明与实装之间不许有缺口**。
这类测试的价值不在一时的红绿，而在于以后有人（包括我自己）往
pyproject 里加依赖、往 config 里加开关时，会被问一句"它接上了吗"。
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"

# 允许"声明但不用"的例外，每一条都要写清理由（否则例外会自我繁殖）
ALLOWED_UNUSED = {
    # 由 pydantic-settings 传递依赖引入，但 config.py 显式用了 env_file=，
    # 属于"我自己依赖它"而非"蹭别人的依赖"，显式声明更稳。
    "python-dotenv",
    # langchain 顶层只在 graph/middleware.py 的 budget_middleware() 里
    # 作为**备选实现**被 import。它是 A/B 对照的一半，删了就没法切换。
    "langchain",
}


def _imported_modules() -> set[str]:
    """扫出项目真正 import 的顶层模块名。

    ★ 用 AST 而不是正则：正则会把注释和 docstring 里的
    `from langchain.agents.middleware import wrap_model_call`
    当成真 import —— 而那句在那个文件里恰恰是**没走到的备选路径**。
    差一点就把"从未调用"的结论做成"看起来在用"。
    """
    mods: set[str] = set()
    files = list(SRC.rglob("*.py")) + list((PROJECT_ROOT / "tests").rglob("*.py"))
    files += [p for p in PROJECT_ROOT.glob("_*.py")]

    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mods.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    mods.add(node.module.split(".")[0])
    return mods


def _declared_deps() -> dict[str, str]:
    """解析 pyproject 的 [project.dependencies]，返回 {包名: 声明行}。"""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for spec in data["project"]["dependencies"]:
        # "uvicorn[standard]>=0.30" → "uvicorn"
        name = spec.split("[")[0].split(">=")[0].split("==")[0].split("<")[0].split("!")[0]
        out[name.strip()] = spec
    return out


def test_no_declared_dependency_is_never_imported() -> None:
    """每个声明的依赖都必须真的被 import（或在 ALLOWED_UNUSED 里附理由）。

    这条测试第一次跑就会抓出缺陷 ⑧ 里的三个幽灵依赖。
    """
    imported = _imported_modules()
    declared = _declared_deps()

    # 包名 → import 名 的常见映射（只登记我们真用到的）
    alias = {
        "python-dotenv": "dotenv",
        "sse-starlette": "sse_starlette",
        "pydantic-settings": "pydantic_settings",
        "langchain-openai": "langchain_openai",
        "langchain-core": "langchain_core",
        "sqlalchemy": "sqlalchemy",
        "fastapi": "fastapi",
    }

    unused = []
    for pkg in declared:
        mod = alias.get(pkg, pkg.replace("-", "_"))
        if mod in imported or pkg in imported:
            continue
        if pkg in ALLOWED_UNUSED:
            continue
        unused.append(f"{pkg}（import 名 {mod}）")

    assert not unused, (
        "以下依赖被声明但从没 import —— 要么它在假装能力，要么声明该删：\n  "
        + "\n  ".join(unused)
        + "\n（确需保留的，请加进 ALLOWED_UNUSED 并写明理由）"
    )


def test_allowed_unused_entries_are_still_declared() -> None:
    """例外清单不许腐烂：ALLOWED_UNUSED 里的包必须仍在 pyproject 里。

    否则会出现"例外比被例外者活得久"—— 包都删了，豁免还挂着，
    下一个人以为那是个真依赖。
    """
    declared = set(_declared_deps())
    stale = ALLOWED_UNUSED - declared
    assert not stale, f"ALLOWED_UNUSED 里这些包已不在 pyproject.toml 中：{stale}"


def test_description_does_not_promise_unimplemented_components() -> None:
    """description 里承诺的组件必须真的存在。

    原先写的是「LangGraph 主实现 + AgentScope 对照实现」，
    而 AgentScope 对照实现只存在于可选依赖声明里 —— 全项目 grep
    `agentscope` 只有 3 处文字命中，没有一行代码。
    """
    desc = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["description"]
    imported = _imported_modules()

    for promised, module in (("AgentScope", "agentscope"), ("LangGraph", "langgraph")):
        if promised in desc:
            assert module in imported, (
                f"description 承诺了 {promised} 相关能力，但代码里从没 import 过 {module}：{desc!r}"
            )


def test_dead_config_fields_are_documented_or_removed() -> None:
    """config.py 里不许有"没人读"的配置项。

    原先 `log_level: str = "INFO"` 全项目零消费者：调它没有任何效果，
    却让人以为调高日志级别能看到更多东西。

    判据：Settings 的每个字段名，必须在 src/ 里至少出现两次
    （一次是定义，一次是消费）。

    ★ 为什么扫描范围要包含 config.py **自身**（早先版本把它整个排除了）：

    排除它的理由是"只在自己文件里出现的字段等于没人读"。这个理由对一半 ——
    `self.llm_profile` 这种访问是**真消费**：它经 `Settings.endpoint`
    解析成 `Endpoint` 再流到模型构造与成本换算，是实打实接了线的。
    排除 config.py 会把这类字段误判成死旋钮，逼人往例外清单里塞条目，
    而**例外清单一长，这个测试就废了**。

    所以现在扫两类消费形式：
      - 别处的 `settings.{name}` / `"{name}"` / `'{name}'`
      - config.py 自己：`任意接收者.{name}` 的属性访问

    ★ 判据为什么是"任意接收者"而不是枚举 `self.` / `cfg.`：

    早先写死了 `self.{name}` —— 那是在假设"字段只会在 `Settings` 的方法体里
    以 `self.x` 被读"。这个假设在解析逻辑被抽成模块级函数后立刻失效：
    内核 `_resolve_endpoint(cfg, ...)` 里读的是 `cfg.llm_base_url`，
    于是 8 个**真正接了线**的字段集体变红，测试报"没人读这些配置项"。

    差别在哪：`self.` 是**一种写法**，属性访问才是**消费这件事本身**。
    把判据绑在写法上，每次重构消费点都要来改这个测试；绑在消费上，
    它只在"确实没人读"时才红。用一个正则匹配"某个名字的点属性访问"即可
    —— 变量叫 `self` / `cfg` / `s` 都无所谓。

    ★ 正则只用 `\\b`，**不加前导负向后顾**：

    第一版写成 `(?<![\\w.])\\.{name}\\b`，本意是"别把更长名字的后缀算成命中"。
    但那个后顾禁止的是**点号前有任何单词字符**，于是 `cfg.llm_model` 里
    点号前正是 `g` —— **全部落空**，这 11 个真接了线的字段一个都没认出来。

    要防的其实是后缀扩展（`model` 误命中 `models`），而那件事 `\\b` 就够了：
    `\\b` 在 `model` 与 `models` 之间不成立。前缀方向根本不需要防 ——
    属性访问的形状就是"点 + 名字"，不存在"点 + 更长名字"能和它混淆的情况。

    （★ 上面这几行里的反斜杠是**双写**的：docstring 不是 raw 字符串，
    单写的 `\\w` 会让 Python 发 `SyntaxWarning: invalid escape sequence`。
    这种警告本身无害，但**留着它，将来真出问题的警告就被淹没了**。）
    """
    sys.path.insert(0, str(SRC))
    from ticket_agent.config import Settings

    field_names = set(Settings.model_fields)
    # 明确允许"只定义不消费"的：它们是给人看的元信息或在别处被间接使用
    allowed = {
        # 这两个由 graph/checkpoint.py 消费（它有独立模块，不在下面全量扫描里也会被扫到）
        "checkpointer",
        "postgres_dsn",
    }

    src_text = "\n".join(
        p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")
        if p.name != "config.py"
    )
    # config.py 自身：只认属性访问。定义行是 `X: str = ""`（前面没有 `.`），
    # 所以定义本身不会自我命中。
    own_text = (SRC / "ticket_agent" / "config.py").read_text(encoding="utf-8")

    dead = []
    for name in sorted(field_names):
        if name in allowed:
            continue
        # 字段名以属性访问或字符串键的形式被消费
        if (
            f"settings.{name}" in src_text
            or f'"{name}"' in src_text
            or f"'{name}'" in src_text
            # ★ 任意接收者的属性访问（`cfg.llm_model` / `self.llm_model` / …）。
            #   只加 `\b`：它挡住后缀扩展（`model` 不匹配 `models`），
            #   而前缀方向不需要挡 —— 见上面 docstring 里那次写错的教训。
            or re.search(rf"\.{re.escape(name)}\b", own_text)
        ):
            continue
        dead.append(name)

    assert not dead, (
        "以下配置项在 config.py 之外从没被读过 —— 它们是'看起来像能力'的开关：\n  "
        + "\n  ".join(dead)
        + "\n（要么接上，要么删掉；不要留一个拧了没反应的旋钮）"
    )


def test_env_example_database_url_is_synchronous() -> None:
    """`.env.example` 给出来的 DATABASE_URL 必须真能跑。

    原先写的是 `sqlite+aiosqlite:///...`，而数据层是同步 create_engine，
    照抄这行会直接抛：

        ImportError: The SQLAlchemy asyncio module requires that the Python
        'greenlet' library is installed ...

    一个示例文件里跑不起来的示例，比没有示例更糟 —— 它把"照抄就能用"
    变成了一个陷阱。这里只做静态检查（URL 不含 +aiosqlite/+asyncpg），
    避免测试依赖真实数据库。
    """
    env_example = PROJECT_ROOT / ".env.example"
    assert env_example.exists(), ".env.example 不见了 —— 它是快速开始的入口"
    text = env_example.read_text(encoding="utf-8")

    for line in text.splitlines():
        stripped = line.strip()
        # 只看真正生效的行（注释掉的示例是给人参考的，不算）
        if stripped.startswith("#") or not stripped.startswith("DATABASE_URL="):
            continue
        url = stripped.split("=", 1)[1]
        assert "+aiosqlite" not in url, (
            f"数据层是同步 create_engine，URL 不能带 +aiosqlite：{url}\n"
            "（这条正是缺陷 ⑧ 里那个照抄就崩的示例）"
        )
        assert "+asyncpg" not in url, f"数据层是同步 create_engine，URL 不能带 +asyncpg：{url}"


def test_checkpointer_factory_rejects_unknown_mode() -> None:
    """未知 checkpointer 模式必须报错，不许静默降级成内存。

    静默降级的后果是"以为状态已经落库了"—— 这正是缺陷 ⑧ 的核心教训。
    """
    sys.path.insert(0, str(SRC))
    from ticket_agent.graph.checkpoint import get_checkpointer

    with pytest.raises(ValueError, match="未知的 checkpointer 模式"):
        get_checkpointer("mysql")


def test_checkpointer_memory_returns_none() -> None:
    """memory 模式返回 None（不持久化），而不是 InMemorySaver。

    理由：InMemorySaver 的生命周期绑在图上，本项目每次跑工单都新建图，
    所以它不提供任何 None 给不了的能力，却会让人以为在持久化。
    """
    sys.path.insert(0, str(SRC))
    from ticket_agent.graph.checkpoint import get_checkpointer

    for mode in ("memory", "none", "off", ""):
        assert get_checkpointer(mode) is None, f"{mode!r} 应当不持久化"


def test_eval_harness_opts_out_of_persistence() -> None:
    """评测层必须显式传 checkpointer=False，不能靠默认值。

    ★ 这条钉的是一个实测出来的坑：持久 checkpointer 会让参数相同的
    第二次运行**带着第一遍的 state 重跑整张图**（消息 11→22 累积、
    工具被重复调用 5→10、成本翻倍）。评测的 run_id 是确定性的，
    所以第二次运行会命中 —— 而**第一次永远是对的**，最容易漏过 CI。

    用源码断言而非运行时断言：这个行为的正确性取决于"调用处写了什么"，
    而不是"跑起来返回什么"。
    """
    harness = (SRC / "ticket_agent" / "eval" / "harness.py").read_text(encoding="utf-8")
    assert "checkpointer=False" in harness, (
        "eval/harness.py 必须显式传 checkpointer=False —— "
        "省略会让持久 checkpointer 把重跑变成'带着旧 state 重来一遍'"
    )
    assert "run_ticket(" in harness


def test_pyproject_parses_and_has_expected_scripts() -> None:
    """CLI 入口必须仍然声明着（防止改 pyproject 时误删）。"""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    scripts = data["project"].get("scripts", {})
    assert scripts.get("ticket-agent") == "ticket_agent.cli:app"


def test_eval_harness_write_calls_use_the_single_source_of_truth() -> None:
    """报表「写操作」列必须按 `registry.WRITE_TOOLS` 统计，不许另抄一份工具名。

    ★ 这条钉的是一个**实测出来的少算**：

    原实现把写工具名硬编码成四个**退款线**工具
    （create_refund / block_order / close_ticket / escalate_to_human），
    把 `submit_after_sales_action` / `mark_evidence_received` / `ask_customer`
    漏在统计之外 —— 而那三个在 `src/ticket_agent/tools/registry.py:140-150`
    的 `WRITE_TOOLS` 里都登记了。

    实测偏差（`_r10diff.py`，千级库 4/4 条目标 case 全中）：
        官方 write_calls = 1，审计口径实际写调用 = 3。

    ★★ 而且同一个缺陷在 `run_case` 里有**两处**：turn-1 一处、turn-2 一处
    （`res.write_calls +=` 那个分支）。只改一处的话，两轮 case 的写操作
    仍然少算，差额恰好等于售后线的写入次数（turn-1 计入、turn-2 不计入）——
    比原来更难发现，因为症状是"数字变小了"而不是"报错了"。

    形状与缺陷 ⑤（列柱标错）、⑰（键名漂移）同源：
    **统计口径分散在两处，其中一处漏了**，而报表照常出、看不出任何异常。

    ★ 为什么用源码断言而不是运行时断言：要守的是"统计时读的是哪个集合"，
    而这取决于调用处写了什么，不是跑起来返回什么。与
    `test_eval_harness_opts_out_of_persistence` 同一条理由。
    """
    harness = (SRC / "ticket_agent" / "eval" / "harness.py").read_text(encoding="utf-8")
    sys.path.insert(0, str(SRC))
    from ticket_agent.tools.registry import WRITE_TOOLS

    # 售后线三个写工具必须真的在 WRITE_TOOLS 里（否则这条断言本身就失去了锚）
    for missing in (
        "submit_after_sales_action",
        "mark_evidence_received",
        "ask_customer",
    ):
        assert missing in WRITE_TOOLS, f"{missing} 不在 WRITE_TOOLS 里"

    hits = harness.count('t.get("name") in WRITE_TOOLS')
    assert hits >= 2, (
        f"eval/harness.py 里按 WRITE_TOOLS 统计写操作的地方只有 {hits} 处 —— "
        f"turn-1 与 turn-2 两处都要按 registry.WRITE_TOOLS 统计。"
        f"少一处的后果：两轮 case 的写操作仍被少算，"
        f"而差额恰好是售后线写入次数（turn-1 计入、turn-2 不计入），"
        f"症状是数字变小而不是报错，最难发现"
    )
    # 那四个退款线工具名不许再作为统计口径出现（注释/docstring 里可以）
    for line in harness.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith(">"):
            continue
        assert '("create_refund", "block_order", "close_ticket", "escalate_to_human")' not in line, (
            "eval/harness.py 又出现了硬编码的四工具名统计口径 —— "
            "唯一事实来源是 registry.WRITE_TOOLS"
        )
