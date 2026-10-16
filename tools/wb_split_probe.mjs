// 探针 3：判定 hy3 的 content / reasoning_content 分流规律，
// 以此决定 sidecar 要不要做 folding（这直接决定 ticket-agent 能否读到回复）。
import { pathToFileURL } from "node:url";

const HB = "C:/Users/Administrator/.dsh/profiles/desktop/node_modules/dsh-workbuddy-connect/lib/host-heartbeat-L93zSVB5.js";
const m = await import(pathToFileURL(HB).href);
const client = new m.O();
const variant = m.w("workbuddy") ?? m.S;
const store = new m.m({ variant, refresh: (c) => client.refreshToken(c), keyProvider: m.b(variant) });
const cred = await store.resolve();

async function run(label, body) {
  const prepared = m.P(JSON.stringify(body));
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 180_000);
  try {
    const r = await client.chatStream(cred, prepared, ctrl.signal);
    if (!r.ok) { console.log(`\n### ${label}\n  ✗ ok=false ${r.kind}: ${String(r.message).slice(0, 300)}`); return; }
    const text = await r.response.text();
    const evs = text.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6).trim()).filter((p) => p !== "[DONE]");
    let content = "", reasoning = "", finish = null, usage = null, nTool = 0;
    // 关键：记录 delta 里两个字段的先后顺序（判断是否「先思考后正文」）
    const order = [];
    for (const p of evs) {
      let j; try { j = JSON.parse(p); } catch { continue; }
      if (j.usage) usage = j.usage;
      const ch = j.choices?.[0]; if (!ch) continue;
      if (ch.finish_reason) finish = ch.finish_reason;
      const d = ch.delta ?? {};
      if (d.tool_calls?.length) nTool += d.tool_calls.length;
      if (d.reasoning_content) reasoning += d.reasoning_content;
      if (d.content) content += d.content;
      const tag = (d.reasoning_content ? "R" : "") + (d.content ? "C" : "") + (d.tool_calls?.length ? "T" : "");
      if (tag && order[order.length - 1] !== tag) order.push(tag);
    }
    const rt = usage?.completion_tokens_details?.reasoning_tokens ?? 0;
    console.log(`\n### ${label}`);
    console.log(`  finish=${finish} | out=${usage?.completion_tokens} reasoning_tokens=${rt} | delta 序列=[${order.join("→")}]`);
    console.log(`  content=${content.length}字 reasoning=${reasoning.length}字 tool_call片段=${nTool}`);
    console.log(`  content: ${JSON.stringify(content.slice(0, 400))}`);
    console.log(`  reasoning 尾 200: ${JSON.stringify(reasoning.slice(-200))}`);
  } finally { clearTimeout(timer); }
}

const SYS = "你是电商售后工单处理助手。回答简洁。";

// A. 纯问答，max_tokens 充足（这是最常见的「无工具调用」场景）
await run("A 纯问答 max_tokens=3000", {
  model: "hy3",
  messages: [{ role: "system", content: SYS }, { role: "user", content: "客户问：签收 9 天还能无理由退货吗？请直接给出一句话答复。" }],
  stream: true,
  max_tokens: 3000,
});

// B. 稍复杂的推理问答
await run("B 需要推理的问答 max_tokens=4000", {
  model: "hy3",
  messages: [
    { role: "system", content: SYS },
    { role: "user", content: "某订单签收 3 天、金额 820 元、生鲜类目，客户要退款。按规则：R1 七天内可无理由退；R6 生鲜不支持无理由退。请判断该不该退，并说明依据。" },
  ],
  stream: true,
  max_tokens: 4000,
});

// C. 带工具但预期不调用（信息已足够）
await run("C 带工具但可直接回答 max_tokens=3000", {
  model: "hy3",
  messages: [{ role: "system", content: SYS }, { role: "user", content: "你好，请问你们几点上班？" }],
  tools: [{ type: "function", function: { name: "check_refund_eligibility", description: "判定退款资格", parameters: { type: "object", properties: { order_id: { type: "integer" } }, required: ["order_id"] } } }],
  stream: true,
  max_tokens: 3000,
});

// D. 极小 max_tokens（复现「内容被思考吞光」）
await run("D max_tokens=150 强制截断", {
  model: "hy3",
  messages: [{ role: "system", content: SYS }, { role: "user", content: "客户问：签收 9 天还能无理由退货吗？请直接给出一句话答复。" }],
  stream: true,
  max_tokens: 150,
});
