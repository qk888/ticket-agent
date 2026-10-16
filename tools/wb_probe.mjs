// 探针：复用 dsh-workbuddy-connect 的凭据解密 + 上游调用，真实打一次 hy3。
// 只读；输出不含 token 明文。
// 导出名被 minify，别名映射照抄插件自己的 lib/bin.js:2。
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
const parseWorkBuddyAuth = m.v;
const regionOf = m.I;
const chatUserAgent = m.H;

console.log("=== 别名导出确认 ===");
for (const [k, v] of Object.entries({
  "m WorkBuddyCredentialStore": WorkBuddyCredentialStore,
  "O WorkBuddyUpstreamClient": WorkBuddyUpstreamClient,
  "b atRestKeyProviderFor": atRestKeyProviderFor,
  "w variantFor": variantFor,
  "S CN_VARIANT": CN_VARIANT,
  "P prepareChatBody": prepareChatBody,
  "d WorkBuddyCatalog": WorkBuddyCatalog,
  "v parseWorkBuddyAuth": parseWorkBuddyAuth,
})) console.log(`  ${k}: ${typeof v}`);

const variant = variantFor("workbuddy") ?? CN_VARIANT;
console.log("\n=== variant ===");
console.log("  id:", variant.id, "| displayName:", variant.displayName, "| env:", variant.env, "| region:", variant.region);

const client = new WorkBuddyUpstreamClient();
const store = new WorkBuddyCredentialStore({
  variant,
  refresh: (credential) => client.refreshToken(credential),
  keyProvider: atRestKeyProviderFor(variant),
});

console.log("\n=== store ===");
console.log("  方法:", Object.getOwnPropertyNames(Object.getPrototypeOf(store)).join(", "));
console.log("  ownAuthPath:", store.ownAuthPath?.());
console.log("  resolvedDesktopAuthPath:", store.resolvedDesktopAuthPath?.());
console.log("  status:", JSON.stringify(await store.status()));

const cred = await store.resolve();
console.log("\n=== credential（脱敏）===");
for (const [k, v] of Object.entries(cred)) {
  if (typeof v === "string" && v.length > 24) console.log(`  ${k}: <len ${v.length}, ${v.slice(0, 6)}…>`);
  else console.log(`  ${k}: ${JSON.stringify(v)}`);
}

console.log("\n=== client 方法 ===");
console.log("  ", Object.getOwnPropertyNames(Object.getPrototypeOf(client)).join(", "));

// 模型目录
try {
  const cat = new WorkBuddyCatalog({ variant });
  const rows = cat.current?.() ?? [];
  console.log("\n=== 目录模型数:", rows.length, "===");
  const want = ["hy3", "hy3-x", "deepseek-v4.1-flash"];
  for (const r of rows) {
    if (!want.includes(r.id)) continue;
    console.log(`  ${JSON.stringify(r)}`);
  }
} catch (e) {
  console.log("  catalog 失败:", String(e).slice(0, 200));
}

// 真实调用 hy3
console.log("\n=== 真实调用 hy3（上游强制 stream）===");
const body = JSON.stringify({
  model: "hy3",
  messages: [
    { role: "system", content: "你是客服工单助手，回答极简。" },
    { role: "user", content: "用一句话说明：签收 9 天的无理由退货申请按 7 天期限应如何处理。" },
  ],
  stream: true,
  max_tokens: 200,
});
const prepared = prepareChatBody(body);
console.log("  prepared 顶层键:", Object.keys(JSON.parse(prepared)).join(", "));

const t0 = Date.now();
const ctrl = new AbortController();
const timeout = setTimeout(() => ctrl.abort(), 90_000);
try {
  const result = await client.chatStream(cred, prepared, ctrl.signal);
  console.log("  ok:", result.ok, "| status:", result.status, "| kind:", result.kind);
  if (!result.ok) {
    console.log("  message:", String(result.message).slice(0, 500));
  } else {
    const text = await result.response.text();
    console.log("  耗时:", Date.now() - t0, "ms | 原始 SSE 字节:", text.length);
    const lines = text.split("\n").filter((l) => l.startsWith("data: "));
    console.log("  SSE 事件数:", lines.length);
    let content = "";
    let reasoning = "";
    let usage = null;
    let id = null;
    for (const l of lines) {
      const payload = l.slice(6).trim();
      if (payload === "[DONE]") continue;
      let j;
      try { j = JSON.parse(payload); } catch { continue; }
      id ??= j.id;
      usage ??= j.usage;
      const d = j.choices?.[0]?.delta ?? {};
      if (d.content) content += d.content;
      if (d.reasoning_content) reasoning += d.reasoning_content;
    }
    console.log("  id:", id);
    console.log("  reasoning 字符数:", reasoning.length, "| content 字符数:", content.length);
    console.log("  usage:", JSON.stringify(usage));
    console.log("  ---- content ----");
    console.log(content.slice(0, 800));
    console.log("  ---- reasoning 前 300 ----");
    console.log(reasoning.slice(0, 300));
    console.log("\n  === 头两条 SSE 原文（看形状）===");
    console.log(lines.slice(0, 2).join("\n").slice(0, 600));
  }
} catch (e) {
  console.log("  异常:", String(e).slice(0, 500));
} finally {
  clearTimeout(timeout);
}
