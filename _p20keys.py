from ticket_agent.db.seed import build_eval_cases
import json
cs = build_eval_cases(size=40, seed=11)
print("keys:", sorted(cs[0].keys()))
print(json.dumps({k: str(v)[:60] for k, v in cs[0].items()}, ensure_ascii=False, indent=2))
