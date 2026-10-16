"""JDDC 语料接入可行性取证。

回答问题（不猜）：
  1. 文件在不在、多大、多少行；
  2. `messages` 结构长什么样（单轮还是多轮）；
  3. 能抽出多少条客户话术（总数 / 去重数 / 长度分布）；
  4. ★ 其中**售后相关**的剩多少条 —— 本项目的工单全是售后场景，
     售前咨询（"这个有货吗"）进来会与政策 R1-R7 完全对不上；
  5. 与现有 `ecom_aftersale.jsonl` 的 862 条重合吗；
  6. 抽样肉眼验质量。

输出的是数字与样例句，不含结论 —— 结论由人看着这些数字下。
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
CAND = [
    ROOT / "data" / "probe" / "samples" / "ms_ecom_train_FULL.jsonl",
    ROOT / "data" / "probe" / "samples" / "jddc_question_answer_FULL.txt",
    ROOT / "data" / "probe" / "samples" / "gh_jddc_chatglm_FULL.json",
]

# 售后关键词。宁可宽一点（漏掉真售后才是损失），但要能挡住纯售前。
AFTERSALE_RE = re.compile(
    r"退款|退货|退换|退钱|换货|换新|售后|质保|保修|维修|"
    r"坏了|坏掉|破损|瑕疵|划痕|开线|开胶|裂|漏|次品|假货|变质|发霉|过期|"
    r"错发|发错|少发|漏发|多发|发漏|缺件|少件|"
    r"没收到|未收到|没到|不见|丢了|丢件|签收|物流|快递|运单|配送|驿站|"
    r"补偿|赔偿|投诉|差评|维权|发票|价保|保价|退运费|运费"
)
# 售前关键词，用来识别"虽然是售后词但其实是问购"的噪声。
PRESALE_RE = re.compile(r"有货|现货|什么时候发货|尺码|颜色|能不能便宜|优惠|包邮|推荐|哪个好|多少钱")


def _stats(xs: list[int]) -> str:
    if not xs:
        return "n=0"
    xs = sorted(xs)
    q = lambda p: xs[min(len(xs) - 1, int(len(xs) * p))]  # noqa: E731
    return f"n={len(xs)} p50={q(.5)} p90={q(.9)} max={xs[-1]} mean={sum(xs)/len(xs):.1f}"


def probe_ecom() -> dict:
    p = CAND[0]
    if not p.exists():
        print(f"✗ 不存在：{p}")
        return {}
    size_mb = p.stat().st_size / 1024 / 1024
    print(f"=== ms_ecom_train_FULL.jsonl  {size_mb:.1f} MB ===")

    n_lines = 0
    role_counter: Counter[str] = Counter()
    msgs_per_row: Counter[int] = Counter()
    users_all: list[str] = []
    users_after: list[str] = []
    first_row = None

    with p.open(encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            n_lines += 1
            try:
                obj = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if first_row is None:
                first_row = obj
            msgs = obj.get("messages") or []
            msgs_per_row[len(msgs)] += 1
            for m in msgs:
                role = m.get("role", "?")
                role_counter[role] += 1
                if role == "user":
                    t = (m.get("content") or "").strip()
                    if t:
                        users_all.append(t)
                        if AFTERSALE_RE.search(t) and not PRESALE_RE.search(t):
                            users_after.append(t)

    print(f"行数：{n_lines}")
    print(f"role 分布：{dict(role_counter)}")
    print(f"每行 message 条数分布：{dict(sorted(msgs_per_row.items())[:8])}")
    print(f"首行结构：{json.dumps(first_row, ensure_ascii=False)[:300]}")

    u_all = sorted(set(users_all))
    u_after = sorted(set(users_after))
    print()
    print(f"客户话术：总 {len(users_all)} / 去重 {len(u_all)}")
    print(f"  长度 {_stats([len(x) for x in u_all])}")
    print(f"售后候选：总 {len(users_after)} / 去重 {len(u_after)}")
    print(f"  长度 {_stats([len(x) for x in u_after])}")
    if u_all:
        print(f"  售后占比（去重后）：{len(u_after)/len(u_all)*100:.1f}%")

    # 与现有语料的重合度
    old = ROOT / "data" / "raw" / "ecom_aftersale.jsonl"
    if old.exists():
        old_texts = set()
        for ln in old.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                old_texts.add((json.loads(ln).get("user") or "").strip())
            except json.JSONDecodeError:
                pass
        inter = old_texts & set(users_all)
        print(f"\n与 ecom_aftersale.jsonl（{len(old_texts)} 条）完全重合：{len(inter)} 条")

    print("\n--- 售后话术抽样（前 25 条，按长度升序）---")
    for s in sorted(u_after, key=len)[:25]:
        print(f"  [{len(s):>3}] {s}")
    print("\n--- 售后话术抽样（长度 20-60，随机 15 条）---")
    mid = [s for s in u_after if 20 <= len(s) <= 60]
    step = max(1, len(mid) // 15)
    for s in mid[::step][:15]:
        print(f"  [{len(s):>3}] {s}")

    return {
        "rows": n_lines,
        "users_all": len(users_all),
        "users_unique": len(u_all),
        "after_all": len(users_after),
        "after_unique": len(u_after),
    }


def probe_qa() -> None:
    p = CAND[1]
    if not p.exists():
        print(f"\n✗ 不存在：{p}")
        return
    print(f"\n=== jddc_question_answer_FULL.txt  {p.stat().st_size/1024/1024:.1f} MB ===")
    n = 0
    cue: list[str] = []
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not ln.strip():
            continue
        n += 1
        parts = ln.split("\t")
        if parts and parts[0].strip():
            cue.append(parts[0].strip())
    uniq = sorted(set(cue))
    ua = sorted({x for x in uniq if AFTERSALE_RE.search(x) and not PRESALE_RE.search(x)})
    print(f"行数：{n}  客户句去重：{len(uniq)}  售后候选去重：{len(ua)}")
    print(f"售后占比：{len(ua)/max(1,len(uniq))*100:.1f}%")
    print("★ 注意：JDDC 原始 txt 是**空格分词版**，下面样例能直接看出：")
    for s in ua[:10]:
        print(f"  {s}")


if __name__ == "__main__":
    r = probe_ecom()
    print("\n" + "=" * 60)
    probe_qa()
    print("\n" + "=" * 60)
    print("汇总：", json.dumps(r, ensure_ascii=False))
