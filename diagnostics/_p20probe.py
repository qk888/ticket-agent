from ticket_agent.config import settings
from ticket_agent.endpoints import last_check_matches, resolve_endpoint
ep = settings.endpoint
print("eval_dir     :", settings.eval_dir)
print("check path   :", settings.eval_dir / ".endpoint_check.json", (settings.eval_dir / ".endpoint_check.json").exists())
print("ep profile   :", ep.profile, "| base:", ep.base_url, "| model:", ep.model)
print("matches      :", last_check_matches(ep))
ep2 = resolve_endpoint(None)
print("resolve      :", ep2.profile, ep2.base_url, ep2.model, last_check_matches(ep2))
