r"""缺陷 ㉞：sidecar 的五个 peer 依赖在本机**根本不存在**，而它此前"能跑"。

这不是"插件坏了"，是**一次从未被验证过的静默降级**：

  - `dsh-workbuddy-connect` v0.7.1 的 `node_modules/@deepseek-ai/` 下五个目录
    全是**空目录**（0 文件、无 package.json）：`dsh-atomic-write` /
    `dsh-home-paths` / `dsh-llm` / `dsh-llm-pi-ai` / `schemastery`；
    `@earendil-works/pi-ai` 同样为空。
  - 顶层 `node_modules/@deepseek-ai/` 只有 `cosmokit` 与 `schemastery` 是真的。
  - 全盘（.dsh / AppData\Roaming\npm / AppData\Local / Program Files）按
    "有 package.json 的目录"搜这五个包 → **0 命中**。
  - `app.asar.unpacked` 里只有 `dsh-desktop-host` / `dsh-session-log-export` /
    `libreoffice-kit` 三个 @deepseek-ai 包，没有这五个。
  - `dsh-connect-workbuddy` v4.0.0 与 v0.7.1 是**两个不同的包**，前者没有
    node_modules，且它的 `index.js` **同样 import 这五个包** —— 换包没用。

而在此之前，`hy3` 档案的 `price_in/out = 0.0` 是**实测值**（真调用
`usage.credit: 0`、额度快照前后都是 1358），200 case × k=3 两臂 1200 run
也是真跑出来的。所以顺序是：

    某个时刻插件/依赖被清理或未完整安装 → sidecar 起不来
    → 但没有人知道，因为上一次成功读数是"历史事实"
    → 下一次 `_resume.py` 照常跑，每条 22s 快速失败，写成 tools=0 并标记已完成

这正是缺陷 ⑯「自检通过但闸门永远打不开」的同形第二例：
**一段流程的两半之间没有任何东西会变红。** 上一半（端点自检）早就绿过，
下一半（真的还在服务）没人复查；而 `_resume.py` 的 append-only 进度文件
把"连接失败"永久固化成"已跑完"。

为什么不再继续 stub：`dsh-llm-pi-ai` 里的 `PiAiAdapter` 是**真推理适配器**，
stub 它等于伪造推理路径 —— 读数还在，但它不再代表任何真实模型。
那是缺陷 ⑫「护栏守住钱没守住谎」的形状，比没有读数更糟。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE = Path(os.environ.get("DSH_NODE_MODULES") or (Path.home() / ".dsh" / "profiles" / "desktop" / "node_modules"))
PEERS = (
    ("@deepseek-ai", "dsh-atomic-write"),
    ("@deepseek-ai", "dsh-home-paths"),
    ("@deepseek-ai", "dsh-llm"),
    ("@deepseek-ai", "dsh-llm-pi-ai"),
    ("@deepseek-ai", "schemastery"),
    ("@earendil-works", "pi-ai"),
)


def main() -> int:
    print("=== A. 六个 peer 依赖的实际状态（空目录 = 有名字没内容）===")
    real = []
    for scope, name in PEERS:
        p = BASE / scope / name
        if not p.is_dir():
            print(f"  {scope}/{name:20} 目录不存在")
            continue
        pj = p / "package.json"
        files = sum(1 for x in p.rglob("*") if x.is_file())
        state = "真实" if (pj.is_file() and files) else "空壳"
        print(f"  {scope}/{name:20} 目录存在  package.json={pj.is_file()}  文件={files}  → {state}")
        if state == "真实":
            real.append(name)
    print(f"\n  六个里真实的只有：{real or '无'}")

    print("\n=== B. v4.0.0 能不能救（它 import 同一批包）===")
    v4_idx = BASE / "dsh-connect-workbuddy" / "lib" / "index.js"
    v4_text = v4_idx.read_text(encoding="utf-8", errors="replace")
    same = [n for _, n in PEERS if n in v4_text]
    print(f"  v4.0.0 也引用：{same}")
    print("  → 换包不能解决问题")

    print("\n=== 结论 ===")
    print("  sidecar 在本机无法恢复；继续 stub 会 stub 到真推理适配器（PiAiAdapter），")
    print("  那是伪造推理路径。真模型读数改走 config.py 里已有的 deepseek / hunyuan 档案。")
    print("  本条已作为缺陷 ㉞ 记入 README §4.2。")
    # 没有真实副本时，这份取证本身就是结论；不要用退出码假装失败
    return 0 if len(real) == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
