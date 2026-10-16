// 探针：WorkBuddy 的额度是否真被扣 + 模型名册里有没有价目字段。
//
// 要回答的问题只有一个：**hy3 能不能声明 price=0**。
//   ticket-agent 的档案表里 price 是"元/千 token"，填 0 表示"边际成本为 0"。
//   截图写着「限时免费」，但截图不是账单 —— 实测额度前后差值才是。
//
// 只读；输出不含 token 明文。
import { pathToFileURL } from "node:url";

const HB = "C:/Users/Administrator/.dsh/profiles/desktop/node_modules/dsh-workbuddy-connect/lib/host-heartbeat-L93zSVB5.js";
const m = await import(pathToFileURL(HB).href);

const WorkBuddyCredentialStore = m.m;
const WorkBuddyUpstreamClient = m.O;
const atRestKeyProviderFor = m.b;
const variantFor = m.w;
const CN_VARIANT = m.S;
const prepareChatBody = m.P;
const WorkBuddyCatalog = m.d;
const fallbackModels = m.u ?? m.l;

const variant = variantFor("workbuddy") ?? CN_VARIANT;
const client = new WorkBuddyUpstreamClient();
const store = new WorkBuddyCredentialStore({
  variant,
  refresh: (c) => client.refreshToken(c),
  keyProvider: atRestKeyProviderFor(variant),
});
const cred = await store.resolve();

// ── 1. 模型名册：hy3 那条有没有价目/额度字段 ──────────────────────
console.log("=== 1. 模型名册里 hy3 的全部字段 ===");
try {
  const cat = new WorkBuddyCatalog(Array.isArray(fallbackModels) ? fallbackModels : []);
  const rows = cat.current?.() ?? [];
  console.log("  名册模型数:", rows.length);
  const hy3 = rows.filter((r) => String(r.id || "").includes("hy3"));
  for (const r of hy3) console.log("  " + JSON.stringify(r));
  console.log("  全部 id:", rows.map((r) => r.id).join(", "));
  // 有没有任何字段长得像价格
  const priceish = new Set();
  for (const r of rows) {
    for (const k of Object.keys(r)) {
      if (/price|cost|credit|fee|billing|quota/i.test(k)) priceish.add(k);
    }
  }
  console.log("  疑似价目字段:", priceish.size ? [...priceish].join(", ") : "（无）");
} catch (e) {
  console.log("  catalog 失败:", String(e).slice(0, 300));
}

// ── 2. 额度快照（调用前）───────────────────────────────────────────
async function credits(label) {
  const out = {};
  for (const name of ["fetchCredits", "fetchEnterpriseCredits"]) {
    try {
      const r = await client[name](cred);
      out[name] = r;
    } catch (e) {
      out[name] = { error: String(e).slice(0, 200) };
    }
  }
  console.log(`\n=== 2. 额度快照（${label}）===`);
  console.log("  " + JSON.stringify(out).slice(0, 1200));
  return out;
}

const before = await credits("调用前");

// ── 3. 打一次真实的 hy3（给足 max_tokens）─────────────────────────
console.log("\n=== 3. 打一次真实 hy3 ===");
const body = prepareChatBody(JSON.stringify({
  model: "hy3",
  messages: [{ role: "user", content: "用一句话回答：签收 9 天还能无理由退货吗？" }],
  stream: true,
  max_tokens: 3000,
}));
const ctrl = new AbortController();
const t = setTimeout(() => ctrl.abort(), 90_000);
let usage = null;
try {
  const r = await client.chatStream(cred, body, ctrl.signal);
  if (!r.ok) {
    console.log("  失败:", r.status, r.kind, String(r.message).slice(0, 300));
  } else {
    const text = await r.response.text();
    for (const line of text.split("\n")) {
      if (!line.startsWith("data: ")) continue;
      const p = line.slice(6).trim();
      if (p === "[DONE]") continue;
      try {
        const j = JSON.parse(p);
        if (j.usage) usage = j.usage;
      } catch { /* 半截行 */ }
    }
    console.log("  usage:", JSON.stringify(usage));
  }
} catch (e) {
  console.log("  异常:", String(e).slice(0, 300));
} finally {
  clearTimeout(t);
}

// ── 4. 额度快照（调用后）→ 差值就是这次调用的真实计价 ────────────────
const after = await credits("调用后");

console.log("\n=== 4. 结论 ===");
const b = before.fetchCredits, a = after.fetchCredits;
if (b && a && !b.error && !a.error) {
  const keys = new Set([...Object.keys(b || {}), ...Object.keys(a || {})]);
  let anyDiff = false;
  for (const k of keys) {
    if (typeof b[k] === "number" && typeof a[k] === "number" && a[k] !== b[k]) {
      console.log(`  ★ 额度变化 ${k}: ${b[k]} → ${a[k]}（差 ${a[k] - b[k]}）`);
      anyDiff = true;
    }
  }
  if (!anyDiff) console.log("  额度数值无变化 —— 本次调用没有扣额度");
  console.log("  before（全量）:", JSON.stringify(b).slice(0, 600));
  console.log("  after （全量）:", JSON.stringify(a).slice(0, 600));
} else {
  console.log("  额度接口不可用，无法据此判定计费；接口返回：",
    JSON.stringify({ before: b, after: a }).slice(0, 600));
}
