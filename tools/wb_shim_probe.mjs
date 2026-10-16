// 探针 4：shim 对 stream:false / 缺省 的实际响应形状。
// 决定 sidecar 是否必须做 SSE→JSON 聚合（ticket-agent 走 LangChain ChatOpenAI，默认非流式）。
// 注意：index.js 与 host-heartbeat 各有各的导出，需要合并使用。
import { pathToFileURL } from "node:url";

const IDX = "C:/Users/Administrator/.dsh/profiles/desktop/node_modules/dsh-workbuddy-connect/lib/index.js";
const HB = "C:/Users/Administrator/.dsh/profiles/desktop/node_modules/dsh-workbuddy-connect/lib/host-heartbeat-L93zSVB5.js";
const idx = await import(pathToFileURL(IDX).href);
const hb = await import(pathToFileURL(HB).href);

console.log("=== index.js 里可用的构造器 ===");
for (const k of ["createWorkBuddyShim", "WorkBuddyCredentialStore", "WorkBuddyUpstreamClient", "WorkBuddyCatalog"]) {
  console.log(`  ${k}: ${typeof idx[k]}`);
}
console.log("=== host-heartbeat 别名里可用的 ===");
for (const [k, label] of [["w", "variantFor"], ["b", "atRestKeyProviderFor"], ["S", "CN_VARIANT"]]) {
  console.log(`  ${label}(${k}): ${typeof hb[k]}`);
}

const variant = hb.w("workbuddy") ?? hb.S;
const client = new idx.WorkBuddyUpstreamClient();
const store = new idx.WorkBuddyCredentialStore({
  variant,
  refresh: (c) => client.refreshToken(c),
  keyProvider: hb.b(variant),
});
const catalog = new idx.WorkBuddyCatalog({ variant });
const shim = idx.createWorkBuddyShim({ store, client, catalog, logger: console });
await shim.ready;
console.log("\nshim baseUrl:", shim.baseUrl());

const hostHeader = new URL(shim.baseUrl()).host;
const H = { "Content-Type": "application/json", Authorization: `Bearer ${shim.token()}`, Host: hostHeader };

async function post(label, body) {
  console.log(`\n### ${label}`);
  const t0 = Date.now();
  const r = await fetch(`${shim.baseUrl()}/v1/chat/completions`, { method: "POST", headers: H, body: JSON.stringify(body) });
  const ctype = r.headers.get("content-type");
  const text = await r.text();
  console.log(`  ${Date.now() - t0}ms | HTTP ${r.status} | content-type: ${ctype} | 字节 ${text.length}`);
  if (ctype?.includes("json") && !text.startsWith("data: ")) {
    try {
      const j = JSON.parse(text);
      console.log("  ★ 是 JSON —— OpenAI 非流式形状");
      console.log("  顶层键:", Object.keys(j).join(", "));
      console.log("  choices[0].message:", JSON.stringify(j.choices?.[0]?.message).slice(0, 400));
      console.log("  usage:", JSON.stringify(j.usage));
      return;
    } catch (e) { console.log("  JSON 解析失败:", String(e).slice(0, 120)); }
  }
  if (text.startsWith("data: ")) {
    const evs = text.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6).trim()).filter((p) => p !== "[DONE]");
    let content = "", reasoning = "", finish = null, usage = null, toolCalls = 0;
    for (const p of evs) {
      let j; try { j = JSON.parse(p); } catch { continue; }
      if (j.usage) usage = j.usage;
      const ch = j.choices?.[0]; if (!ch) continue;
      if (ch.finish_reason) finish = ch.finish_reason;
      content += ch.delta?.content ?? "";
      reasoning += ch.delta?.reasoning_content ?? "";
      toolCalls += ch.delta?.tool_calls?.length ?? 0;
    }
    console.log(`  ★ 返回的是 SSE（content-type 却是 ${ctype}）`);
    console.log(`  events=${evs.length} finish=${finish} content=${content.length}字 reasoning=${reasoning.length}字 tool片段=${toolCalls}`);
    console.log(`  usage=${JSON.stringify(usage)}`);
    console.log(`  content: ${JSON.stringify(content.slice(0, 200))}`);
  } else {
    console.log("  前 300:", JSON.stringify(text.slice(0, 300)));
  }
}

const base = { model: "hy3", messages: [{ role: "user", content: "只回答两个字：收到" }], max_tokens: 600 };

await post("① stream:false（LangChain 非流式默认）", { ...base, stream: false });
await post("② stream 字段缺省", { ...base });
await post("③ stream:true（显式流式）", { ...base, stream: true });
await post("④ 带 tools 且 stream:false", {
  model: "hy3",
  messages: [{ role: "user", content: "查一下订单 9001 能不能退" }],
  tools: [{ type: "function", function: { name: "check_refund_eligibility", description: "判定退款资格", parameters: { type: "object", properties: { order_id: { type: "integer" } }, required: ["order_id"] } } }],
  stream: false,
  max_tokens: 2000,
});

await shim.close();
process.exit(0);
