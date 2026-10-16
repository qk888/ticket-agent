"""验证 `Settings(_env_file=None)` 是否反映到 `model_config` —— 决定缺陷 ⑮ 的修法。

若反映：解析器就能"跟着 cfg 的 env_file 走"，测试里 `Settings(_env_file=None)`
天然看不到 `.env`，隔离不被破坏（现有 133 个测试不必改）。
若不反映：只能另想办法（例如让测试显式传隔离 environ）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ticket_agent.config import PROJECT_ROOT, Settings  # noqa: E402

ENV_PATH = PROJECT_ROOT / ".env"
os.environ.pop("WORKBUDDY_SIDECAR_TOKEN", None)  # 确保不是进程环境给的

print(f"pydantic_settings 版本检查")
import pydantic_settings  # noqa: E402

print(f"  pydantic_settings = {pydantic_settings.VERSION if hasattr(pydantic_settings, 'VERSION') else '?'}")
print()

# 1) 默认构造（读 .env）
s_default = Settings()
print(f"1) Settings()                    model_config['env_file'] = {s_default.model_config.get('env_file')!r}")
print(f"   llm_profile = {s_default.llm_profile!r}")

# 2) _env_file=None（测试用的隔离构造）
s_none = Settings(_env_file=None)
print(f"2) Settings(_env_file=None)      model_config['env_file'] = {s_none.model_config.get('env_file')!r}")
print(f"   llm_profile = {s_none.llm_profile!r}")

# 3) 显式路径
s_path = Settings(_env_file=ENV_PATH)
print(f"3) Settings(_env_file=<path>)    model_config['env_file'] = {s_path.model_config.get('env_file')!r}")
print(f"   llm_profile = {s_path.llm_profile!r}")

print()
print("—— 判据 ——")
ef_default = s_default.model_config.get("env_file")
ef_none = s_none.model_config.get("env_file")
if ef_none is None and ef_default is not None:
    print("✓ `_env_file=None` **确实**反映到 model_config（None）")
    print("  → 解析器可以按 cfg.model_config['env_file'] 决定要不要读 .env，")
    print("    测试用 Settings(_env_file=None) 时自动隔离，133 个测试不必改。")
elif ef_none == ef_default:
    print("✗ `_env_file=None` **没有**反映到 model_config（仍是默认路径）")
    print("  → 解析器无法靠 cfg 判断隔离；需要另找办法（如让测试显式传 environ）。")
else:
    print(f"? 意外组合：default={ef_default!r} none={ef_none!r}")

print()
print("—— .env 里几个关键值（用 dotenv 直读，作为对照）——")
from dotenv import dotenv_values  # noqa: E402

vals = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
for k in ["LLM_PROFILE", "OPENAI_BASE_URL", "MODEL_NAME", "OPENAI_API_KEY",
          "WORKBUDDY_SIDECAR_TOKEN", "CHEAP_MODEL_NAME"]:
    v = vals.get(k)
    shown = (v[:10] + "…") if v and len(v) > 10 else v
    print(f"  {k:<28} = {shown!r}")
