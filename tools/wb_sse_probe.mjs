// 探针 6：hy3 流式里 tool_calls 的确切 delta 形状 —— sidecar 聚合器的唯一依据。
// 不猜，直接把原始 SSE 行打出来。
import { pathToFileURL } from "node:url";

const HB = "C:/Users/Administrator/.dsh/profiles/desktop/node_modules/dsh-workbuddy-connect/lib/host-heartbeat-L93zSVB5.js";
const m = await import(pathToFileURL(HB).href);
const client = new m.O();
const variant = m.w("workbuddy") ?? m.S;
const store = new m.m({ variant, refresh: (c) => client.refreshToken(c), keyProvider: m.b(variant) });
const cred = await store.resolve();

async function dump(label, body) {
  const prepared = m.P(JSON.stringify(body));
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 180_000);
  try {
    const r = await client.chatStream(cred, prepared, ctrl.signal);
    if (!r.ok) { console.log(`\n### ${label}\n  ✗ ${r.kind} ${r.status}: ${String(r.message).slice(0,300)}`); return; }
    const text = await r.response.text();
    const lines = text.split("\n").filter((l) => l.startsWith("data: "));
    console.log(`\n### ${label}  (${lines.length} 个 data 行)`);
    lines.forEach((l, i) => {
      const p = l.slice(6).trim();
      if (p === "[DONE]") { console.log(`  [${i}] [DONE]`); return; }
      let j; try { j = JSON.parse(p); } catch { console.log(`  [${i}] <非 JSON> ${p.slice(0,120)}`); return; }
      const ch = j.choices?.[0];
      const d = ch?.delta ?? {};
      // 只打印非空字段，看清每个 chunk 到底带了什么
      const bits = [];
      if (d.role) bits.push(`role=${d.role}`);
      if (d.content) bits.push(`content=${JSON.stringify(d.content)}`);
      if (d.reasoning_content) bits.push(`reasoning=${JSON.stringify(d.reasoning_content.slice(0,40))}…(${d.reasoning_content.length})`);
      if (d.tool_calls) bits.push(`tool_calls=${JSON.stringify(d.tool_calls)}`);
      if (ch?.finish_reason) bits.push(`★finish=${ch.finish_reason}`);
      if (j.usage) bits.push(`★usage=${JSON.stringify(j.usage)}`);
      console.log(`  [${i}] ${bits.join(" | ") || "(空 delta)"}`);
    });
  } finally { clearTimeout(timer); }
}

const TOOLS = [{
  type: "function",
  function: {
    name: "check_refund_eligibility",
    description: "核查某订单是否满足无理由退款条件。",
    parameters: { type: "object", properties: { order_id: { type: "integer", description: "订单号" } }, required: ["order_id"] },
  },
}, {
  type: "function",
  function: {
    name: "close_ticket",
    description: "关闭工单。",
    parameters: {
      type: "object",
      properties: { ticket_id: { type: "integer" }, resolution: { type: "string" }, status: { type: "string", enum: ["resolved", "rejected"] } },
      required: ["ticket_id", "resolution", "status"],
    },
  },
}];

const SYS = "你是电商售后工单处理助手。必须调用工具核查后再下结论。";

// ① 第一轮：模型该发 tool_call
await dump("① 触发 tool_call（首轮）", {
  model: "hy3",
  messages: [{ role: "system", content: SYS }, { role: "user", content: "工单 501：订单 9001，客户说签收 9 天了想退款。请处理。" }],
  tools: TOOLS,
  stream: true,
  max_tokens: 6000,
});

// ② 第二轮：回灌工具结果，模型该继续（这是 ticket-agent ReAct 的真实形状）
await dump("② 工具结果回灌后（ReAct 第二轮）", {
  model: "hy3",
  messages: [
    { role: "system", content: SYS },
    { role: "user", content: "工单 501：订单 9001，客户说签收 9 天了想退款。请处理。" },
    { role: "assistant", content: "", tool_calls: [{ id: "call_1", type: "function", function: { name: "check_refund_eligibility", arguments: '{"order_id":9001}' } }] },
    { role: "tool", tool_call_id: "call_1", content: '{"action":"reject","eligible":false,"reasons":["R1：签收已 9 天，超过 7 天无理由期限"]}' },
  ],
  tools: TOOLS,
  stream: true,
  max_tokens: 6000,
});
