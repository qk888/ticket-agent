"""取证：`.env` 里写的 key 到底哪些能生效（缺陷 ⑮ —— 已修，本脚本现在兼作回归检查）。

动机：`.env.example` 把 `HUNYUAN_API_KEY` / `RELAY_API_KEY` / `DEEPSEEK_API_KEY` /
`OPENAI_OFFICIAL_API_KEY` 列为"key 的第二种放法"，还给 `hy3` 加了
`WORKBUDDY_SIDECAR_TOKEN`。但 `Settings` 是 `BaseSettings(extra="ignore")`，
它**只会把 .env 里认得的键**读进字段，不认得的直接丢掉 —— 更关键的是，
它**从不写 `os.environ`**。而 `_resolve_endpoint` 的 `env_get` 读的是 `os.environ`。
两条路各走各的，于是这些 key 放在 .env 里就消失了。

★ 缺陷 ⑮ 的修法**没有**去写 `os.environ`（那会污染进程、破坏测试隔离），
而是在解析器里垫了一层"进程环境优先、`.env` 兜底"的视图，由 `Settings`
实例自己记住"这次构造读没读 `.env`"。所以下面那张三列表里
`os.environ` 和 `Settings认` 两列**仍然是 False**（它们是机制说明，不是判据）；
真正的判据是最后那段"实际后果" —— 解析器必须把 key 读出来，且 `source` 里
要出现"（来自 .env）"。若哪天这两列的变化让人以为"没修"，看 `source` 即可。

本脚本不发任何网络请求，只比较三个视角看到的东西。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ticket_agent.config import (  # noqa: E402
    ENDPOINT_PROFILES,
    PROJECT_ROOT,
    Settings,
    resolve_endpoint,
)

ENV_PATH = PROJECT_ROOT / ".env"

# 收集所有档案用到的 key_env（这就是"档案专用变量"的全集）
PROFILE_KEYS = sorted(
    {
        str(p.get("key_env") or "")
        for p in ENDPOINT_PROFILES.values()
        if str(p.get("key_env") or "")
    }
)

print("=" * 72)
print("缺陷 ⑮ 取证：.env 里的 key 能不能到 env_get")
print("=" * 72)
print(f".env 路径      {ENV_PATH}")
print(f"存在           {ENV_PATH.exists()}")
if ENV_PATH.exists():
    text = ENV_PATH.read_text(encoding="utf-8")
    print(f"大小           {len(text)} 字符")
    # 只看"未被注释掉"的赋值行
    live = [
        ln.split("=", 1)[0].strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#") and "=" in ln
    ]
    print(f"生效的键（未注释）  {sorted(set(live))}")

print()
print("── 档案专用 key 变量全集 ──")
print(PROFILE_KEYS)

print()
print("── 三个视角 ──")
print(f"{'变量名':<28} {'.env里写了':<11} {'os.environ':<11} {'Settings认':<11}")
print("-" * 72)

# 干净地构造 Settings（不带 .env，避免把开发机状态混进来），
# 另一个带 .env 的实例用来对比"pydantic 到底读进去了什么"。
fresh_noenv = Settings(_env_file=None)
fresh_env = Settings(_env_file=ENV_PATH) if ENV_PATH.exists() else None

fields = set(Settings.model_fields)

rows = []
for key in PROFILE_KEYS:
    lower = key.lower()
    in_env_os = key in os.environ
    # .env 文件里有没有这一行（未注释）
    in_file = False
    if ENV_PATH.exists():
        for ln in ENV_PATH.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip() == key:
                in_file = True
                break
    # pydantic 认得它吗：字段名匹配才行
    declared = lower in fields
    rows.append((key, in_file, in_env_os, declared))
    print(f"{key:<28} {str(in_file):<11} {str(in_env_os):<11} {str(declared):<11}")

print()
print("── 老路径 key 的对照组（这些应该是 True/True）──")
for key in ["OPENAI_API_KEY", "LLM_API_KEY"]:
    lower = key.lower()
    declared = lower in fields
    print(f"{key:<28} {'-':<11} {str(key in os.environ):<11} {str(declared):<11}")

print()
print("=" * 72)
print("实际后果：用档案解析器看 value 从哪来")
print("=" * 72)
for prof in ["hunyuan", "hy3", "deepseek"]:
    key_env = str(ENDPOINT_PROFILES[prof].get("key_env") or "")
    ep = resolve_endpoint(prof)
    # Endpoint 的字段名是 api_key；has_api_key 只出现在 redacted() 的字典里
    print(
        f"档案 {prof:<10} key_env={key_env:<28} "
        f"has_api_key={bool(ep.api_key)!s:<6} source={ep.source}"
    )

print()
print("—— 关键判据（缺陷 ⑮ 修复后：兜底层必须把 .env 里的 key 送到解析器）——")
# ★ 旧版判据只看"os.environ / Settings认"两列，于是修复之后它仍然打印
#   "两条路都到不了"—— 因为它量的是**机制**（谁写进了哪个映射），
#   而修复改的是**结果**（解析器最终能不能拿到值）。判据必须量结果。
unreachable = []
for prof in [p for p in ("hunyuan", "hy3", "deepseek", "relay", "openai") if p in ENDPOINT_PROFILES]:
    key_env = str(ENDPOINT_PROFILES[prof].get("key_env") or "")
    if not key_env:
        continue
    wrote_in_file = any(r[0] == key_env and r[1] for r in rows)
    if not wrote_in_file:
        continue  # .env 里没写这个变量 → 无从判断，跳过（见下面那行提醒）
    ep = resolve_endpoint(prof)
    if not ep.api_key:
        unreachable.append((prof, key_env))
    else:
        via = "（来自 .env）" if "来自 .env" in ep.source else ""
        print(f"  ✓ {key_env:<28} → 档案 {prof} 拿到了 key{via}")

if unreachable:
    print(f"★ 写在 .env 里但解析器仍拿不到的变量：{[k for _, k in unreachable]}")
    print("  修复应当让这里为空。若不为空，检查 _resolve_endpoint 里的兜底层：")
    print("  ① Settings.__init__ 有没有记录 _env_file（_dotenv_fallback）；")
    print("  ② 兜底层是不是被进程环境里已有的非空值挡住了（那是正确行为）；")
    print("  ③ 是不是 Settings(_env_file=None) 造的实例（那是隔离，本就该没有兜底）。")
elif not any(in_file for _, in_file, _, _ in rows):
    print("（.env 里没写任何档案专用 key —— 判据无从触发。）")
    print("  想验证修复：往 .env 写一行 HUNYUAN_API_KEY=sk-test 再跑本脚本。")
else:
    print("✓ 全部到位：写在 .env 里的档案专用 key 都能被解析器读到。")
