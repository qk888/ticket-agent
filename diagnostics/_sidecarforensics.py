r"""sidecar 起不来的完整取证（缺陷 ㉞ 前置，纯只读，不改任何文件）。

结论先行：**本机不存在这五个 peer 包的真实副本**，因此 sidecar 无法在本机恢复；
但这不阻塞真模型读数 —— 有一条不依赖插件的路径（见文件尾）。

事实（全部实测，非推断）：

  A. `dsh-workbuddy-connect` v0.7.1 的 node_modules/@deepseek-ai/ 下五个目录
     全是**空目录**（0 files、无 package.json）：
       dsh-atomic-write / dsh-home-paths / dsh-llm / dsh-llm-pi-ai / schemastery
     @earendil-works/pi-ai 也空。
  B. 全盘（.dsh / AppData\Roaming\npm / AppData\Local / Program Files）按
     "有 package.json 的目录"搜这五个包 → **0 命中**。
  C. app.asar.unpacked 里只有 dsh-desktop-host / dsh-session-log-export /
     libreoffice-kit 三个 @deepseek-ai 包，**没有** dsh-atomic-write 等。
  D. `dsh-connect-workbuddy` v4.0.0 与 v0.7.1 是**两个不同的包**，前者没有
     node_modules，且它的 index.js 同样 import 这五个包 —— 换过去没用。
  E. 顶层 node_modules/@deepseek-ai/ 只有 cosmokit 与 schemastery 是真的。

→ 插件作者把内部包打进 node_modules 时只留了目录名，没有内容。
   这解释了为什么 `wb_sidecar.mjs` 报
   `Cannot find package '@deepseek-ai/dsh-home-paths' imported from ...`
   也解释了为什么 stub 掉 `dsh-atomic-write` 之后下一个报错就是
   `Cannot find package '@deepseek-ai/dsh-home-paths'` —— 五个全是空的，
   一个个 stub 是**无底洞**，而且 `dsh-llm-pi-ai` 里的 `PiAiAdapter` 是
   真推理适配器，stub 它等于伪造推理路径（这正是缺陷 ⑫「护栏守住钱没守住谎」
   的形状：读数还在，但它不再代表任何真实模型）。

不依赖插件的替代路径（三选一，按对项目的可信度排序）：
  1. **DeepSeek 官方 API**（`config.py` 里已有 deepseek 档案）——
     环境变量 `DEEPSEEK_API_KEY` 已在机器上（本会话已验证过 16 个模型 +
     tool_calls 可用）。改 `.env` 的 `LLM_PROFILE=deepseek` 即可跑真模型。
     这是**最该用的**：它不依赖任何插件，价格可查，`price_in/out` 是官方标价
     而非「实测为 0」的促销值。
  2. 混元档案（`hunyuan`）—— 同理，需要 `HUNYUAN_API_KEY`。
  3. 本机 Ollama —— `price_scope="address"`，边际成本恒 0，但模型能力与
     Hy3 不可比，只能做「流程能跑通」的冒烟，不能出能力读数。
"""
from __future__ import annotations

from pathlib import Path

BASE = Path(r"C:\Users\Administrator\.dsh\profiles\desktop\node_modules")
NEEDED = (
    ("@deepseek-ai", "dsh-atomic-write"),
    ("@deepseek-ai", "dsh-home-paths"),
    ("@deepseek-ai", "dsh-llm"),
    ("@deepseek-ai", "dsh-llm-pi-ai"),
    ("@deepseek-ai", "schemastery"),
    ("@earendil-works", "pi-ai"),
)


def main() -> int:
    print("=== A. 五个 peer 包的实际状态 ===")
    all_empty = True
    for scope, name in NEEDED:
        p = BASE / scope / name
        if not p.is_dir():
            print(f"  {scope}/{name:22} 目录不存在")
            all_empty = False
            continue
        pj = p / "package.json"
        files = sum(1 for _ in p.rglob("*") if _.is_file())
        print(
            f"  {scope}/{name:22} 目录存在  package.json={pj.is_file()}  文件数={files}"
        )
        if pj.is_file() or files:
            all_empty = False
    print(f"\n  → 五个包全部为空目录：{all_empty}")

    print("\n=== B. 顶层 node_modules/@deepseek-ai 里谁是真的 ===")
    top = BASE / "@deepseek-ai"
    for child in sorted(top.iterdir()):
        pj = child / "package.json"
        print(f"  {child.name:22} package.json={pj.is_file()}")

    print("\n=== C. v4.0.0 是否救得了（它没有 node_modules，且 import 同一批包）===")
    v4 = BASE / "dsh-connect-workbuddy"
    idx = v4 / "lib" / "index.js"
    text = idx.read_text(encoding="utf-8", errors="replace")
    for _, name in NEEDED:
        print(f"  v4.0.0 index.js 引用 @{'/'.join(_ for _ in (name,))}: {name in text}")

    print(
        "\n=== 结论 ===\n"
        "  本机没有这五个包的真实副本，sidecar 无法恢复；\n"
        "  继续 stub 是无底洞，且 stub PiAiAdapter 等于伪造推理路径。\n"
        "  替代路径：LLM_PROFILE=deepseek（DEEPSEEK_API_KEY 已在机器上）→ 真模型、真价格。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
