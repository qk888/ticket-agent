"""核对收紧 `质量` 词典是否误杀了真投诉。

背景：上一版把裸的 `质量|坏|脏|味道` 当质量事由，导致售前句（「哪个质量最好」）
混进语料；收紧后（要求"质量"出现在问题语境里、去掉裸的 坏/脏/味道）质量事由
从 1105 掉到 535。降幅过半，必须确认掉的是**售前句**还是**真投诉**。

做法：把**被闸门判为「无事由」的句子**里凡含可疑瑕疵词（坏/脏/味道/质量）的
都捞出来 —— 这些正是收紧后被踢出语料的那一批。再用两条线索分开数：

- 真投诉的迹象：说的是**自己已经收到的**东西（收到/拆开/里面/发现/宝宝…）
- 售前的迹象：问属性/比价/规则（哪个/哪款/多少钱/好吃吗/保质期…）

两边都命中或都不命中的算"说不清"，单独一栏，不硬塞进任何一边。

★ 闸门函数直接从 `tools/build_corpus_jddc.py` 导入，不在这里重写一份 ——
两份正则一旦漂移，这个核对就失去意义。

用法：python _qualprobe.py
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))

import build_corpus_jddc as B  # noqa: E402

# ★ 只捞被删掉的宽泛词：收紧后含它们、且没有任何其他事由的句子，
#   正是从「质量」桶掉进「无事由」桶的那一批。
SUSPECT = re.compile(r"坏|脏|味道|质量")

COMPLAINT = re.compile(
    r"收到|寄来|发来|打开|拆开|用了|吃了|喝了|宝宝|孩子|里面|发现|竟然|居然|"
    r"这次|上次|今天|昨天|已经|怎么|为什么|太|很|非常|一点|完全|根本|都"
)
PRESALE = re.compile(
    r"哪个|哪款|哪家|多少|怎么卖|能便宜|推荐|有活动|包邮|尺码|颜色|"
    r"好吃吗|好用吗|好喝吗|是正品|真的吗|保质期|有什么区别|划算|选哪个|"
    r"买哪个|可以吗|能吃吗|怎么吃|怎么用|有货"
)


def main() -> int:
    if not B.SRC.exists():
        print(f"找不到原始语料：{B.SRC}")
        return 1

    seen: set[str] = set()
    suspects: list[str] = []
    total = 0

    with B.SRC.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            for m in obj.get("messages") or []:
                if m.get("role") != "user":
                    continue
                text = B.clean(m.get("content"))
                if not text or text in seen:
                    continue
                seen.add(text)
                total += 1
                if B.reject_reason(text) != "无事由":
                    continue
                if SUSPECT.search(text):
                    suspects.append(text)

    print(f"客户话术去重后：{total} 条")
    print(f"被判「无事由」且含可疑瑕疵词的：{len(suspects)} 条\n")

    both = only_c = only_p = neither = 0
    samples: dict[str, list[str]] = {"投诉": [], "售前": [], "都命中": [], "都不命中": []}
    for s in suspects:
        c, p = bool(COMPLAINT.search(s)), bool(PRESALE.search(s))
        if c and p:
            both += 1
            k = "都命中"
        elif c:
            only_c += 1
            k = "投诉"
        elif p:
            only_p += 1
            k = "售前"
        else:
            neither += 1
            k = "都不命中"
        if len(samples[k]) < 12:
            samples[k].append(s)

    n = max(len(suspects), 1)
    print("判定分布：")
    print(f"  疑似真投诉   {only_c:6d}  {only_c / n:6.1%}")
    print(f"  疑似售前     {only_p:6d}  {only_p / n:6.1%}")
    print(f"  两者都像     {both:6d}  {both / n:6.1%}")
    print(f"  都不像       {neither:6d}  {neither / n:6.1%}\n")

    for k in ("投诉", "售前", "都命中", "都不命中"):
        print(f"  【{k}】抽样：")
        for s in samples[k]:
            print(f"    - {s}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
