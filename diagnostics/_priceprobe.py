import os, importlib
for k in list(os.environ):
    if k.startswith(("LLM_","OPENAI_","MODEL_NAME","PRICE_","CHEAP_MODEL","HUNYUAN_","RELAY_","DEEPSEEK_")):
        del os.environ[k]
import ticket_agent.config as C
def show(tag):
    importlib.reload(C)
    ep = C.resolve_endpoint()
    print(f"{tag:38s} base={ep.base_url:42s} model={ep.model:18s} price={ep.price_in}/{ep.price_out} known={ep.price_known}")
show("1 老路径默认（deepseek 地址+模型）")
os.environ["OPENAI_BASE_URL"]="http://localhost:11434/v1"; os.environ["MODEL_NAME"]="qwen2.5:7b"
show("2 老路径指向 ollama（成本 0）")
os.environ["LLM_PROFILE"]="ollama"
show("3 同一端点、改用 ollama 档案")
del os.environ["LLM_PROFILE"]
os.environ["OPENAI_BASE_URL"]="https://api.hunyuan.cloud.tencent.com/v1"; os.environ["MODEL_NAME"]="hunyuan-a13b"
show("4 老路径指向混元（入 0.0005/出 0.002）")
os.environ["LLM_PRICE_IN_PER_1K_CNY"]="0.0005"; os.environ["LLM_PRICE_OUT_PER_1K_CNY"]="0.002"
show("5 同上 + 显式声明单价")
