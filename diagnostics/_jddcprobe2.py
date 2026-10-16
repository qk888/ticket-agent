"""第二轮取证：从**多轮结构**里取客户首句，而不是取任意一轮。

第一轮发现的问题：`ms_ecom_train_FULL.jsonl` 每行只有一对 user/assistant
（是从多轮会话里切出来的），所以它的 user 可能是对话中间轮 ——
抽样里的「哦快递」「嗯快递」「啥快递」就是回复上文的词，脱离上下文
根本不能当工单正文。9,508 条"售后候选"里有多少是这种，必须量出来。

本脚本换两条路：
  A. `gh_jddc_chatglm_FULL.json` —— history 是逐轮累积的对数组，天然多轮，
     可以取每段对话的**第一个** user 发言（客户主动发起）。
  B. `gh_csds_goldrefs_FULL.txt` —— 800 段真实京东客服完整对话（64.1% 售后），
     同样取首句。

同时做长度与质量过滤对照，回答"最终能有多少条可用工单正文"。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
S = ROOT / "data" / "probe" / "samples"

AFTERSALE_RE = re.compile(
    r"退款|退货|退换|退钱|换货|换新|售后|质保|保修|维修|"
    r"坏了|坏掉|破损|瑕疵|划痕|开线|开胶|裂|漏|次品|假货|变质|发霉|过期|"
    r"错发|发错|少发|漏发|多发|发漏|缺件|少件|"
    r"没收到|未收到|没到|不见|丢了|丢件|签收|物流|快递|运单|配送|驿站|"
    r"补偿|赔偿|投诉|差评|维权|发票|价保|保价|退运费|运费"
)
# 客服腔/纯应答/纯语气 —— 这些不是"客户在描述问题"。
FILLER_RE = re.compile(
    r"^(嗯|哦|啊|呃|额|好吧|好的|行|是|对|不|在|有|没|可以|谢谢|麻烦|亲|"
    r"什么|怎么|为什么|哪|多少|几个|多久)[啊呢吧吗么了~！!。.？?，,、\s]*$"
)
# 纯咨询购物（有售后词但问的是买）—— 靠 PRESALE 挡。
PRESALE_RE = re.compile(r"有货|现货|尺码|颜色|优惠|包邮|推荐|哪个好|多少钱|怎么卖|能便宜")


def clean(s: str) -> str:
    """去客服称呼前缀与首尾空白。JDDC 里客户常以「亲」起头。"""
    s = s.strip()
    s = re.sub(r"^(亲[，,、\s]*)+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def is_usable(s: str, *, min_len: int = 8) -> bool:
    if len(s) < min_len:
        return False
    if FILLER_RE.match(s):
        return False
    if not AFTERSALE_RE.search(s):
        return False
    if PRESALE_RE.search(s):
        return False
    # 至少要有点"我在说一件事"的样子：含动词性或含标点
    return True


def stats(xs: list[int]) -> str:
    if not xs:
        return "n=0"
    xs = sorted(xs)
    q = lambda p: xs[min(len(xs) - 1, int(len(xs) * p))]  # noqa: E731
    return f"n={len(xs)} p50={q(.5)} p90={q(.9)} max={xs[-1]} mean={sum(xs)/len(xs):.1f}"


def probe_chatglm() -> None:
    p = S / "gh_jddc_chatglm_FULL.json"
    if not p.exists():
        print(f"✗ 不存在：{p}")
        return
    print(f"=== gh_jddc_chatglm_FULL.json  {p.stat().st_size/1024/1024:.1f} MB ===")
    data = json.loads(p.read_text(encoding="utf-8"))
    print(f"顶层类型：{type(data).__name__}  条数：{len(data)}")
    if isinstance(data, list) and data:
        print(f"首条键：{list(data[0].keys())}")
        print(f"首条 prompt：{str(data[0].get('prompt'))[:120]}")
        h = data[0].get("history")
        print(f"首条 history 长度：{len(h) if isinstance(h, list) else '?'}")
        if isinstance(h, list) and h:
            print(f"history[0]：{json.dumps(h[0], ensure_ascii=False)[:200]}")

    firsts: list[str] = []
    all_u: list[str] = []
    for row in data if isinstance(data, list) else []:
        h = row.get("history") or []
        # history 是 [[user, assistant], ...] 逐轮累积
        if isinstance(h, list):
            for pair in h:
                if isinstance(pair, (list, tuple)) and len(pair) >= 1:
                    t = clean(str(pair[0]))
                    if t:
                        all_u.append(t)
            if h and isinstance(h[0], (list, tuple)) and h[0]:
                firsts.append(clean(str(h[0][0])))
        # prompt 本身也是客户话术
        pr = clean(str(row.get("prompt") or ""))
        if pr:
            all_u.append(pr)

    u_first = sorted({s for s in firsts if s})
    u_all = sorted(set(all_u))
    print(f"\n客户发言（含所有轮）：总 {len(all_u)} / 去重 {len(u_all)}")
    print(f"  ★ 首句（每段对话第一个 user）：总 {len(firsts)} / 去重 {len(u_first)}")
    kept_first = [s for s in u_first if is_usable(s)]
    kept_all = [s for s in u_all if is_usable(s)]
    print(f"  首句 + 售后过滤 + 长度≥8：{len(kept_first)}")
    print(f"  任意轮 + 售后过滤 + 长度≥8：{len(kept_all)}")
    print(f"  长度分布（首句可用）：{stats([len(s) for s in kept_first])}")
    print("\n--- 首句·售后·可用 抽样 20 ---")
    for s in sorted(kept_first, key=len)[:10]:
        print(f"  [{len(s):>3}] {s}")
    mid = sorted(kept_first, key=len)[len(kept_first) // 2:][:10]
    for s in mid:
        print(f"  [{len(s):>3}] {s}")


def probe_csds() -> None:
    p = S / "gh_csds_goldrefs_FULL.txt"
    if not p.exists():
        print(f"\n✗ 不存在：{p}")
        return
    print(f"\n=== gh_csds_goldrefs_FULL.txt  {p.stat().st_size/1024:.0f} KB ===")
    text = p.read_text(encoding="utf-8", errors="ignore")
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    print(f"段落数：{len(blocks)}")
    print(f"首段前 400 字：\n{blocks[0][:400]}")
    # 表头形如 "用户：xxx / 客服：yyy" 或 "Q: / A:"，两种都试
    firsts: list[str] = []
    for b in blocks:
        lines = [ln.strip() for ln in b.splitlines() if ln.strip()]
        for ln in lines:
            m = re.match(r"^(?:用户|客户|顾客|Q|User)[:：]\s*(.+)$", ln)
            if m:
                firsts.append(clean(m.group(1)))
                break
    u = sorted({s for s in firsts if s})
    kept = [s for s in u if is_usable(s)]
    print(f"抽到首句：总 {len(firsts)} / 去重 {len(u)} / 售后可用 {len(kept)}")
    if kept:
        print(f"长度分布：{stats([len(s) for s in kept])}")
        print("--- 抽样 12 ---")
        for s in sorted(kept, key=len)[:6]:
            print(f"  [{len(s):>3}] {s}")
        mid = sorted(kept, key=len)[len(kept) // 2:][:6]
        for s in mid:
            print(f"  [{len(s):>3}] {s}")


def probe_ecom_multiturn() -> None:
    """回答第一轮的核心疑问：ms_ecom 的 user 里有多少是"独立成立"的。"""
    p = S / "ms_ecom_train_FULL.jsonl"
    if not p.exists():
        return
    print(f"\n=== ms_ecom_train_FULL.jsonl：独立成立性量化 ===")
    users: list[str] = []
    with p.open(encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except json.JSONDecodeError:
                continue
            for m in obj.get("messages") or []:
                if m.get("role") == "user":
                    t = clean(m.get("content") or "")
                    if t:
                        users.append(t)
    u = sorted(set(users))
    after = [s for s in u if AFTERSALE_RE.search(s) and not PRESALE_RE.search(s)]
    print(f"全部去重 {len(u)}；售后候选 {len(after)}")
    for ml in (6, 8, 10, 12, 15, 20):
        k = [s for s in after if is_usable(s, min_len=ml)]
        print(f"  长度≥{ml:>2} 且通过填词/售前过滤：{len(k)}")
    kept = [s for s in after if is_usable(s, min_len=10)]
    print(f"\n★ 净可用（≥10 字）：{len(kept)} 条，长度分布 {stats([len(s) for s in kept])}")
    print("--- 抽样 15（净可用）---")
    step = max(1, len(kept) // 15)
    for s in kept[::step][:15]:
        print(f"  [{len(s):>3}] {s}")


def _safe(fn) -> None:
    """单个语料探不动不该拖垮整轮取证 —— 它是 40MB 的下载中文件。"""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        print(f"✗ {fn.__name__} 失败：{type(exc).__name__}: {str(exc)[:160]}")


if __name__ == "__main__":
    _safe(probe_chatglm)
    print("\n" + "=" * 66)
    _safe(probe_csds)
    print("\n" + "=" * 66)
    _safe(probe_ecom_multiturn)
