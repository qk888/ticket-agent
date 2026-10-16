// sidecar 验收：healthz / 非流式聚合 / tool calling / 流式透传 / 小 max_tokens 抬升。
// 这不是探针，是 ticket-agent 将要依赖的那条路径的验收测试。
import { readFileSync } from "node:fs";

const disc = JSON.parse(readFileSync(new URL("../.workbuddy-sidecar.json", import.meta.url), "utf8"));
const BASE = disc.baseUrl;
const TOKEN = disc.apiKey;
console.log(`sidecar: ${BASE}（pid=${disc.pid}，model=${disc.model}，shim=${disc.shimBaseUrl}）\n`);

const H = { "Content-Type": "application/json", Authorization: `Bearer ${TOKEN}` };

let pass = 0, fail = 0;
function check(label, ok, detail = "") {
  if (ok) { pass += 1; console.log(`  ✓ ${label}${detail ? ` — ${detail}` : ""}`); }
  else { fail += 1; console.log(`  ✗ ${label}${detail ? ` — ${detail}` : ""}`); }
}

// ── ① healthz ──
console.log("### ① /healthz");
{
  const r = await fetch(`${BASE.replace("/v1", "")}/healthz`, { headers: H });
  const j = await r.json();
  check("HTTP 200", r.status === 200, `status=${r.status}`);
  check("登录态 signed-in", j.workbuddy?.state === "signed-in", `domain=${j.workbuddy?.domain}`);
  check("报告了输出下限", typeof j.minMaxTokens === "number", `minMaxTokens=${j.minMaxTokens}`);
}

