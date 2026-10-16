"""逐句追踪 `classify_request` 的判定路径。

`_gapdiag.py` 说"price_protect 0 条"，`_corpusdiag.py` 说"语料里有价保话术"。
两者都对，所以问题在**分类器的顺序**：哪些话术被更早的规则截走了。

这个脚本把每一句话依次过一遍各条规则，打印"谁先命中"。
"""

from __future__ import annotations

from ticket_agent.db import corpus

#: 直接取语料里的真实句子（从上一个脚本的输出里抄的）+ 几个边界样本
SAMPLES = [
    "最近有活动的话可不可以退差价",
    "买了请问怎么返差价呀",
    "我这才买几天降价送的也没送",
    "请问18号有满300减60的活动我买了500的还没收货是不是不能退差价只能参加活动",
    "能退了重新拍吗要6个就差不多了然后你要送5个吧我拍了14个能退我4元差价吗",
    "1块钱的差价在哪里拍",
    # 边界：这句话同时含"退货"和"降价"，应当是退货优先（客户主诉是退）
    "我要退货，但是降价了很生气",
    # 纯价保
    "刚买就降价了，能价保吗",
    "保价怎么弄",
    # 赔付
    "俩盒40你才给我补偿5块",
    "3元你打发要饭的吧算了我就直接给你们个差评了就这种服务态度我花了20就买这个你就退我3元",
    "商品碎了，要求赔偿30元",
    # 改址
    "地址写错了，帮我改一下收货地址",
]

print(f"{'判定请求':<12} {'判定原因':<10} {'抽到的金额':>10}  原话")
print("-" * 100)
for s in SAMPLES:
    kind = corpus.classify_request(s)
    cause = corpus.classify_cause(s, kind)
    amount = corpus.extract_amount(s)
    print(f"{kind:<12} {cause or '-':<10} {amount:>10.2f}  {s[:60]}")

print()
print("对照：corpus 里各正则的命中顺序（_REQUEST_HINT_PATTERNS 在退换货正则之后）")
for name in ("_REQUEST_HINT_PATTERNS", "_EXCHANGE_RE", "_RETURN_RE", "_REQUEST_RE"):
    obj = getattr(corpus, name, None)
    if obj is None:
        print(f"   {name}: <不存在>")
        continue
    if isinstance(obj, (list, tuple)) and obj and isinstance(obj[0], tuple):
        print(f"   {name} 顺序：{[k for k, _ in obj]}")
    else:
        print(f"   {name}: {obj.pattern[:80]}")
