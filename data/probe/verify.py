#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""核对最终清单里每个下载 URL 是否真的可下（Range: bytes=0-0 拿 Content-Range 总长）。

用法: python verify.py
逐行打印: OK/FAIL  <总字节>  <url>
"""
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

UA = {"User-Agent": "Mozilla/5.0"}

URLS = [
    # ---- ModelScope ----
    ("MS 电商客服_单轮对话 train(去分词版JDDC)",
     "https://modelscope.cn/api/v1/datasets/modelscope_mp_677764216/E_commerce_Customer_Service/repo?Revision=master&FilePath=train_clean_v2_small.jsonl"),
    ("MS 电商客服 dev", "https://modelscope.cn/api/v1/datasets/modelscope_mp_677764216/E_commerce_Customer_Service/repo?Revision=master&FilePath=dev_clean_v2.jsonl"),
    ("MS 电商客服 test", "https://modelscope.cn/api/v1/datasets/modelscope_mp_677764216/E_commerce_Customer_Service/repo?Revision=master&FilePath=test_clean_v2.jsonl"),
    ("MS customer_json ecom 200m", "https://modelscope.cn/api/v1/datasets/zhouyang55666/customer_json/repo?Revision=master&FilePath=data/ecom/ecom_v2_200m.jsonl"),
    ("MS customer_json 多行业", "https://modelscope.cn/api/v1/datasets/zhouyang55666/customer_json/repo?Revision=master&FilePath=data/multi_industry/supplement_v2.jsonl"),
    ("MS customer_json 标签体系", "https://modelscope.cn/api/v1/datasets/zhouyang55666/customer_json/repo?Revision=master&FilePath=metadata/label_catalogue.json"),
    ("MS 工单决策教程 test.jsonl", "https://modelscope.cn/api/v1/datasets/icebergyang/decision-model-tutorial/repo?Revision=master&FilePath=data/test.jsonl"),
    ("MS 工单决策教程 calibration.jsonl", "https://modelscope.cn/api/v1/datasets/icebergyang/decision-model-tutorial/repo?Revision=master&FilePath=data/calibration.jsonl"),
    ("MS 电商售后维权合成 997", "https://modelscope.cn/api/v1/datasets/QingshanAI/ecom-after-sale-synthetic/repo?Revision=master&FilePath=data.jsonl"),
    ("MS 电商物流合成 987", "https://modelscope.cn/api/v1/datasets/QingshanAI/ecom-logistics-synthetic/repo?Revision=master&FilePath=data.jsonl"),
    ("MS 电商客服合成 992", "https://modelscope.cn/api/v1/datasets/QingshanAI/ecom-customer-service-synthetic/repo?Revision=master&FilePath=data.jsonl"),
    ("MS WWW2025+ 真实意图 17442", "https://modelscope.cn/api/v1/datasets/smau0441/Intent_Recognition_and_Image_Classification_in_E-commerce_Scenarios/repo?Revision=master&FilePath=all.json"),
    ("MS ZhDoc2Bot 文档驱动(医疗)", "https://modelscope.cn/api/v1/datasets/DAMO_ConvAI/ZhDoc2BotDialogue/repo?Revision=master&FilePath=dialog_zh_train.csv"),
    ("MS 天猫对话测试集", "https://modelscope.cn/api/v1/datasets/newhwa/dialog_test_tmall/repo?Revision=master&FilePath=train.csv"),
    # ---- HF 镜像 ----
    ("HF 繁中工单 train", "https://hf-mirror.com/datasets/albertkingdom/custom-service-ticket-zh-tw/resolve/main/train.jsonl"),
    ("HF 繁中工单 validation", "https://hf-mirror.com/datasets/albertkingdom/custom-service-ticket-zh-tw/resolve/main/validation.jsonl"),
    ("HF 繁中工单 test", "https://hf-mirror.com/datasets/albertkingdom/custom-service-ticket-zh-tw/resolve/main/test.jsonl"),
    ("HF 中文对话合辑 kdconv", "https://hf-mirror.com/datasets/TingchenFu/ChineseDialogue/resolve/main/kdconv.jsonl"),
    ("HF CrossWOZ data.zip", "https://hf-mirror.com/datasets/GEM/CrossWOZ/resolve/main/data.zip"),
    ("HF kdconv train(film)", "https://hf-mirror.com/datasets/thu-coai/kdconv/resolve/main/film/train.json"),
    # ---- GitHub（jsdelivr / ghapi）----
    ("GH CSDS 真实对话全文", "https://cdn.jsdelivr.net/gh/xiaolinAndy/CSDS@master/results/final/ext_gold_refs.txt"),
    ("GH CSDS 摘要(真实答案)", "https://cdn.jsdelivr.net/gh/xiaolinAndy/CSDS@master/results/final/fast_rl_mod_true_preds.txt"),
    ("GH CSDS 摘要(模型生成)", "https://cdn.jsdelivr.net/gh/xiaolinAndy/CSDS@master/results/final/fast_rl_mod_preds.txt"),
    ("GH CSDS 用户摘要", "https://cdn.jsdelivr.net/gh/xiaolinAndy/CSDS@master/results/final/bert_rl_block5_refs.txt"),
    ("GH JDDC QA 单轮", "https://cdn.jsdelivr.net/gh/Dikea/Dialog-System-with-Task-Retrieval-and-Seq2seq@master/search_dialog/data/question_answer.txt"),
    ("GH JDDC 多轮", "https://cdn.jsdelivr.net/gh/Dikea/Dialog-System-with-Task-Retrieval-and-Seq2seq@master/seq2seq_dialog/data/multi_train.txt"),
    ("GH JDDC 单轮", "https://cdn.jsdelivr.net/gh/Dikea/Dialog-System-with-Task-Retrieval-and-Seq2seq@master/seq2seq_dialog/data/single_train.txt"),
    ("GH JDDC 上下文", "https://cdn.jsdelivr.net/gh/Dikea/Dialog-System-with-Task-Retrieval-and-Seq2seq@master/search_dialog/data/context_response.txt"),
    ("GH JDDC chatglm 版", "https://cdn.jsdelivr.net/gh/zhangbo2008/JDDC_for_train_gpt_data@main/extract_train.json"),
    ("GH 中文歧义澄清对话", "https://cdn.jsdelivr.net/gh/ygan/Chinese-Ambiguous-Reference@main/All%20Conversation.json"),
    ("GH codeload 整仓 tar（备用通道·不限流）",
     "https://codeload.github.com/zhangbo2008/JDDC_for_train_gpt_data/tar.gz/refs/heads/main"),
]

# 含空格路径需 quote 的，已在上面手工 quote；这里仅对 GitHub 明文路径做一次保护
for name, url in URLS:
    try:
        req = urllib.request.Request(url, headers=dict(UA, **{"Range": "bytes=0-0"}))
        r = urllib.request.urlopen(req, timeout=90)
        cr = r.headers.get("Content-Range") or ""
        total = cr.split("/")[-1] if "/" in cr else r.headers.get("Content-Length")
        r.read()
        print(f"OK   {str(total):>12}  {name}\n     {url}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {'':>12}  {name}\n     {url}\n     {e!r}", flush=True)
