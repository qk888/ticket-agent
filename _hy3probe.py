import os
from ticket_agent.config import Settings, resolve_endpoint, _profile_for_endpoint

# 同一个地址（DeepSeek 官方），两个不同价的模型
for model in ("deepseek-chat", "deepseek-reasoner"):
    for k in list(os.environ):
        if k.startswith(("LLM_", "OPENAI_", "MODEL_NAME", "CHEAP_MODEL")):
            del os.environ[k]
    os.environ["LLM_BASE_URL"] = "https://api.deepseek.com/v1"
    os.environ["LLM_MODEL"] = model
    ep = resolve_endpoint("")
    print(f"model={model:20s} price={ep.price_in}/{ep.price_out} known={ep.price_known}")
    print(f"    source={ep.source}")

print()
print("地址反查只看地址，不看模型：", repr(_profile_for_endpoint("https://api.deepseek.com/v1")))

# OpenRouter：一个地址挂几百个模型，价差几十倍
print("OpenRouter 地址反查：", repr(_profile_for_endpoint("https://openrouter.ai/api/v1")))
