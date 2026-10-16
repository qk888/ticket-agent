import json, collections
d = json.load(open("eval_out/eval-multi-20261010-142137.json", encoding="utf-8"))
runs = d["cases"] if isinstance(d.get("cases"), list) else []
rows = [r for r in runs if not r.get("passed")]
out = collections.OrderedDict()
for r in rows:
    out.setdefault(r["case_id"], r)
print("failed runs:", len(rows), "| failed cases:", len(out))
for cid, r in list(out.items())[:40]:
    print(f"{cid:14s} {r['condition']:18s} verifier={r.get('verifier_passed')} obs={r.get('observed_action')}")
json.dump(list(out.keys()), open("eval_out/_failed_cases.json","w",encoding="utf-8"), ensure_ascii=False)
