"""评测进度探针：估算正在跑的 eval 到了哪一步。

★ 为什么需要它：`eval` 是一条命令跑到黑，中途不打印进度条（打印会污染
   `--report` 的 markdown 输出）。但**每次工具调用都会往 `audit_log` 写一行**，
   所以"跑到哪了"这件事其实一直在库里，只是没人去看。

★ 为什么数 audit_log 而不是 runs 表：本项目**没有 runs 表** —— run 的元信息
   只活在进程内存里（这正是缺陷 ⑦：人工能看到待办却裁决不了）。
   库里只有业务表 + `audit_log`。audit_log 的 `created_at` 是**业务时钟**
   （评测锚点固定），所以不能用时间戳分窗口，只能按 `id` 递增切最近的一段。

用法：
    python _progresscheck.py                  # 采样 60 秒，报速率
    python _progresscheck.py --sample 0       # 不等待，只打当前快照
    python _progresscheck.py --expected 120   # 对比期望的 run 数（每次 run 约 1 次 get_ticket）
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DB = PROJECT_ROOT / "eval_out" / "ticket_agent.db"

# 每次 run 的第一步都是 get_ticket（入口工具），所以它的条数≈已完成的 run 数。
ENTRY_TOOL = "get_ticket"
WRITE_TOOLS = ("create_refund", "close_ticket", "block_order", "escalate_to_human")


def _connect(path: Path) -> sqlite3.Connection:
    # ★ 只读打开：正在跑的评测进程持着写锁，用默认模式连会撞 "database is locked"。
    #   uri=True + mode=ro 让 SQLite 走只读，连 WAL 里尚未检查点的数据也能看见。
    uri = f"file:{path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5.0)


def _snapshot(con: sqlite3.Connection) -> dict[str, int]:
    cur = con.cursor()
    tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "audit_log" not in tables:
        raise SystemExit(f"库里没有 audit_log。现有的表：{sorted(tables)}")
    counts = {
        "audit_total": cur.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        "entry": cur.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action=?", (ENTRY_TOOL,)
        ).fetchone()[0],
    }
    marks = ",".join("?" * len(WRITE_TOOLS))
    counts["writes"] = cur.execute(
        f"SELECT COUNT(*) FROM audit_log WHERE action IN ({marks})", WRITE_TOOLS
    ).fetchone()[0]
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="数一数评测跑完多少了")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--expected", type=int, default=0, help="期望的总 run 数（0 = 不对比）")
    ap.add_argument("--sample", type=int, default=60, help="采样多少秒来估速率（0 = 不采样）")
    args = ap.parse_args()

    path = args.db
    if not path.exists():
        print(f"数据库不存在：{path}")
        return 1

    try:
        con = _connect(path)
    except sqlite3.OperationalError as exc:
        print(f"连不上数据库（评测可能正忙）：{exc}")
        return 2
    try:
        before = _snapshot(con)
        cur = con.cursor()
        by_action = Counter(
            r[0]
            for r in cur.execute(
                "SELECT action FROM (SELECT action FROM audit_log ORDER BY id DESC LIMIT 600)"
            )
        )
    finally:
        con.close()

    print(f"audit_log 共 {before['audit_total']} 行")
    print(f"  {ENTRY_TOOL:24s} {before['entry']:6d}   ← 每完成一次 run 至少 +1")
    print(f"  {'写操作合计':24s} {before['writes']:6d}")
    print("最近 600 次工具调用里最常见的是：")
    for act, n in by_action.most_common(6):
        print(f"  {act:26s} {n:5d}")

    if args.expected:
        done = before["entry"]
        pct = done / args.expected * 100
        print(f"\n进度：{done}/{args.expected} 次 run（{pct:.0f}%），还差 {max(0, args.expected - done)}")

    if args.sample <= 0:
        return 0

    time.sleep(args.sample)
    try:
        con = _connect(path)
    except sqlite3.OperationalError as exc:
        print(f"第二次采样失败：{exc}")
        return 0
    try:
        after = _snapshot(con)
    finally:
        con.close()

    delta = after["entry"] - before["entry"]
    rate = delta / args.sample * 60
    print(f"\n{args.sample}s 内新增 {delta} 次 run → 约 {rate:.2f} 次/分钟")
    if args.expected and rate > 0:
        remain = max(0, args.expected - after["entry"])
        if remain:
            print(f"按此速度，剩余 {remain} 次约需 {remain / rate:.0f} 分钟（{remain / rate / 60:.1f} 小时）")
        else:
            print("按此计数已经跑满 —— 等进程落盘报表即可。")
    elif rate == 0:
        print("★ 这一分钟没有新 run —— 要么在等模型返回（hy3 单条 ~28s），要么已经跑完了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
