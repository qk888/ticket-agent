// 探针 2：hy3 的 tool calling 能力 —— ticket-agent 的硬门槛。
// hy3 是 onlyReasoning 模型（思考不可关闭），必须确认思考不会吞掉 tool_calls。
import { pathToFileURL } from "node:url";

const HB = "C:/Users/Administrator/.dsh/profiles/desktop/node_modules/dsh-workbuddy-connect/lib/host-heartbeat-L93zSVB5.js";
const m = await import(pathToFileURL(HB).href);

const client = new m.O();
const variant = m.w("workbuddy") ?? m.S;
const store = new m.m({
  variant,
  refresh: (c) => client.refreshToken(c),
  keyProvider: m.b(variant),
});
const cred = await store.resolve();

/** 打一次调用，返回解析后的聚合结果。 */
async function call(label, body, timeoutMs = 120_000) {
  const prepared = m.P(JSON.stringify(body));
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  const t0 = Date.now();
  try {
    const result = await client.chatStream(cred, prepared, ctrl.signal);
    if (!result.ok) {
      console.log(`\n### ${label} → 失败 ok=false status=${result.status} kind=${result.kind}`);
      console.log("   ", String(result.message).slice(0, 400));
      return null;
    }
    const text = await result.response.text();
    const events = text.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6).trim()).filter((p) => p !== "[DONE]");
    let content = "", reasoning = "", usage = null, finish = null, id = null;
    const toolCalls = new Map(); // index -> {id, name, args}
    for (const p of events) {
      let j; try { j = JSON.parse(p); } catch { continue; }
      id ??= j.id;
      if (j.usage) usage = j.usage;
      const ch = j.choices?.[0];
      if (!ch) continue;
      if (ch.finish_reason) finish = ch.finish_reason;
      const d = ch.delta ?? {};
      if (d.content) content += d.content;
      if (d.reasoning_content) reasoning += d.reasoning_content;
      for (const tc of d.tool_calls ?? []) {
        const idx = tc.index ?? 0;
        const cur = toolCalls.get(idx) ?? { id: "", name: "", args: "" };
        if (tc.id) cur.id = tc.id;
        if (tc.function?.name) cur.name = tc.function.name;
        if (tc.function?.arguments) cur.args += tc.function.arguments;
        toolCalls.set(idx, cur);
      }
    }
    const ms = Date.now() - t0;
    const rt = usage?.completion_tokens_details?.reasoning_tokens ?? usage?.completion_thinking_tokens ?? 0;
    console.log(`\n### ${label}`);
    console.log(`  ${ms}ms | events=${events.length} | finish=${finish} | usage.in=${usage?.prompt_tokens} out=${usage?.completion_tokens} reasoning_tokens=${rt} | credit=${usage?.credit}`);
    console.log(`  content=${content.length}字 reasoning=${reasoning.length}字 tool_calls=${toolCalls.size}`);
    if (content) console.log(`  content 前 300: ${content.slice(0, 300)}`);
    for (const [i, tc] of toolCalls) {
      console.log(`  [tool_call ${i}] name=${tc.name} id=${tc.id}`);
      console.log(`      args=${tc.args.slice(0, 400)}`);
      // 校验 JSON 合法性 —— 流式拼接的 arguments 常常不是合法 JSON
      try { JSON.parse(tc.args); console.log("      args JSON: 合法"); }
      catch (e) { console.log(`      args JSON: ★不合法 → ${String(e).slice(0, 120)}`); }
    }
    return { content, reasoning, toolCalls, finish, usage, ms };
  } finally {
    clearTimeout(timer);
  }
}

const TOOLS = [
  {
    type: "function",
    function: {
      name: "check_refund_eligibility",
      description: "按售后政策判定某订单能否退款/退货。必须在下结论前调用。",
      parameters: {
        type: "object",
        properties: { order_id: { type: "integer", description: "订单号" } },
        required: ["order_id"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "create_refund",
      description: "创建退款单。仅当 check_refund_eligibility 判定 eligible=true 时可调用。",
      parameters: {
        type: "object",
        properties: { order_id: { type: "integer" }, amount: { type: "number" }, reason: { type: "string" } },
        required: ["order_id", "amount", "reason"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "close_ticket",
      description: "关闭工单，status 只能是 resolved 或 rejected。",
      parameters: {
        type: "object",
        properties: {
          ticket_id: { type: "integer" },
          resolution: { type: "string" },
          status: { type: "string", enum: ["resolved", "rejected"] },
        },
        required: ["ticket_id", "resolution", "status"],
      },
    },
  },
];

const SYS = "你是电商售后工单处理助手。必须先用工具核实政策再下结论，不得编造。";

// 1) 基本 tool calling
await call("① 简单工具调用（应调 check_refund_eligibility）", {
  model: "hy3",
  messages: [
    { role: "system", content: SYS },
    { role: "user", content: "工单 501，订单 9001，客户说收到货 9 天了想无理由退货。请处理。" },
  ],
  tools: TOOLS,
  stream: true,
  max_tokens: 2000,
});

// 2) 工具结果回灌 → 多轮
await call("② 工具结果回灌后继续（看能否走第二步）", {
  model: "hy3",
  messages: [
    { role: "system", content: SYS },
    { role: "user", content: "工单 501，订单 9001，客户说收到货 9 天了想无理由退货。请处理。" },
    { role: "assistant", content: "", tool_calls: [{ id: "call_1", type: "function", function: { name: "check_refund_eligibility", arguments: '{"order_id":9001}' } }] },
    { role: "tool", tool_call_id: "call_1", content: '{"action":"reject","eligible":false,"reasons":["R1：签收已 9 天，超过 7 天无理由期限"]}' },
  ],
  tools: TOOLS,
  stream: true,
  max_tokens: 2000,
});

// 3) developer 角色（pi-ai 会发这个，上游曾以 11128 拒绝）
await call("③ developer 角色（看 prepareChatBody 是否改写成功）", {
  model: "hy3",
  messages: [
    { role: "developer", content: SYS },
    { role: "user", content: "只回答：1+1=?" },
  ],
  stream: true,
  max_tokens: 500,
});

// 4) tool_choice 对象形式（normalizeToolChoice）
await call("④ tool_choice 对象形式", {
  model: "hy3",
  messages: [
    { role: "system", content: SYS },
    { role: "user", content: "工单 502，订单 9002，刚签收 1 天，客户要退款。" },
  ],
  tools: TOOLS,
  tool_choice: { type: "function", function: { name: "check_refund_eligibility" } },
  stream: true,
  max_tokens: 2000,
});

// 5) 小 max_tokens 下 content 是否被思考吞光
await call("⑤ max_tokens=200（复现 content 为空）", {
  model: "hy3",
  messages: [
    { role: "system", content: SYS },
    { role: "user", content: "用一句话说明退款政策 R1。" },
  ],
  stream: true,
  max_tokens: 200,
});
