"""并行分片驱动：把 `_resume.py` 的串行跑批拆成 N 个进程同时跑。

为什么需要它
-----------
千级真模型单臂约 12.6 小时，瓶颈是**等模型回**（Hy3 约 3s/turn），
CPU/磁盘基本闲着。切成 N 片同时跑，墙钟 ≈ 12.6 / N 小时。

★ 本脚本比「手工起 N 个进程」多做的事：**两臂也能同时跑**。
  早先的约束是「分片库 `eval_out/shard-*.db` 是固定名，两个 `_prun.py`
  会互相 `init_db(drop=True)` 删表」——那是真的，但解法不是不许并行，
  而是**把臂名加进库名/进度名**。所以这里默认 `--arms single,multi`，
  总并行度 = shards × 臂数。

三条硬约束（都有代码依据，不要绕）：
1. **每片一个 SQLite 库文件** —— `init_db(drop=True)` 是**整库 drop**。
   N 个进程共用一个库时，先跑完的那个会把别人正在用的表删掉，
   症状是随机的 `no such table` / 「重置失败」，而且**只在并行时才出现**。
2. **每片一个进度文件** —— 进度是 append-only jsonl，混写会串行阻塞，
   而指纹校验还会互相拒绝（arm 相同而内容不同）。
3. **shard / arm 的划分方式不进 `run_sig` 指纹** —— `--arm` 本身在指纹里
   （它改变单条读数的分组口径），但「跑哪一片」只决定**谁来跑**。
   所以同一分片重跑同一条命令必须还能通过自己的指纹校验。

断电后怎么办
-----------
**重跑同一条 `_prun.py` 命令**即续：某片的进度已凑齐它的 todo，
`_resume.py` 会打印「已完成 X 条，本次待跑 0 条」然后直接退出；
没跑完的片从它自己的进度文件接上。不存在「从头再来」。

合并为什么不会混进不可比的读数
---------------------------
每条 case 开头都 `apply_snapshot` 重置到 `initial_state`（`harness.py:400`），
每条 case 自洽且幂等。而且**每个分片都 seed 了同一份完整数据集**
（`seed_all` 不按片切），所以任选一片的库都能还原完整评测集清单 ——
合并步骤读的是 `shard-<arm>-0.db` 就是这个原因。

★ Hy3 限流提醒：并行度 = shards × 臂数，一次打太多会被本机 sidecar
  或插件侧限流。**先从 shards=4 × 2 臂 = 8 起**，盯
  `eval_out/_prun-*.log` 尾部有没有限流/超时，稳了再加。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVAL_DIR = ROOT / "eval_out"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="并行分片跑评测（起 N×臂 个 _resume.py 进程，合并后出正式报表）"
    )
    ap.add_argument("--shards", type=int, default=4, help="每臂分几片（总并行度 = shards × 臂数）")
    ap.add_argument(
        "--arms",
        default="single",
        help="逗号分隔的臂，例如 single,multi（默认只跑 single）",
    )
    ap.add_argument("--report", default="", help="报表路径；留空则按 <arm>-<kind>-<seed>c.md 自动命名")
    # ── 以下原样透传给 _resume.py（必须每一片都一样，否则指纹对不上）──
    ap.add_argument("--kind", default="llm", choices=("scripted", "llm"))
    ap.add_argument("--size", type=int, default=605)
    ap.add_argument("--customers", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--split", default="all")
    ap.add_argument("--k", type=int, default=3)
    # ★ 为什么要有这个开关：`_resume.py` 的 `run_sig` 指纹含
    #   size / k / seed / customers / kind —— 改了任何一个，旧进度文件
    #   就会被拒（"两批读数不可比，混进同一张报表就是缺陷 ⑨"）。
    #   那是**护栏在干活，不是故障**；但 `_prun.py` 若不透传 --reset，
    #   换参数重挂时 8 个片会全部拒跑、一条不跑就退出，
    #   而日志里只有一句"确实要从头跑请加 --reset"。
    ap.add_argument(
        "--reset",
        action="store_true",
        help="清空本批分片进度从头跑（改了任何进指纹的参数就必须加）",
    )
    args = ap.parse_args()

    if args.shards < 2:
        print("✗ --shards 至少为 2；单片请直接用 _resume.py")
        return 2

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    for a in arms:
        if a not in ("single", "multi"):
            print(f"✗ --arms 里出现未知臂 {a!r}（只支持 single / multi）")
            return 2
    if not arms:
        print("✗ --arms 为空")
        return 2

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    merged = EVAL_DIR / "_progress-merged.jsonl"

    t0 = time.time()
    total = args.shards * len(arms)
    print(
        f"并行 {args.shards} 片 × {len(arms)} 臂 = {total} 个进程："
        f"arms={arms} kind={args.kind} size={args.size} "
        f"customers={args.customers} seed={args.seed} k={args.k}"
    )

    # ── 起 臂×片 个进程 ──────────────────────────────────────
    procs = []
    logs: dict[tuple[str, int], Path] = {}
    for arm in arms:
        for i in range(args.shards):
            db = EVAL_DIR / f"shard-{arm}-{i}.db"
            log = EVAL_DIR / f"_prun-{arm}-shard-{i}.log"
            # ★ 库名与进度名都带 arm：这是「两臂同时跑」的全部依据。
            #   `_resume.py` 会自动在这个 stem 后面追加 -shard{i}of{n}。
            progress = EVAL_DIR / f"_progress-{arm}.jsonl"
            cmd = [
                sys.executable,
                "_resume.py",
                "--shard", f"{i}/{args.shards}",
                "--database", str(db),
                "--progress", str(progress),
                "--arm", arm,
                "--kind", args.kind,
                "--size", str(args.size),
                "--customers", str(args.customers),
                "--seed", str(args.seed),
                "--split", args.split,
                "--k", str(args.k),
            ]
            if args.reset:
                cmd.append("--reset")
            # ★ 每片一个日志文件：并行进程共写一个文件会交错成乱码，
            #   而失败时第一反应就是去看某个分片的最后几十行。
            fh = log.open("w", encoding="utf-8")
            logs[(arm, i)] = log
            p = subprocess.Popen(cmd, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT)
            procs.append((arm, i, p, fh))
            print(f"  起分片 {arm} {i}/{args.shards} → 库 {db.name} / 日志 {log.name}")

    failed: list[str] = []
    for arm, i, p, fh in procs:
        rc = p.wait()
        fh.close()
        if rc != 0:
            failed.append(f"{arm}/{i}")
            print(f"  ✗ 分片 {arm} {i} 退出码 {rc} —— 看 {logs[(arm, i)].name} 尾部")
        else:
            print(f"  ✓ 分片 {arm} {i} 完成")

    if failed:
        print(
            f"\n{len(failed)} 个分片失败（{failed}）。修好后**重跑同一条命令**即续"
            f"（跑完的片会跳过，不会从头跑）。"
        )
        return 1

    # ── 按臂合并进度 + 出正式报表 ───────────────────────────
    for arm in arms:
        shard_files = sorted(EVAL_DIR.glob(f"_progress-{arm}-shard*of*.jsonl"))
        if not shard_files:
            print(f"✗ {arm}: 找不到分片进度文件（glob: _progress-{arm}-shard*of*.jsonl）")
            return 1

        n_lines = 0
        with merged.open("w", encoding="utf-8") as out:
            for f in shard_files:
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        out.write(line + "\n")
                        n_lines += 1
        print(f"\n{arm}: 合并 {len(shard_files)} 个分片进度 → {merged.name}（{n_lines} 行）")

        if args.report:
            report = args.report
        else:
            report = str(EVAL_DIR / f"eval-{arm}-{args.kind}-{args.seed}c.md")

        # ★ 用本臂 shard-0 的库：每个分片都 seed 了同一份完整数据集，
        #   所以任选一片都能还原出完整评测集清单（`build_eval_cases`）。
        cmd = [
            sys.executable,
            "_resume.py",
            "--no-rebuild",
            "--database", str(EVAL_DIR / f"shard-{arm}-0.db"),
            "--progress", str(merged),
            "--arm", arm,
            "--kind", args.kind,
            "--size", str(args.size),
            "--customers", str(args.customers),
            "--seed", str(args.seed),
            "--split", args.split,
            "--k", str(args.k),
            "--report", report,
        ]
        rc = subprocess.call(cmd, cwd=str(ROOT))
        if rc != 0:
            print(f"✗ {arm}: 合并报表步骤失败（退出码 {rc}）")
            return rc

    print(f"\n并行跑批完成，总耗时 {(time.time() - t0) / 60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())
