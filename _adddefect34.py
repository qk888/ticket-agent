"""缺陷 ㉞ 落档：sidecar 的五个 peer 依赖在本机根本不存在，而它此前"能跑"。

三段都做了幂等与断言；跑完自查残留。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent
README = ROOT / "README.md"
OVERVIEW = ROOT / "交付总览.md"
CHECK = ROOT / "_readmecheck.py"


def sub(text: str, old: str, new: str, where: str) -> str:
    n = text.count(old)
    if n == 0 and new in text:
        print(f"  幂等跳过：{where}")
        return text
    assert n == 1, f"{where}：期望命中 1 次，实际 {n} 次 —— {old[:60]!r}"
    return text.replace(old, new)


DEFECT34 = """
---

**缺陷 ㉞：端点的 peer 依赖在本机根本不存在，而它此前"能跑"** —— sidecar 起不来的完整定性。

- **现象**：200 case 两臂报表出完之后重跑任何一条真模型 case，全部变成
  `turns=1 tools=0 action=none`、约 22 秒返回。`Invoke-WebRequest http://127.0.0.1:8787/healthz`
  返回"目标计算机积极拒绝"，`Get-NetTCPConnection -LocalPort 8787` 为空，node 进程列表里没有 sidecar。
  直接 `node tools/wb_sidecar.mjs --port 8787` 报：

      [sidecar] 加载插件运行时失败：Cannot find package '@deepseek-ai/dsh-home-paths'
      imported from ...\\dsh-workbuddy-connect\\lib\\host-heartbeat-L93zSVB5.js

- **取证**（`_d34sidecar.py`，纯只读）：
  - `dsh-workbuddy-connect` v0.7.1 的 `node_modules/@deepseek-ai/` 下五个目录
    **全是空目录**（0 文件、无 package.json）：`dsh-atomic-write` / `dsh-home-paths` /
    `dsh-llm` / `dsh-llm-pi-ai` / `schemastery`；`@earendil-works/pi-ai` 同样为空；
  - 顶层 `node_modules/@deepseek-ai/` 只有 `cosmokit` 与 `schemastery` 是真的；
  - 全盘（`.dsh` / `AppData\\Roaming\\npm` / `AppData\\Local` / `Program Files`）按
    "有 package.json 的目录"搜这五个包 → **0 命中**；
  - `app.asar.unpacked` 里只有 `dsh-desktop-host` / `dsh-session-log-export` / `libreoffice-kit`
    三个 @deepseek-ai 包，没有这五个；
  - `dsh-connect-workbuddy` v4.0.0 与 v0.7.1 是**两个不同的包**，前者没有 node_modules，
    且它的 `index.js` **同样 import 这五个包** —— 换包不能解决问题。
- **根因**：插件把内部包打进 `node_modules` 时只留了目录名，没有内容。
  在此之前 `hy3` 档案的 `price_in/out = 0.0` 是**实测值**（真调用 `usage.credit: 0`、
  额度快照前后都是 1358），1200 run 也是真跑出来的 —— 所以顺序是：

       依赖被清理/未完整安装 → sidecar 起不来
       → 没有人知道，因为上一次成功读数是"历史事实"
       → 下一次 _resume.py 照常跑，每条 22s 快速失败，写成 tools=0 并标记已完成

  这是缺陷 ⑯「自检通过但闸门永远打不开」的**同形第二例**：
  **一段流程的两半之间没有任何东西会变红。** 上一半（端点自检）早就绿过，
  下一半（服务还在不在）没人复查；而 `_resume.py` 的 append-only 进度文件
  把"连接失败"永久固化成"已跑完"。
- **为什么不再继续 stub**：`dsh-llm-pi-ai` 里的 `PiAiAdapter` 是**真推理适配器**，
  stub 它等于伪造推理路径 —— 读数还在，但它不再代表任何真实模型。
  那是缺陷 ⑫「护栏守住钱没守住谎」的形状，**比没有读数更糟**。
  已落地的三个脚手架（`tools/_sidecar_boot.mjs` / `_atomic_hook.mjs` / `_atomic_stub.mjs`）
  保留为"走到哪一步"的证据，不再往前 stub。
- **出路**：真模型读数改走 `config.py` 里已有的 `deepseek` / `hunyuan` 档案
  （`DEEPSEEK_API_KEY` 已在机器上、官方价格可查），不依赖任何插件。
  本机 Ollama 只能做"流程能跑通"的冒烟，不出能力读数。

## 5. 目录结构
"""

readme = README.read_text(encoding="utf-8")
readme = sub(
    readme,
    "### 4.2 三十三个真实缺陷（面试时讲这个比讲架构图有用）",
    "### 4.2 三十四个真实缺陷（面试时讲这个比讲架构图有用）",
    "README §4.2 标题",
)
for old, new in [
    ("这是全部三十三个里我花时间最多的一个", "这是全部三十四个里我花时间最多的一个"),
    ("这是三十三个里最应该怕的一个", "这是三十四个里最应该怕的一个"),
]:
    readme = sub(readme, old, new, "README 正文计数")
readme = readme.replace("#42-三十三个真实缺陷", "#42-三十四个真实缺陷")

if "**缺陷 ㉞：" in readme:
    print("  幂等跳过：缺陷 ㉞ 正文")
else:
    readme = sub(readme, "\n## 5. 目录结构\n", DEFECT34, "README §4.2 追加缺陷 ㉞")

if "# 234 passed" not in readme:
    readme = sub(readme, "# 231 passed", "# 234 passed", "README 测试数")
README.write_text(readme, encoding="utf-8")
print(f"README.md {len(readme)} 字符")

chk = CHECK.read_text(encoding="utf-8")
chk = sub(chk, "EXPECTED_DEFECTS = 33", "EXPECTED_DEFECTS = 34", "_readmecheck.py 缺陷数")
chk = sub(chk, "[①-⑳㉑-㉝]", "[①-⑳㉑-㉞]", "_readmecheck.py 字符类")
CHECK.write_text(chk, encoding="utf-8")
print("_readmecheck.py 已同步")

ov = OVERVIEW.read_text(encoding="utf-8")
ov = sub(
    ov,
    "#### ② 三十三个真实缺陷（面试时讲这个比讲架构图有用）",
    "#### ② 三十四个真实缺陷（面试时讲这个比讲架构图有用）",
    "交付总览 §② 标题",
)
ov = ov.replace("（完整三十三个：", "（完整三十四个：")
if "端点的 peer 依赖在本机根本不存在" not in ov:
    ov = sub(
        ov,
        "/ 售后记录的删除维度与幂等维度不是同一个。",
        "/ 售后记录的删除维度与幂等维度不是同一个 / 端点的 peer 依赖在本机根本不存在。",
        "交付总览 缺陷全名单",
    )
ov = ov.replace("| 缺陷取证链 | **33 条** |", "| 缺陷取证链 | **34 条** |")
ov = ov.replace("**33 条缺陷取证链**", "**34 条缺陷取证链**")
OVERVIEW.write_text(ov, encoding="utf-8")
print(f"交付总览.md {len(ov)} 字符")

for name, text in (("README.md", readme), ("交付总览.md", ov), ("_readmecheck.py", chk)):
    for bad in ("三十三个真实缺陷", "33 条缺陷取证链"):
        assert bad not in text, f"{name} 残留 {bad}"
print("✓ 无残留")
