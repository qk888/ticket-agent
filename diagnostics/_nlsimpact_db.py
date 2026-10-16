"""按当前库实测：工单正文含繁体（会被 normalize 改变）的条数。

★ 为什么单独量：交付文档里引用了「587 张工单里正文含繁体的是 1 张（0.17%）」，
   但库后来重造成了 583 张。引用一个旧库测出的数，等于让读者拿一个
   当时成立、现在无法复现的数字去评估风险 —— 这正是缺陷 ⑨「基准漂移」
   在文档层面的翻版：漂移源从时钟换成了"我没重新量"。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ticket_agent.db import session as db_session  # noqa: E402
from ticket_agent.db.models import Ticket  # noqa: E402
from ticket_agent.db.textnorm import normalize  # noqa: E402


def main() -> None:
    with db_session.session_scope() as db:
        rows = [(t.id, t.body) for t in db.query(Ticket).all()]

    total = len(rows)
    changed = [(tid, body) for tid, body in rows if normalize(body) != body]
    print(f"工单总数            : {total}")
    print(f"正文含表内繁体（会被归一化改变）: {len(changed)}"
          f"  ({(len(changed) / total * 100) if total else 0:.2f}%)")
    for tid, body in changed[:10]:
        print(f"  [{tid}] {body[:60]}")


if __name__ == "__main__":
    main()
