// 单次请求，打印完整错误正文（定位 502 sidecar_upstream_unreachable 的真实原因）
import { readFileSync } from "node:fs";
const disc = JSON.parse(readFileSync(new URL("../.workbuddy-sidecar.json", import.meta.url), "utf8"));
const r = await fetch(`${disc.baseUrl}/chat/completions`, {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${disc.apiKey}` },
  body: JSON.stringify({ model: "hy3", messages: [{ role: "user", content: "hi" }], stream: false }),
});
console.log(`HTTP ${r.status} | ${r.headers.get("content-type")}`);
console.log(await r.text());
