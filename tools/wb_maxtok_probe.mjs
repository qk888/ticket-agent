// 探针 5：hy3 的 max_tokens 缺省行为 —— 决定 sidecar 是否要注入下限。
// 背景：hy3 是 onlyReasoning 模型，思考 token 计入 max_tokens（实测 200 全被吃掉、content 为空）。
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
    if (!r.ok) {
      console.log(`\n### ${label}\n  ✗ ok=false ${r.kind} status=${r.status}: ${String(r.message).slice(0, 300)}`);
      return;
    }
    const text = await r.response.text();
    const evs = text.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6).trim()).filter((p) => p !== "[DONE]");
    let content = "", reasoning = "", finish = null, usage = null;
    for (const p of evs) {
      let j; try { j = JSON.parse(p); } catch { continue; }
      if (j.usage) usage = j.usage;
      const ch = j.choices?.[0]; if (!ch) continue;
      if (ch.finish_reason) finish = ch.finish_reason;
      content += ch.delta?.content ?? "";
      reasoning += ch.delta?.reasoning_content ?? "";
    }
    const rt = usage?.completion_tokens_details?.reasoning_tokens ?? 0;
    console.log(`\n### ${label}`);
    console.log(`  finish=${finish} | out=${usage?.completion_tokens} reasoning_tokens=${rt} | credit=${usage?.credit}`);
    console.log(`  content=${content.length}字 reasoning=${reasoning.length}字`);
    console.log(`  content: ${JSON.stringify(content.slice(0, 200))}`);
  } finally { clearTimeout(timer); }
}

const SYS = "你是电商售后工单处理助手。回答简洁。";
const Q = "客户问：签收 9 天还能无理由退货吗？请直接给出一句话答复。";

// 关键：完全不传 max_tokens
await run("① 不传 max_tokens（看上游默认）", {
  model: "hy3",
  messages: [{ role: "system", content: SYS }, { role: "user", content: Q }],
  stream: true,
});

// max_tokens 显式很小 vs 很大
await run("② max_tokens=64", {
  model: "hy3",
  messages: [{ role: "system", content: SYS }, { role: "user", content: Q }],
  stream: true,
  max_tokens: 64,
});

await run("③ max_tokens=64 但告知模型不要思考（看能否绕过）", {
  model: "hy3",
  messages: [
    { role: "system", content: SYS + " 直接输出答案，不要任何分析过程。" },
    { role: "user", content: Q },
  ],
  stream: true,
  max_tokens: 64,
});

// reasoning_effort 类参数是否被接受（hy3 defaultEffort=high）
await run("④ reasoning_effort=low", {
  model: "hy3",
  messages: [{ role: "system", content: SYS }, { role: "user", content: Q }],
  stream: true,
  max_tokens: 4000,
  reasoning_effort: "low",
});

await run("⑤ 不传 max_tokens 强行要求长回答（压力测试）", {
  model: "hy3",
  messages: [{ role: "system", content: SYS }, { role: "user", content: "请详细列出 R1-R7 七条售后政策各自适用条件，每条不少于三句话。" }],
  stream: true,
});
