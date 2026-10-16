"""语料指纹构成核对。

★ 为什么需要这个脚本：报表 meta 里那一格 `corpus_sha256` 是**结论可追溯的唯一凭据**，
但它是"合并指纹"（各文件 sha256 按名字排序拼接后再哈希）。这意味着
**往 data/raw/ 里多放一个 .jsonl，指纹就会变**——哪怕那个文件根本不参与造数。
本脚本把合并指纹拆开，看它到底由哪些文件、哪些字节构成。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

from ticket_agent.db import corpus  # noqa: E402

print("=" * 74)
print("一、合并指纹与它的成员")
print("=" * 74)
st = corpus.corpus_stats()
print(f"合并指纹（报表 meta 里那一格）：{st['sha256']}")
print(f"唯一话术：{st['unique_phrasings']}")
print()
print(f"{'文件':<30}{'sha256(前16)':<20}{'字节':>12}  存在")
for name in sorted(st["per_file"]):
    d = st["per_file"][name]
    size = d.get("bytes") or d.get("size") or "-"
    print(f"{name:<30}{d['sha256'][:16]:<20}{str(size):>12}  {d.get('exists')}")

manual = hashlib.sha256(
    "".join(f"{n}:{st['per_file'][n]['sha256']}" for n in sorted(st["per_file"])).encode()
).hexdigest()
print()
print(f"手工复算合并指纹：{manual}")
print(f"与 corpus_stats 一致：{manual == st['sha256']}")

print()
print("=" * 74)
print("二、合并指纹认错人的风险：data/raw 里还有哪些 .jsonl 没进指纹")
print("=" * 74)
raw = Path("data/raw")
in_fp = set(st["per_file"])
for p in sorted(raw.glob("*.jsonl")):
    mark = "已进指纹" if p.name in in_fp else "★ 未进指纹"
    print(f"{mark:<12}{p.name:<30}{p.stat().st_size:>12} 字节")
for p in sorted(raw.glob("*.bak")):
    print(f"{'备份文件':<12}{p.name:<30}{p.stat().st_size:>12} 字节")

print()
print("=" * 74)
print("三、PRIMARY_CORPUS 单文件指纹（造数实际读的那一个）")
print("=" * 74)
primary = raw / corpus.PRIMARY_CORPUS
print(f"文件：{corpus.PRIMARY_CORPUS}")
if primary.exists():
    print(f"sha256：{hashlib.sha256(primary.read_bytes()).hexdigest()}")
    print(f"字节：{primary.stat().st_size}")
    src = corpus.CORPUS_SOURCES.get(corpus.PRIMARY_CORPUS, {})
    print(f"溯源表声明 sha256：{src.get('sha256')}")
    print(f"溯源表声明 bytes ：{src.get('bytes')}")
    expect = sum(1 for _ in primary.open(encoding="utf-8"))
    print(f"实际行数：{expect}（溯源表声明 rows：{src.get('rows')}）")
else:
    print("★ 文件不存在")

print()
print("=" * 74)
print("四、结论")
print("=" * 74)
only_primary = len(in_fp) == 1
print(f"合并指纹的成员数：{len(in_fp)}")
if not only_primary:
    others = [n for n in sorted(in_fp) if n != corpus.PRIMARY_CORPUS]
    print(f"★ 除主语料外还包含：{others}")
    print("  → 这些文件里任何一个变了，报表 meta 的指纹都会变，")
    print("    即便造数根本没读它们。对比两份报表时要知道这一点。")
