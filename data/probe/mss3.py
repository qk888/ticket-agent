# -*- coding: utf-8 -*-
import urllib.request, urllib.parse, json, sys, time
sys.stdout.reconfigure(encoding='utf-8')
UA = {'User-Agent': 'Mozilla/5.0'}
KWS = ['客服 数据集', '售后 服务', '工单', '客诉', '投诉 处理', '退货 换货', '物流 查询',
       '订单 咨询', '意图 分类 中文', '槽位 抽取', '对话 客服 语料', '电商 售后',
       '服务 质检', '满意度',
       'customer service zh', 'ticket classification chinese', 'intent chinese ecommerce']
out = {}
for kw in KWS:
    u = 'https://modelscope.cn/api/v1/datasets?Query=%s&PageSize=20&SortBy=Downloads' % urllib.parse.quote(kw)
    try:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30).read().decode('utf-8', 'replace'))
        lst = d.get('Data') or []
        out[kw] = [{'ns': x.get('Namespace'), 'name': x.get('Name'), 'cn': x.get('ChineseName'),
                    'dl': x.get('Downloads'), 'likes': x.get('Likes'),
                    'desc': (x.get('Description') or '')[:200]} for x in lst]
        print('####', kw, len(lst))
        for x in out[kw][:8]:
            print('   %-56s dl=%-6s %s' % (x['ns'] + '/' + str(x['name']), x['dl'], (x['cn'] or x['desc'])[:55].replace('\n', ' ')))
    except Exception as e:
        print('####', kw, 'ERR', repr(e)[:70])
        out[kw] = {'err': repr(e)[:120]}
    time.sleep(0.4)
json.dump(out, open('ms_round3.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('wrote ms_round3.json')