// ── ② 鉴权与反 rebinding ──
console.log("\n### ② 鉴权 / Host / Origin 三道校验");
{
  const noAuth = await fetch(`${BASE}/chat/completions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  check("无 bearer → 401", noAuth.status === 401, `status=${noAuth.status}`);
  const badAuth = await fetch(`${BASE}/chat/completions`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer wrong" }, body: "{}" });
  check("错 bearer → 401", badAuth.status === 401, `status=${badAuth.status}`);
  const badOrigin = await fetch(`${BASE}/chat/completions`, { method: "POST", headers: { ...H, Origin: "https://evil.example" }, body: "{}" });
  check("非 loopback Origin → 403", badOrigin.status === 403, `status=${badOrigin.status}`);
}

// ── ③ /v1/models ──
console.log("\n### ③ GET /v1/models");
{
  const r = await fetch(`${BASE}/models`, { headers: H });
  const j = await r.json();
  check("返回 OpenAI 名册形状", j.object === "list" && Array.isArray(j.data), `data=${JSON.stringify(j.data)}`);
}

// ── ④ 非流式聚合（LangChain 默认路径，sidecar 存在的首要理由）──
console.log("\n### ④ 非流式聚合（关键：shim 只会吐 SSE，这里必须变成 JSON）");
{
  const t0 = Date.now();
  const r = await fetch(`${BASE}/chat/completions`, {
    method: "POST",
    headers: H,
    body: JSON.stringify({
      model: "hy3",
      messages: [
        { role: "system", content: "你是电商售后工单处理助手。回答简洁。" },
        { role: "user", content: "签收 9 天还能无理由退货吗？一句话答复。" },
      ],
      stream: false,
    }),
  });
  const ct = r.headers.get("content-type") ?? "";
  check("content-type 是 application/json（不是 SSE）", ct.includes("application/json"), ct);
  const j = await r.json();
  check("object=chat.completion", j.object === "chat.completion", j.object);
  check("有 choices[0].message", j.choices?.[0]?.message !== undefined);
  const content = j.choices?.[0]?.message?.content ?? "";
  check("content 非空（思考没吞掉正文）", content.length > 0, `${content.length}字：${JSON.stringify(content.slice(0, 80))}`);
  check("finish_reason 有值", typeof j.choices?.[0]?.finish_reason === "string", j.choices?.[0]?.finish_reason);
  check("usage 带 token 数", typeof j.usage?.completion_tokens === "number", `prompt=${j.usage?.prompt_tokens} completion=${j.usage?.completion_tokens} 思考=${j.usage?.completion_tokens_details?.reasoning_tokens}`);
  check("reasoning 被单独保留，没混进 content", (j.choices?.[0]?.message?.reasoning_content ?? "").length > 0 && !content.includes("我需要"));
  console.log(`    耗时 ${Date.now() - t0}ms`);
}

// ── ⑤ tool calling（ticket-agent 的主路径）──
const TOOLS = [{
  type: "function",
  function: {
    name: "check_refund_eligibility",
    description: "核查某订单是否满足无理由退款条件。",
    parameters: { type: "object", properties: { order_id: { type: "integer" } }, required: ["order_id"] },
  },
}];

console.log("\n### ⑤ tool calling（非流式聚合后的 tool_calls 必须完整）");
let firstCallId = null;
{
  const r = await fetch(`${BASE}/chat/completions`, {
    method: "POST",
    headers: H,
    body: JSON.stringify({
      model: "hy3",
      messages: [
        { role: "system", content: "你是电商售后工单处理助手。必须调用工具核查后再下结论。" },
        { role: "user", content: "订单 9001 客户想退款，请核查资格。" },
      ],
      tools: TOOLS,
      stream: false,
    }),
  });
  const j = await r.json();
  const msg = j.choices?.[0]?.message ?? {};
  const tcs = msg.tool_calls ?? [];
  check("finish_reason=tool_calls", j.choices?.[0]?.finish_reason === "tool_calls", j.choices?.[0]?.finish_reason);
  check("拿到 1 个 tool_call", tcs.length === 1, `${tcs.length} 个`);
  if (tcs.length > 0) {
    firstCallId = tcs[0].id;
    check("函数名未被空分片抹掉", tcs[0].function?.name === "check_refund_eligibility", `name=${JSON.stringify(tcs[0].function?.name)}`);
    check("有 id", typeof tcs[0].id === "string" && tcs[0].id.length > 0, tcs[0].id);
    let parsed = null;
    try { parsed = JSON.parse(tcs[0].function?.arguments ?? ""); } catch (e) { /* 下面报 */ }
    check("arguments 是合法 JSON", parsed !== null, tcs[0].function?.arguments);
    check("arguments 内容正确", parsed?.order_id === 9001, JSON.stringify(parsed));
  }
}

// ── ⑥ 回灌工具结果后继续（ReAct 第二轮：正解应关成 rejected）──
console.log("\n### ⑥ 工具结果回灌（ReAct 第二轮，ticket-agent 的真实形状）");
{
  const r = await fetch(`${BASE}/chat/completions`, {
    method: "POST",
    headers: H,
    body: JSON.stringify({
      model: "hy3",
      messages: [
        { role: "system", content: "你是电商售后工单处理助手。必须调用工具核查后再下结论。" },
        { role: "user", content: "订单 9001 客户想退款，请核查资格。" },
        { role: "assistant", content: "", tool_calls: [{ id: firstCallId ?? "call_1", type: "function", function: { name: "check_refund_eligibility", arguments: '{"order_id":9001}' } }] },
        { role: "tool", tool_call_id: firstCallId ?? "call_1", content: '{"action":"reject","eligible":false,"reasons":["R1：签收已 9 天，超过 7 天无理由期限"]}' },
      ],
      stream: false,
    }),
  });
  const j = await r.json();
  const msg = j.choices?.[0]?.message ?? {};
  const content = msg.content ?? "";
  check("HTTP 200 且能继续", r.status === 200, `status=${r.status}`);
  check("有正文说明结论", content.length > 0, `${content.length}字`);
  check("结论里认了不合格", /不符合|不予|驳回|超过|9\s*天/.test(content), JSON.stringify(content.slice(0, 120)));
  console.log(`    content: ${JSON.stringify(content)}`);
}

// ── ⑦ 小 max_tokens 是否被抬高（端点自检脚本会发 16）──
console.log("\n### ⑦ 小 max_tokens 抬升（否则 16 个 token 全被思考吃掉、正文空）");
{
  const r = await fetch(`${BASE}/chat/completions`, {
    method: "POST",
    headers: H,
    body: JSON.stringify({
      model: "hy3",
      messages: [{ role: "user", content: "1+1=?" }],
      max_tokens: 16,
      stream: false,
    }),
  });
  const j = await r.json();
  const content = j.choices?.[0]?.message?.content ?? "";
  const rt = j.usage?.completion_tokens_details?.reasoning_tokens ?? 0;
  check("正文非空（说明 16 被抬到了下限）", content.length > 0, `${content.length}字：${JSON.stringify(content.slice(0, 60))}`);
  check("实际 completion_tokens 远超 16", (j.usage?.completion_tokens ?? 0) > 16, `completion=${j.usage?.completion_tokens} reasoning=${rt}`);
}

// ── ⑧ 流式透传 ──
console.log("\n### ⑧ 流式（stream:true 应原样透传 SSE）");
{
  const r = await fetch(`${BASE}/chat/completions`, {
    method: "POST",
    headers: H,
    body: JSON.stringify({ model: "hy3", messages: [{ role: "user", content: "说三个字" }], stream: true }),
  });
  const ct = r.headers.get("content-type") ?? "";
  check("content-type 是 SSE", ct.includes("text/event-stream"), ct);
  const text = await r.text();
  check("含多个 data: 行", (text.match(/^data: /gm) ?? []).length > 2, `${(text.match(/^data: /gm) ?? []).length} 行`);
  check("以 [DONE] 收尾", text.trimEnd().endsWith("[DONE]"), JSON.stringify(text.trimEnd().slice(-20)));
}

console.log(`\n${"=".repeat(60)}\n通过 ${pass} / 失败 ${fail}`);
process.exit(fail === 0 ? 0 : 1);
