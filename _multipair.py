"""量：新增的 4,681 条里，有多少能配上**正确**的客服回复。

★ 为什么要量这个：

  `jddc_multi_train_FULL.txt` 每行的形状是

      第一句<s>第二句<s>...<s>最后一句 \t 客服回复

  而客服回复对应的是**最后一句**，不是第一句。实测印证：
  `我拍了一组我在拍一组可以嘛<s>那就等你们处理喽` → `好的亲退了` ——
  回复的是"等你们处理"，不是"能不能再拍一组"。

  所以从多轮行抽"第一句"当工单正文时，同行那个 `bot` 字段**是错位的**。

★ 为什么错位仍可接受、以及有没有更好的选择：

  `bot` 字段全项目**只有写入、没有读取**（`grep '"bot"'` 只命中
  `tools/build_corpus_jddc.py:211` 一处写入）。运行时完全不消费它，
  它只在我做"漏判率"弱标签统计时被用过。

  但"没人读"不等于"可以随便填" —— 一个填错的字段迟早会被当成对的用。
  本脚本量出三条出路各自的规模，由数字决定选哪条：

    甲 只收**单轮行**（没有 `<s>` 的），回复天然配对 —— 保守，但条数少
    乙 收多轮行的第一句，`bot` 留空 —— 正文最多，代价是弱标签统计的样本变小
    丙 收多轮行的第一句，去 `ms_ecom_train_FULL.jsonl` 里按正文回查回复 ——
       只有当那句话恰好也被上游切成过单轮、且它在单轮源里是**首句**时才对得上
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
MULTI = ROOT / "data" / "probe" / "samples" / "jddc_multi_train_FULL.txt"
SINGLE = ROOT / "data" / "probe" / "samples" / "ms_ecom_train_FULL.jsonl"

# 直接复用质量脚本的闸门，保证与它量的口径一致
sys.path.insert(0, str(ROOT))
from _multiquality import keep  # noqa: E402


def main() -> int:
    # 单轮源：正文 → 回复（同一句可能出现在多处，取首条）
    single_map: dict[str, str] = {}
    with SINGLE.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            user = reply = ""
            for m in row.get("messages") or []:
                if m.get("role") == "user":
                    user = (m.get("content") or "").strip()
                elif m.get("role") == "assistant":
                    reply = (m.get("content") or "").strip()
            if user and user not in single_map:
                single_map[user] = reply

    # 多轮源：逐行取第一句，记录该行是不是多轮、回复是什么
    rows: list[tuple[str, str, bool]] = []  # (first_turn, reply, is_single_turn)
    with MULTI.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            left, reply = parts[0], parts[1].replace(" ", "").strip()
            is_single = "<s>" not in left
            first = left.split("<s>")[0].replace(" ", "").strip()
            if first:
                rows.append((first, reply, is_single))

    single_keys = set(single_map)
    seen: set[str] = set()
    buckets = {"single_turn": 0, "multi_reply_found": 0, "multi_reply_absent": 0}
    examples_found: list[tuple[str, str]] = []
    for first, reply, is_single in rows:
        if first in single_keys or first in seen or not keep(first):
            continue
        seen.add(first)
        if is_single:
            buckets["single_turn"] += 1
        elif first in single_map:
            buckets["multi_reply_found"] += 1
            if len(examples_found) < 6:
                examples_found.append((first, single_map[first]))
        else:
            buckets["multi_reply_absent"] += 1

    total = sum(buckets.values())
    print("=" * 78)
    print("新增可用句的回复配对情况")
    print("=" * 78)
    print(f"\n新增可用句合计: {total:,}\n")
    print(f"  甲  单轮行，回复天然配对      : {buckets['single_turn']:>6,} "
          f"({buckets['single_turn'] / total:5.1%})")
    print(f"  丙  多轮行，能在单轮源回查到   : {buckets['multi_reply_found']:>6,} "
          f"({buckets['multi_reply_found'] / total:5.1%})")
    print(f"  乙  多轮行，回查不到（需留空） : {buckets['multi_reply_absent']:>6,} "
          f"({buckets['multi_reply_absent'] / total:5.1%})")
    print(f"\n★ 能配上回复（甲+丙）: {buckets['single_turn'] + buckets['multi_reply_found']:,} "
          f"({(buckets['single_turn'] + buckets['multi_reply_found']) / total:.1%})")

    if examples_found:
        print("\n丙 档抽样（正文 → 从单轮源回查到的回复）：")
        for body, rep in examples_found:
            print(f"  · {body[:50]}")
            print(f"      → {rep[:56]}")

    print("\n★ 回查到的回复是否与正文语义对得上，需要人眼判断 —— 上面这几条就是判据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
