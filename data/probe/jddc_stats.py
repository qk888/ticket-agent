# -*- coding: utf-8 -*-
"""JDDC 全量 question_answer.txt 统计分析：条数、去重、长度分布、场景占比。"""
import sys, os, re, json, collections
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))

P = os.path.join(HERE, "samples", "jddc_question_answer_FULL.txt")

def load(p):
    b = open(p, "rb").read()
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return b.decode(enc), enc
        except Exception as e:
            err = e
    return b.decode("utf-8", "replace"), "replace"

# 场景关键词（用于判断售前 vs 售后）
SCENES = {
    "物流/发货": ["发货", "快递", "物流", "什么时候到", "几天到", "单号", "派送", "签收", "包邮", "运费", "邮费"],
    "退换货":   ["退货", "退款", "换货", "退换", "退钱", "不要了", "申请退", "退不了", "七天", "无理由"],
    "售后质量": ["坏", "破损", "漏", "少发", "发错", "缺", "质量", "假货", "瑕疵", "色差", "不合适", "尺码", "大小"],
    "投诉不满": ["投诉", "差评", "举报", "骗", "态度", "不满", "太过分", "垃圾", "骗子", "虚假"],
    "价格优惠": ["优惠", "便宜", "降价", "折扣", "券", "包邮吗", "少点", "砍价", "多少钱", "价格", "贵"],
    "商品咨询": ["什么材质", "成分", "规格", "型号", "尺寸", "颜色", "有货", "库存", "保质期", "怎么用", "怎么吃", "好不好"],
    "发票/订单": ["发票", "订单", "改价", "备注", "地址", "下单", "支付", "付款", "取消"],
    "招呼/闲聊": ["在吗", "你好", "客服", "亲", "谢谢", "在么", "有人吗"],
}

def scen(t):
    hits = [k for k, kws in SCENES.items() if any(w in t for w in kws)]
    return hits[0] if hits else "其它"

def main():
    t, enc = load(P)
    lines = [l for l in t.split("\n") if l.strip()]
    print("编码:", enc, "总行数:", len(lines))
    qs, as_, bad = [], [], 0
    for l in lines:
        parts = l.split("\t")
        if len(parts) < 2:
            bad += 1
            continue
        qs.append(parts[0].strip())
        as_.append("\t".join(parts[1:]).strip())
    print("成功切分:", len(qs), " 坏行:", bad)

    # 客户话术里可能含 <s> 多轮
    has_multi = sum(1 for q in qs if "<s>" in q)
    print("客户侧含 <s> 多轮分隔的条数:", has_multi, "(%.1f%%)" % (100.0 * has_multi / max(1, len(qs))))

    # 拆出每一个客户单句（最后一句 = 触发该回复的当前诉求）
    last_utt, all_utt = [], []
    for q in qs:
        segs = [s.strip() for s in q.split("<s>") if s.strip()]
        if segs:
            last_utt.append(segs[-1])
        all_utt.extend(segs)
    print("客户单句总数(含历史轮):", len(all_utt))
    print("客户末句总数(=核心诉求):", len(last_utt))

    uq_last = set(last_utt)
    uq_all = set(all_utt)
    print("去重后 末句:", len(uq_last))
    print("去重后 全句:", len(uq_all))

    # 长度分布（按去重末句，字符数，去掉空格）
    lens = sorted(len(re.sub(r"\s+", "", x)) for x in uq_last)
    def pct(p):
        return lens[min(len(lens) - 1, int(len(lens) * p))] if lens else 0
    print("长度(去空格字符数) 去重末句: min=%d p25=%d p50=%d p75=%d p90=%d max=%d" %
          (lens[0] if lens else 0, pct(.25), pct(.5), pct(.75), pct(.9), lens[-1] if lens else 0))

    # 场景分布（对去重末句）
    cnt_all = collections.Counter(scen(x) for x in uq_last)
    print("\n=== 去重末句场景分布 (粗规则) ===")
    tot = sum(cnt_all.values())
    for k, v in cnt_all.most_common():
        print("  %-10s %6d  %5.1f%%" % (k, v, 100.0 * v / tot))

    # 售后 = 退换货 + 售后质量 + 投诉不满 + 物流/发货
    after = sum(v for k, v in cnt_all.items() if k in ("退换货", "售后质量", "投诉不满", "物流/发货"))
    print("  >> 售后类合计: %d / %d = %.1f%%" % (after, tot, 100.0 * after / tot))

    # 前 20 条真实样例
    print("\n=== 去重末句 前 20 条原文 ===")
    for i, x in enumerate(list(dict.fromkeys(last_utt))[:20]):
        print("  [%02d] %s" % (i, x))

    # 售后类里挑 15 条
    print("\n=== 售后类(退换货/质量/投诉) 样例 15 条 ===")
    n = 0
    for x in dict.fromkeys(last_utt):
        s = scen(x)
        if s in ("退换货", "售后质量", "投诉不满"):
            print("  [%02d][%s] %s" % (n, s, x))
            n += 1
            if n >= 15:
                break

    # 答案侧长度
    alens = sorted(len(re.sub(r"\s+", "", a)) for a in set(as_))
    print("\n客服回复条数(含重复):", len(as_), " 去重:", len(set(as_)),
          " 中位长度:", alens[len(alens) // 2] if alens else 0)

    with open(os.path.join(HERE, "jddc_stats.json"), "w", encoding="utf-8") as f:
        json.dump({
            "encoding": enc, "lines": len(lines), "pairs": len(qs),
            "q_with_multiturn": has_multi, "all_utts": len(all_utt),
            "last_utts": len(last_utt), "uniq_last": len(uq_last), "uniq_all": len(uq_all),
            "len_p50": pct(.5), "len_p90": pct(.9),
            "scene_dist": dict(cnt_all), "aftersale_total": after,
            "answers_uniq": len(set(as_)),
        }, f, ensure_ascii=False, indent=1)
    print("\nwrote jddc_stats.json")

if __name__ == "__main__":
    main()
