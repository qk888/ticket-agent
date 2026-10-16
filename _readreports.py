"""读 200 case 报表的失败归因与三率，写进 数据集跑批结果汇总.md 的「两份干净报表」节。

只用真实数字：直接读 eval_out/eval-*-llm-200c.md，不手算。
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).parent
for name in ("single", "multi"):
    p = ROOT / "eval_out" / f"eval-{name}-llm-200c.md"
    t = p.read_text(encoding="utf-8")
    print(f"===== {name} =====")
    # 失败归因
    for line in t.splitlines():
        if line.startswith("| `") and ("%" in line or "条" in line):
            print(line)
        if "失败归因" in line or line.startswith("## "):
            print("##", line[:60])
    print()
