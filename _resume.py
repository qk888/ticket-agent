"""断点续跑的评测跑批（数据集线 T4 的前置条件）。

为什么需要它
------------
千级真模型评测（605 case × k）走 Hy3 约 80s/run，单臂 ≈ 12.6 小时。
用户会断电 —— 而「跑完再存」等于「断电全丢」。

★ 但本项目的 `run_case` 每条开头都会 `_reset_case` → `apply_snapshot`
  把库重置到 `initial_state`（`harness.py:379`）。也就是说**每条 case 是自洽
  且幂等的**：它不依赖上一条在库里留下的任何状态。
  → 所以「环境级快照」（定期备份 .db）在这里是**多余的**。真正需要跨进程
    保存的只有一样东西：「哪些 (case_id, arm, k_index) 已经跑完，以及它们
    的读数」——那部分只活在评测进程的内存里。
  两层因此降成一层：断点续跑 = 一个 append-only 的 jsonl。

代价要说清楚（不假装没有）：
- 断电会丢掉**正在跑的那一条**（约 80s），因为它跑完才写盘。
- 换来的是「续跑结果与一口气跑完**逐位相同**」—— 每条 case 自带重置，
  不存在「上一段留下的状态污染这一段」，所以报表里不需要标注
  「本表由 N 段拼成」。这是比快照方案更强的一个性质，不是更弱的。

用法
----
    # 第一段：跑 150 条（断电也没关系）
    python _resume.py --customers 1000 --size 605 --arm single --k 1 --limit 150

    # 断了以后：同一条命令直接重跑，自动从断点接上
    python _resume.py --customers 1000 --size 605 --arm single --k 1 --limit 150

    # 全部跑完再出正式报表
    python _resume.py --customers 1000 --size 605 --arm single --k 1 --report eval_out/eval-single-1000c.md

分片并行（真模型线提速用）
----
    千级真模型单臂约 12.6 小时，瓶颈是**等模型回**（Hy3 约 3s/turn），
    CPU/磁盘基本闲着。切成 N 片同时跑，墙钟 ≈ 12.6 / N 小时。

    一条 driver 命令（它负责起 N 个进程、合成进度、出正式报表）：

        python _prun.py --shards 4 --arm multi --kind llm \
            --size 605 --customers 1000 --seed 7 --k 3 \
            --report eval_out/eval-multi-llm-1000c.md

    等价的手工形式（**库文件与进度文件都必须每片一份**，原因见那两个参数的注释）：

        for i in 0 1 2 3:
            python _resume.py --shard i/4 --database eval_out/shard-i.db \
                --arm multi --kind llm --size 605 --customers 1000 --seed 7 --k 3

    ★ 并行**不改变单条读数**：`run_case` 每条开头都 `apply_snapshot` 重置
      （`harness.py:379`），每条 case 自洽且幂等，不依赖上一条留下的状态。
      所以 shard 只决定"谁来跑"，**不进** `run_sig` 指纹 —— 加了它反而会让
      "同一分片重跑同一条命令"通不过自己的指纹校验。
    ★ 断电后重跑**同一条** `_prun.py` 命令即续：跑完的片跳过，
      没跑完的片从它自己的进度文件接上。
    ★ Hy3 走本机 sidecar（`tools/wb_sidecar.mjs`，127.0.0.1:8787/v1）。
      N 大了一起打过去可能被插件侧限流：**先从 2 片试**，稳了再加。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVAL_DIR = ROOT / "eval_out"
PROGRESS = EVAL_DIR / "_progress.jsonl"
SUMMARY = EVAL_DIR / "_resume_summary.json"

#: 进度文件格式版本。改了这个文件的结构就 +1，否则旧文件会被当新格式读。
PROGRESS_VERSION = 2


def _shard_paths(args: argparse.Namespace, shard_i: int, shard_n: int) -> tuple[Path, Path]:
    """进度/摘要文件路径：分片并行时每片一份，否则用模块级默认。

    ★ 不改 `run_sig` 的分片维度：指纹描述的是「哪些参数会改变单条读数」，
      shard 只改变**谁来跑**，不改变读数本身。加了它反而会让
      「同一分片重跑同一条命令」通不过自己的指纹校验。
    """
    progress = Path(args.progress) if args.progress else PROGRESS
    summary = EVAL_DIR / "_resume_summary.json"
    if shard_n > 1:
        suffix = f"-shard{shard_i}of{shard_n}"
        progress = progress.with_name(progress.stem + suffix + progress.suffix)
        summary = EVAL_DIR / f"_resume_summary{suffix}.json"
    return progress, summary


def run_sig(a: argparse.Namespace) -> dict:
    """决定「这两批结果能不能互相续」的指纹。

    ★ 必须包含**所有会改变单条读数**的参数。少一项的后果不是「多跑了一点」，
      而是**把两批不可比的读数混进同一张报表**，而报表照常出、看不出问题 ——
      这正是缺陷 ⑨「基准漂移」：漂移的定义不是「数变了」，是「没人知道数变了」。
    """
    return {
        "version": PROGRESS_VERSION,
        "arm": a.arm,
        "kind": a.kind,
        "size": a.size,
        "k": a.k,
        "customers": a.customers,
        "seed": a.seed,
        "split": a.split,
    }


def load_progress(path: Path):
    """读进度文件 → (已完成键 → 读数, 指纹)。

    ★ 单行 JSON 解析失败时**跳过并计数**，不能整体崩掉：断电的瞬间正在写的
      那一行很可能只剩半截。整文件崩掉等于「上一次续跑白干」。
    """
    done: dict = {}
    sig: dict | None = None
    broken = 0
    if not path.exists():
        return done, sig
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                broken += 1
                continue
            if "__sig__" in rec:
                sig = rec["__sig__"]
                continue
            key = (rec.get("case_id", ""), rec.get("arm", ""), int(rec.get("k_index", -1)))
            done[key] = rec
    if broken:
        print(f"⚠ 进度文件里有 {broken} 行残缺（多半是断电时正在写的），已跳过")
    return done, sig


def _emit(
    done: dict,
    args: argparse.Namespace,
    n_cases: int,
    summary_path: Path,
    expected_runs: int | None = None,
) -> int:
    """把累计读数汇总落盘（不是只报本段）。

    ★ 分片并行时每个分片各调一次，所以 `summary_path` 必须是调用方传进来的
      分片路径，不能写死模块级 `SUMMARY` —— 否则 N 个分片会互相覆盖同一份摘要。
    ★ `expected_runs` 分片时应该是**本片**的 run 数（本片 case 数 × k），
      不是全量 case 数 × k：否则每个分片的摘要都会报「缺一大堆」。
    """
    from ticket_agent.eval.harness import CaseResult, accuracy, pass_hat_k, three_rates

    results = [CaseResult(**rec) for rec in done.values()]
    # ★ 分片时 caller 传本片 run 数；没传就按全量推。
    expected = expected_runs if expected_runs is not None else n_cases * args.k
    payload = {
        "arm": args.arm,
        "kind": args.kind,
        "k": args.k,
        "cases_in_eval_set": n_cases,
        "runs_collected": len(results),
        "expected_runs": expected,
        "accuracy": accuracy(results),
        "pass_at_k": pass_hat_k(results, args.k) if args.k > 1 else None,
        "three_rates": three_rates(results),
        "note": "由 _resume.py 跨多次运行累计而成；每条 case 自带重置，故与一口气跑完等价",
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    tr = payload["three_rates"]
    res_txt = "—" if tr["resolution_rate"] is None else format(tr["resolution_rate"], ".1%")
    print(
        f"\n累计 {len(results)}/{payload['expected_runs']} runs | "
        f"准确率 {payload['accuracy']:.4f} | "
        f"Automation {tr['automation_rate']:.1%} / "
        f"Involvement {tr['involvement_rate']:.1%} / Resolution {res_txt}"
    )
    if len(results) < payload["expected_runs"]:
        print(
            f"（还没跑完，缺 {payload['expected_runs'] - len(results)} 条 —— "
            f"重跑同一条命令即可续）"
        )
    print(f"累计读数：{summary_path}")

    if args.report:
        from ticket_agent.eval.report import build_report, render_markdown

        report = build_report(results, k=args.k, meta={"source": "_resume.py"})
        Path(args.report).write_text(render_markdown(report), encoding="utf-8")
        print(f"报表：{args.report}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="可断点续跑的评测跑批")
    ap.add_argument("--arm", default="single", choices=("single", "multi"))
    ap.add_argument("--kind", default="llm", choices=("llm", "scripted"))
    ap.add_argument("--size", type=int, default=605, help="评测集条数上限")
    ap.add_argument("--customers", type=int, default=1000, help="造数客户数")
    ap.add_argument("--seed", type=int, default=11, help="造数与评测集 seed（必须与正式报表一致）")
    ap.add_argument("--split", default="all", choices=("all", "train", "holdout"))
    ap.add_argument("--k", type=int, default=1, help="每条 case 重复次数（pass^k 用）")
    ap.add_argument("--limit", type=int, default=0, help="本次最多新跑多少条（0 = 不限）")
    ap.add_argument("--reset", action="store_true", help="清空进度从头跑")
    ap.add_argument("--report", default="", help="跑完后写一份 markdown 报表到这个路径")
    ap.add_argument("--no-rebuild", action="store_true", help="不重建数据库（库里已有数据时用）")
    # ── 并行分片（由 `_prun.py` 驱动）──────────────────────────────
    # ★ `run_sig` 指纹**不含** shard：同一分片重跑同一条命令即续跑，
    #   而不同分片各有自己的进度文件与库文件，互不干扰。
    ap.add_argument("--progress", default="", help="进度文件路径（默认 eval_out/_progress.jsonl；分片并行时每片一个）")
    ap.add_argument("--shard", default="", help="i/n：只跑第 i 个分片（按 case 序号取模），例如 0/4")
    ap.add_argument("--database", default="", help="SQLite 库文件路径（分片并行时每片一个，避免互相 drop 表）")
    args = ap.parse_args()

    # ── 分片解析（并行跑批用，由 `_prun.py` 驱动）──────────────────
    # ★ 为什么必须每片一个库文件：`init_db(drop=True)` 是**整库 drop**。
    #   N 个进程共用一个库文件时，先跑完的那个会把别人正在用的表删掉，
    #   症状是随机的 `no such table` / 重置失败，而且只在并行时才出现。
    # ★ 为什么每片一个进度文件：进度是 append-only jsonl，混写会串行阻塞，
    #   而指纹校验还会互相拒绝（arm 相同但内容不同）。
    shard_i, shard_n = 0, 1
    if args.shard:
        try:
            _a, _b = args.shard.split("/", 1)
            shard_i, shard_n = int(_a), int(_b)
        except ValueError:
            print(f"✗ --shard 格式应为 i/n（例如 0/4）；收到 {args.shard!r}")
            return 2
        if shard_n < 1 or not (0 <= shard_i < shard_n):
            print(f"✗ --shard 越界：{args.shard!r}（需要 0 <= i < n）")
            return 2

    progress_path, summary_path = _shard_paths(args, shard_i, shard_n)

    # ★ 必须在 import ticket_agent **之前**设置：`settings` 在 import 时
    #   就把 database_url 定下来了，之后再改环境变量无效。
    if args.database:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(args.database).resolve().as_posix()}"

    progress_path.parent.mkdir(parents=True, exist_ok=True)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    sig = run_sig(args)

    if args.reset and progress_path.exists():
        progress_path.unlink()
        print(f"已清空进度（{progress_path.name}），从头跑")

    done, prev_sig = load_progress(progress_path)
    if prev_sig is not None and prev_sig != sig:
        diff = {
            k: (prev_sig.get(k), sig.get(k))
            for k in set(prev_sig) | set(sig)
            if prev_sig.get(k) != sig.get(k)
        }
        print("✗ 进度文件的指纹与本次参数不一致，拒绝续跑：")
        for k, (old, new) in sorted(diff.items()):
            print(f"    {k}: 文件里是 {old!r}，本次要 {new!r}")
        print("  这两批读数不可比，混进同一张报表就是缺陷 ⑨「基准漂移」。")
        print("  确实要从头跑请加 --reset。")
        return 2

    from ticket_agent.db.seed import build_eval_cases, seed_all
    from ticket_agent.db.session import init_db
    from ticket_agent.eval.harness import run_case

    if not args.no_rebuild:
        # ★ drop=True：`init_db` 走 create_all，只补缺表、绝不给已存在的表加列。
        #   模型加过列的库必须重建。库内容全部由 seed 复现，重建无信息损失。
        init_db(drop=True)
        seed_all(n_customers=args.customers, seed=args.seed)
    cases = build_eval_cases(size=args.size, seed=args.seed, split=args.split)
    print(
        f"评测集 {len(cases)} 条（customers={args.customers} seed={args.seed} "
        f"size={args.size} split={args.split}），arm={args.arm} kind={args.kind} k={args.k}"
    )

    # ★ 本片实际分到多少条 case：`expected_runs` 按**本片**算，
    #   否则每个分片的摘要都会报「缺一大堆」——那是分片设计的正常现象，不是没跑完。
    shard_cases = [
        c for idx, c in enumerate(cases) if shard_n == 1 or idx % shard_n == shard_i
    ]
    expected_runs = len(shard_cases) * args.k

    # ★ 按 case 为单位切批，而不是「先跑完所有 case 的 k=0、再跑 k=1」。
    #   否则断电时会留下一堆「同一条 case 只跑了 k=0」的半截结果，
    #   pass^k 的分母被污染却看不出来。
    # ★ shard 过滤用 `enumerate` 的**下标**而不是 case_id：case_id 是字符串，
    #   取模会把分片边界变成哈希函数的形状，不同 seed 下每片条数会不均。
    #   下标取模保证每片条数只差 1，而 `build_eval_cases` 对同一 seed 是确定的。
    todo = [
        (c, k)
        for idx, c in enumerate(cases)
        if shard_n == 1 or idx % shard_n == shard_i
        for k in range(args.k)
        if (c["case_id"], args.arm, k) not in done
    ]
    if args.limit:
        todo = todo[: args.limit]

    print(
        f"已完成 {len(done)} 条，本次待跑 {len(todo)} 条"
        + (f"（分片 {shard_i}/{shard_n}，本片 {len(cases) // shard_n}+ 条 case）" if shard_n > 1 else "")
    )

    if not todo:
        return _emit(done, args, len(cases), summary_path, expected_runs)

    fresh = progress_path.open("a", encoding="utf-8")
    if prev_sig is None:
        fresh.write(json.dumps({"__sig__": sig}, ensure_ascii=False) + "\n")
        fresh.flush()

    print()
    t0 = time.time()
    for i, (case, k_index) in enumerate(todo, 1):
        r1 = time.time()
        res = run_case(case, arm=args.arm, kind=args.kind, k_index=k_index)
        dt = time.time() - r1
        rec = dataclasses.asdict(res)
        fresh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        # ★ 每条 flush：断电最多丢当前这一条。
        fresh.flush()
        done[(case["case_id"], args.arm, k_index)] = rec

        mark = "OK " if res.passed else "BAD"
        extra = f" err={res.error!r}" if res.error else ""
        print(
            f"[{i}/{len(todo)}] {mark} {case['case_id']:<14} {case['condition']:<24} "
            f"{dt:6.1f}s turns={res.turns} tools={res.tool_calls} "
            f"cost={res.cost_cny:.6f} action={res.observed_action}{extra}",
            flush=True,
        )

    fresh.close()
    print(f"\n本段耗时 {(time.time() - t0) / 60:.1f} 分钟")
    return _emit(done, args, len(cases), summary_path, expected_runs)


if __name__ == "__main__":
    sys.exit(main())