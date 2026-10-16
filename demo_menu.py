"""一键启动菜单 —— 双击 `启动.bat` 打开的就是这个。

为什么要有它：`手动启动.md` 写的是“命令”，但命令解决不了
“我该在哪个窗口、先敲哪一条、跑完怎么知道成了”。
这里把顺序固定下来，每一步都先告诉你它在做什么、大概要多久。

★ 演示默认用**独立的** `eval_out/demo.db`，不碰 `eval_out/ticket_agent.db`。
  否则你一边看演示，一边就把正在跑的评测数据洗掉了。
  （这是踩过的坑：`seed --drop` 会重建整库，把别人的 120 次运行清空。）
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVAL_OUT = ROOT / "eval_out"
DEMO_DB = EVAL_OUT / "demo.db"
DEMO_ENV = {"DATABASE_URL": f"sqlite:///{DEMO_DB}"}
SIDECAR_PORT = 8787


# ══════════════════════════════════════════════════════════════
# 基础设施
# ══════════════════════════════════════════════════════════════


def setup_console() -> None:
    """把控制台切成 UTF-8，否则中文和 ✓ 会直接抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def cli(*args: str, env_extra: dict[str, str] | None = None, quiet: bool = False) -> int:
    """调用项目自己的命令行，输出直接打到当前窗口。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if env_extra:
        env.update(env_extra)

    if not quiet:
        print(f"\n$ ticket-agent {' '.join(args)}", flush=True)

    proc = subprocess.run(
        [sys.executable, "-m", "ticket_agent.cli", *args],
        cwd=str(ROOT),
        env=env,
    )
    return proc.returncode


def pause(msg: str = "按回车回到菜单...") -> None:
    try:
        input(f"\n{msg}")
    except (EOFError, KeyboardInterrupt):
        pass


def hr(title: str = "") -> None:
    if title:
        print(f"\n{'─' * 4} {title} {'─' * max(0, 56 - len(title))}")
    else:
        print("─" * 62)


def ask(prompt: str, default: str = "") -> str:
    try:
        got = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return got or default


def read_env_value(name: str) -> str:
    path = ROOT / ".env"
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(name + "=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip()
    return ""


def sidecar_alive() -> bool:
    """探 sidecar 的 /healthz。不依赖第三方库，用标准库打。"""
    import urllib.error
    import urllib.request

    token = read_env_value("WORKBUDDY_SIDECAR_TOKEN")
    req = urllib.request.Request(
        f"http://127.0.0.1:{SIDECAR_PORT}/healthz",
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        # 401 = 服务在跑，只是鉴权没过（多半是 .env 里没有 token）
        return exc.code == 401
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════
# 演示数据的读写（只读，用来挑一张有看头的工单）
# ══════════════════════════════════════════════════════════════


def demo_tickets() -> list[tuple[int, str]]:
    """返回 [(工单号, 状态)]。读不到就返回空列表。"""
    if not DEMO_DB.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{DEMO_DB}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT id, status FROM tickets ORDER BY id").fetchall()
        finally:
            con.close()
        return [(int(r[0]), str(r[1])) for r in rows]
    except Exception:
        return []


def pick_interesting() -> int | None:
    """挑一张最能说明问题的工单：优先"转人工"的（能看到 HITL 全流程）。"""
    tickets = demo_tickets()
    if not tickets:
        return None
    for tid, status in tickets:
        if status == "pending_human":
            return tid
    return tickets[0][0]


def ensure_demo_data() -> bool:
    """确保演示库有数据。没有就生成（约 3 秒）。"""
    if demo_tickets():
        return True
    hr("生成演示数据")
    print("这一步会往 eval_out/demo.db 里造一批合成业务数据（不会碰你的评测库）。")
    code = cli("seed", "--customers", "60", "--seed", "7", "--drop", env_extra=DEMO_ENV)
    if code != 0:
        print("\n★ 生成失败。多半是依赖没装好，先跑菜单里的 [4] 看看。")
        return False
    return True


# ══════════════════════════════════════════════════════════════
# 各菜单项
# ══════════════════════════════════════════════════════════════


def opt_quickstart() -> None:
    """首次体验：生成数据 → 挑一张工单 → 跑给你看。"""
    hr("首次体验")
    print("会做三件事：① 造演示数据 ② 列出现有工单 ③ 挑一张真的跑一遍。")
    print("大约 10 秒。用的内置“脚本化模型”，不需要任何 API Key。")

    if not ensure_demo_data():
        return

    hr("现有工单")
    cli("tickets", "--tenant", "acme", "--limit", "10", env_extra=DEMO_ENV)

    tid = pick_interesting()
    if tid is None:
        print("★ 没读到工单，跳过演示。")
        return

    hr(f"跑工单 #{tid}")
    print("下面是重点：Agent 每查一次数据、每做一次动作，都会实时打出来。")
    print("注意看 triage（侦察）→ resolver（判定并执行）→ verifier（复核）三段。")
    cli("run", str(tid), "--tenant", "acme", "--stream", env_extra=DEMO_ENV)


def opt_run_one() -> None:
    """跑指定工单。"""
    hr("跑一张工单")
    if not ensure_demo_data():
        return

    tickets = demo_tickets()
    print(f"演示库里有 {len(tickets)} 张工单：")
    for tid, status in tickets[:20]:
        print(f"   #{tid:<4} 状态 {status}")
    if len(tickets) > 20:
        print(f"   ...（还有 {len(tickets) - 20} 张）")

    default = pick_interesting() or 1
    raw = ask(f"\n输入工单号（直接回车用 {default}）：", str(default))
    try:
        tid = int(raw)
    except ValueError:
        print("★ 工单号必须是数字。")
        return

    arm = ask("跑哪种编排？ 1=多智能体(推荐)  2=单智能体  [1]：", "1")
    arm_name = "single" if arm.strip() == "2" else "multi"

    hr(f"跑工单 #{tid}（{arm_name}）")
    cli("run", str(tid), "--tenant", "acme", "--arm", arm_name, "--stream", env_extra=DEMO_ENV)


def opt_hitl() -> None:
    """人工审批 —— 这个项目的核心卖点之一。"""
    hr("人工审批（HITL）")
    print("高风险动作（金额超 500 / 已退过款 / 生鲜）不会自动执行，会挂起等人工决定。")
    print("这里让你亲手扮演那个“人工”。")

    pending = [(tid, st) for tid, st in demo_tickets() if st == "pending_human"]
    if not pending:
        print("\n当前没有待审批的工单。先跑几张（菜单 [2]），或者重新生成演示数据。")
        print("★ 小提示：多跑几张就容易撞上需要转人工的单。")
        return

    print(f"\n待审批队列（{len(pending)} 张）：")
    for tid, _ in pending:
        print(f"   #{tid}")

    default = pending[0][0]
    raw = ask(f"\n输入要裁决的工单号（直接回车用 {default}）：", str(default))
    try:
        tid = int(raw)
    except ValueError:
        print("★ 工单号必须是数字。")
        return

    choice = ask("怎么裁决？ 1=批准退款  2=拒绝  3=继续挂起  [1]：", "1")
    decision = {"1": "approve_refund", "2": "reject", "3": "keep_pending"}.get(choice.strip(), "approve_refund")

    hr("第一次：正常裁决")
    cli("approve", "--ticket", str(tid), "--tenant", "acme", "--decision", decision, env_extra=DEMO_ENV)

    print("\n★ 关键点：如果这张单的政策结论是“不该退”，上面会**以退出码 1 拒绝**并说明原因。")
    print("  这就是“物理护栏而不是提示词约定” —— 人工审批也不能悄悄绕过政策。")
    if ask("\n要不要再试一次“强行破例”（加 --override，会留审计记录）？ y/N：", "n").lower() == "y":
        cli(
            "approve", "--ticket", str(tid), "--tenant", "acme",
            "--decision", decision, "--override", env_extra=DEMO_ENV,
        )


def opt_tests() -> None:
    hr("跑自动化测试")
    print("144 个测试，约 6 秒。全绿说明这套代码在这台机器上是好的。")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    print(f"\n$ python -m pytest -q\n", flush=True)
    subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=str(ROOT), env=env)


def opt_eval() -> None:
    hr("跑评测出报告")
    print("用内置规则模型跑 40 条工单 × 3 轮，出一份准确率报告。约 1 分钟。")
    print("用的还是演示库，不影响你看别的。")

    if not ensure_demo_data():
        return

    cli(
        "report", "--size", "40", "--k", "3", "--seed", "7",
        "--customers", "60", "--kind", "scripted", env_extra=DEMO_ENV,
    )
    print(f"\n报告落在：{EVAL_OUT}")
    print("找最新的 eval-single-*.md / eval-multi-*.md，用记事本或浏览器打开都行。")


def opt_serve() -> None:
    hr("打开 HTTP 服务 + 网页控制台")
    print("起一个本地服务，然后浏览器打开就能看到控制台：")
    print("  工单列表 · 实时处置轨迹 · 人工审批按钮")
    print("\n地址：http://127.0.0.1:8000")
    print("★ 这个窗口会被服务占住，想停就按 Ctrl+C。")

    if ask("\n现在起服务？ y/N：", "y").lower() != "y":
        return

    # 起服务前先灌数据：库是空的话，控制台打开一片空白，
    # 看起来跟“程序坏了”一模一样。（这是踩过的坑，见缺陷 ⑳）
    print("\n先确认演示库有数据...")
    ensure_demo_data()

    print("\n服务起来后，浏览器会**自动打开**控制台。")
    threading.Timer(2.5, _open_console).start()

    cli("serve", "--host", "127.0.0.1", "--port", "8000", env_extra=DEMO_ENV)


def _open_console() -> None:
    """等服务醒过来再把浏览器叫起来，免得打开的是“无法访问”。"""
    import webbrowser

    url = "http://127.0.0.1:8000/"
    for _ in range(20):
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:  # noqa: BLE001 —— 还没起来，继续等
            time.sleep(0.5)
    try:
        webbrowser.open(url)
        print(f"\n✓ 控制台已在浏览器打开：{url}")
        print("  接口文档：http://127.0.0.1:8000/docs")
        print('  页面上的租户 Token 用：tok-acme')
    except Exception as exc:  # noqa: BLE001
        print(f"\n★ 自动打开浏览器失败（{exc}），手动访问：{url}")


def opt_real_model() -> None:
    """接本机 WorkBuddy 的 Hy3（零成本）。"""
    hr("接真模型（本机 Hy3，零成本）")
    print("Hy3 跑在本机 WorkBuddy 插件上，目前限时免费。代价是慢（每条约 28 秒）。")
    print("\n流程：① 起桥接  ② 自检  ③ 切到真模型  ④ 冒烟测试")

    if sidecar_alive():
        print("\n✓ 桥接已经在跑了，跳过第 ① 步。")
    else:
        print("\n桥接还没起。它需要单独一个窗口常驻（它一停，模型就用不了）。")
        print("我可以帮你**新开一个窗口**跑它。")
        if ask("现在新开窗口起桥接？ y/N：", "y").lower() == "y":
            try:
                flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                subprocess.Popen(
                    ["node", str(ROOT / "tools" / "wb_sidecar.mjs"), "--port", str(SIDECAR_PORT)],
                    cwd=str(ROOT),
                    creationflags=flags,
                )
                print("已新开一个窗口。等它打印出监听地址（约 2 秒）...")
                time.sleep(4)
            except FileNotFoundError:
                print("★ 没找到 node。先装 Node.js（https://nodejs.org），再回来试。")
                return
            if sidecar_alive():
                print("✓ 桥接起来了。")
            else:
                print("★ 桥接好像没起来。看那个新窗口里的报错；")
                print("  最常见的原因是 WorkBuddy 没登录（要先去 DeepSeek Harness 里登录）。")
                return
        else:
            print("好的。想手动起的话，在另一个窗口跑：")
            print(f'   cd "{ROOT}"')
            print(f"   node tools\\wb_sidecar.mjs --port {SIDECAR_PORT}")
            return

    hr("第二步：自检端点")
    print("不能跳过。有些转发层会**静默吞掉工具调用**，那样跑出来的准确率毫无意义。")
    if cli("endpoint", "--profile", "hy3", "--check") != 0:
        print("\n★ 自检没过，先解决它再往下。")
        return

    hr("第三步：切换到真模型")
    cli("model-kind", "llm")

    hr("第四步：冒烟测试")
    print("跑一张真工单，★ 重点看轨迹里有没有 tool_calls。")
    print("如果工具调用是空的，说明桥接有问题，别急着跑评测。")
    if not ensure_demo_data():
        return
    cli("smoke", "--tenant", "acme", env_extra=DEMO_ENV)

    hr("都通了")
    print("现在可以跑真模型评测了（菜单 [5] 用的是内置模型，真模型请用）：")
    print(f'   cd "{ROOT}"')
    print("   ticket-agent eval --size 40 --k 3 --arms single,multi --kind llm "
          "--seed 11 --customers 300 --report")
    print("\n★ 慢：40 条 × 3 轮 × 两臂约 2.5 小时。想快就换 DeepSeek（约 6 分钟、约 ¥16）。")


def opt_info() -> None:
    hr("当前配置")
    print("看当前用的是哪个模型、各角色能调哪些工具。")
    cli("info")
    hr("可用端点档案")
    cli("endpoint", "--list")


# ══════════════════════════════════════════════════════════════
# 菜单
# ══════════════════════════════════════════════════════════════

MENU = [
    ("1", "首次体验：生成数据 → 跑一张工单给你看", opt_quickstart),
    ("2", "跑一张工单（可以选工单号、选单/多智能体）", opt_run_one),
    ("3", "人工审批：扮演“人工”裁决一笔退款", opt_hitl),
    ("4", "跑测试，确认代码没坏（144 个，约 6 秒）", opt_tests),
    ("5", "跑评测，出一份准确率报告（约 1 分钟）", opt_eval),
    ("6", "打开 HTTP 服务（用浏览器调接口）", opt_serve),
    ("7", "接真模型（本机 Hy3，零成本，但慢）", opt_real_model),
    ("8", "看当前配置和可用模型", opt_info),
]


def banner() -> None:
    print()
    print("╔" + "═" * 60 + "╗")
    print("║" + "  售后工单处置 Agent —— 一键启动".ljust(52) + "║")
    print("╚" + "═" * 60 + "╝")
    print()
    print("  忘了命令怎么敲没关系，选数字就行。")
    print("  第一次用请选 [1]，大约 10 秒，不需要任何 API Key。")
    print()
    print(f"  项目位置：{ROOT}")
    print(f"  演示数据：{DEMO_DB}")
    print("  ★ 演示用的是独立数据库，不会动到你的评测数据。")


def menu_loop() -> int:
    while True:
        banner()
        print()
        for key, label, _ in MENU:
            print(f"   [{key}] {label}")
        print("   [0] 退出")
        print()

        choice = ask("请输入数字后回车：", "0").strip()

        if choice == "0":
            print("\n再见。")
            return 0

        picked = next((fn for key, _, fn in MENU if key == choice), None)
        if picked is None:
            print(f"\n★ 没有 [{choice}] 这个选项，请输入 0-8。")
            time.sleep(1)
            continue

        try:
            picked()
        except KeyboardInterrupt:
            print("\n（已中断，回到菜单）")
        except Exception as exc:  # noqa: BLE001 —— 菜单不能因为一步失败就整个退出
            print(f"\n★ 这一步出错了：{type(exc).__name__}: {exc}")
            print("  把这段报错发给我，我来修。")

        pause()


def main() -> int:
    setup_console()
    EVAL_OUT.mkdir(parents=True, exist_ok=True)

    # 支持非交互调用：`python demo_menu.py 1` 直接跑第一项（方便我自己验）
    if len(sys.argv) > 1:
        picked = next((fn for key, _, fn in MENU if key == sys.argv[1]), None)
        if picked is None:
            print(f"★ 没有 [{sys.argv[1]}] 这个选项。")
            return 1
        picked()
        return 0

    return menu_loop()


if __name__ == "__main__":
    sys.exit(main())
