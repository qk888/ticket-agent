#!/usr/bin/env node
/**
 * wb_sidecar.mjs — WorkBuddy（Hy3）通道的本地 OpenAI 兼容桥。
 *
 * 让 ticket-agent（以及任何 OpenAI 兼容客户端）能用上本机 WorkBuddy 里已登录的
 * hy3 —— 不需要 API key、不需要外网直连、不碰上游私有头。
 *
 * ## 为什么必须有这一层（三条实测理由，缺一条它就不必要）
 *
 * 1) **bearer 只在插件进程内存里**。插件的 shim（`createWorkBuddyShim`）每次启动
 *    随机生成 `SHARED_SECRET` 且从不落盘；外部客户端拿不到，无法直连它监听的
 *    随机端口（实测那些端口对无 bearer 的请求一律回 401）。本进程 `import` 插件的
 *    同一个模块实例，于是能通过 `shim.token()` 合法地调它。
 *
 * 2) **shim 只吐 SSE，从不聚合**。`prepareChatBody` 把 `stream` 强制成 true，
 *    上游 SSE 原样 `pipe` 出去；实测客户端传 `stream:false`、不传、传 `true`，
 *    三种情况都返回 `content-type: text/event-stream`（还以 `: heartbeat` 注释行
 *    开头）。而 ticket-agent 用 LangChain `ChatOpenAI`，默认非流式 —— 直连会
 *    解析失败。本层负责：客户端要非流式，就把 SSE 聚合成 `chat.completion` JSON。
 *
 * 3) **hy3 是 onlyReasoning 模型，思考 token 计入 max_tokens**。实测 `max_tokens=64`
 *    时 64 个 token 全被 `reasoning_content` 吃掉、`content` 是空串、`finish_reason`
 *    是 `length`；而 ticket-agent 的端点自检脚本会发 `max_tokens=16`。本层给
 *    `max_tokens` / `max_completion_tokens` 设一个下限，免得把「上限给小了」
 *    误读成「端点坏了」。
 *
 * ## 鉴权
 *
 * 本层自己也要 bearer，理由和 shim 相同：loopback 绑定不是信任边界 —— 任意本地
 * 进程、或一个 DNS-rebinding 页面，都能连 127.0.0.1。token 默认每次启动随机，
 * 写进 discovery 文件（0600）供 ticket-agent 读取；也可以用 `--token` 或
 * `WORKBUDDY_SIDECAR_TOKEN` 固定下来。
 *
 * ⚠️ token 落盘意味着「本地进程读不到密钥」这条防线在本层不成立 —— discovery
 * 文件本来就是给本地客户端读的。真正的边界是：**上游凭据（WorkBuddy 的
 * accessToken）始终只在本进程内存里，从不经过本层的 HTTP 面**。
 *
 * ## 用法
 *
 *   node tools/wb_sidecar.mjs                      # 起在 127.0.0.1:8787，打印 .env 片段
 *   node tools/wb_sidecar.mjs --port 9000          # 换端口
 *   node tools/wb_sidecar.mjs --model hy3-x        # 默认名册换成别的 WorkBuddy 模型
 *   node tools/wb_sidecar.mjs --token <固定值>      # 固定 bearer（.env 里可写死）
 *   node tools/wb_sidecar.mjs --no-discovery       # 不写 discovery 文件
 *
 * 起来之后 ticket-agent 侧：
 *
 *   LLM_PROFILE=hy3
 *   LLM_BASE_URL=http://127.0.0.1:8787/v1
 *   LLM_MODEL=hy3
 *   LLM_API_KEY=<启动时打印的 token>
 *   LLM_PRICE_IN_PER_1K_CNY=0.0
 *   LLM_PRICE_OUT_PER_1K_CNY=0.0
 *
 * 退出码：2 = WorkBuddy 未登录；3 = 端口占用；4 = 插件运行时加载失败。
 */

import { createServer } from "node:http";
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(HERE, "..");

// ────────────────────── 0. 读 .env（在参数解析之前）──────────────────────

/**
 * 只取本文件需要的那几个变量，**不覆盖**已存在的进程环境变量。
 *
 * ★ 为什么必须在 `parseArgs` 之前跑：`ARGS.token` 的默认值是从
 *   `process.env.WORKBUDDY_SIDECAR_TOKEN` 读的。若等到解析完再读 .env，
 *   那个默认值已经定格成空串，.env 里写的 token 就白写了 —— 结果一边
 *   随机、一边按 .env 填，客户端每次都 401，而两侧配置看上去完全一致。
 *
 * ★ 为什么手写而不引 dotenv：这是 tools/ 下的独立脚本，跑在 sidecar 进程里，
 *   不应该为了读 6 行配置给项目加一个运行期依赖。而且我们要的语义比 dotenv
 *   更窄（"别覆盖已有的"），手写反而更清楚。
 */
function loadDotEnv() {
  const path = resolve(PROJECT_ROOT, ".env");
  const empty = { path, loaded: 0, keys: new Set(), has: () => false };
  if (!existsSync(path)) return empty;
  let text;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    return empty;
  }
  let loaded = 0;
  const keys = new Set();
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) continue;
    let val = line.slice(eq + 1).trim();
    // 去掉成对的引号（.env 里 8787 这种值不会带引号，但 token 可能会）
    if (val.length >= 2 && ((val[0] === '"' && val.endsWith('"')) || (val[0] === "'" && val.endsWith("'")))) {
      val = val.slice(1, -1);
    }
    if (process.env[key] === undefined) {
      process.env[key] = val;
      loaded += 1;
    }
    keys.add(key); // 无论是否生效都记下"文件里写过这个键"
  }
  return { path, loaded, keys, has: (k) => keys.has(k) };
}

const DOTENV = loadDotEnv();

// ─────────────────────────── 1. 参数 ───────────────────────────

function parseArgs(argv) {
  const out = {
    port: 8787,
    token: process.env.WORKBUDDY_SIDECAR_TOKEN || "",
    // ★ token 的来源要记下来：客户端 401 时第一个要问的问题就是
    //   "两边用的是不是同一个 token"，而"我明明填了 .env"与"它真的读到了"
    //   是两件事。三档：命令行 / 环境变量（含 .env）/ 本次随机。
    tokenSource: process.env.WORKBUDDY_SIDECAR_TOKEN
      ? DOTENV.has("WORKBUDDY_SIDECAR_TOKEN")
        ? ".env"
        : "环境变量"
      : "本次随机",
    model: "hy3",
    minMaxTokens: 2048,
    variant: "workbuddy",
    sidecarFile: process.env.WORKBUDDY_SIDECAR_FILE || resolve(PROJECT_ROOT, ".workbuddy-sidecar.json"),
    discovery: true,
    help: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    const next = () => {
      const v = argv[i + 1];
      if (v === undefined) throw new Error(`参数 ${a} 缺少值`);
      i += 1;
      return v;
    };
    if (a === "--port") out.port = Number(next());
    else if (a === "--token") {
      out.token = next();
      out.tokenSource = "--token";
    } else if (a === "--model") out.model = next();
    else if (a === "--min-max-tokens") out.minMaxTokens = Number(next());
    else if (a === "--variant") out.variant = next();
    else if (a === "--sidecar-file") out.sidecarFile = next();
    else if (a === "--no-discovery") out.discovery = false;
    else if (a === "--help" || a === "-h") out.help = true;
    else throw new Error(`未知参数 ${a}`);
  }
  if (!Number.isInteger(out.port) || out.port < 1 || out.port > 65535) {
    throw new Error(`--port 需要是 1..65535 的整数，收到 ${out.port}`);
  }
  if (!Number.isInteger(out.minMaxTokens) || out.minMaxTokens < 0) {
    throw new Error(`--min-max-tokens 需要是非负整数，收到 ${out.minMaxTokens}`);
  }
  return out;
}

let ARGS;
try {
  ARGS = parseArgs(process.argv.slice(2));
} catch (error) {
  console.error(`[sidecar] 参数错误：${error.message}`);
  process.exit(1);
}

if (ARGS.help) {
  console.log(`用法：node tools/wb_sidecar.mjs [选项]

  --port <n>             监听端口（默认 8787）
  --token <v>            固定 bearer；缺省则每次启动随机
  --model <id>           /v1/models 暴露的默认模型名（默认 hy3）；实际模型由客户端请求体里的 model 决定
  --min-max-tokens <n>   max_tokens / max_completion_tokens 的下限（默认 2048，0 = 不干预）
  --variant <id>         WorkBuddy 变体（workbuddy | workbuddy-ai，默认 workbuddy）
  --sidecar-file <path>  discovery 文件路径
  --no-discovery         不写 discovery 文件
  -h, --help             显示本帮助

环境变量：WORKBUDDY_PLUGIN_DIR / WORKBUDDY_SIDECAR_TOKEN / WORKBUDDY_SIDECAR_FILE`);
  process.exit(0);
}

const PLUGIN_DIR =
  process.env.WORKBUDDY_PLUGIN_DIR ||
  "C:/Users/Administrator/.dsh/profiles/desktop/node_modules/dsh-workbuddy-connect";
const LIB_DIR = resolve(PLUGIN_DIR, "lib");

function log(...parts) {
  console.log(`[sidecar ${new Date().toISOString().slice(11, 19)}]`, ...parts);
}
function warn(...parts) {
  console.warn(`[sidecar ${new Date().toISOString().slice(11, 19)}] ⚠`, ...parts);
}

// ─────────────────── 2. 加载插件运行时 ───────────────────

/**
 * `lib/index.js` 导出干净名字（createWorkBuddyShim / WorkBuddyCredentialStore /
 * WorkBuddyUpstreamClient），但**不导出** `variantFor` / `atRestKeyProviderFor`
 * —— 那两个在 `lib/host-heartbeat-<hash>.js` 里，且导出名被 minify 成了单字母。
 * 这里连同 build 里的 hash 一起在运行时发现，避免把 `-L93zSVB5` 钉死在代码里
 * （插件一升级文件名就变）。
 */
function findHostHeartbeat() {
  let names;
  try {
    names = readdirSync(LIB_DIR);
  } catch (error) {
    throw new Error(`读不到插件 lib 目录 ${LIB_DIR}：${error.message}`);
  }
  const hit = names.filter((n) => /^host-heartbeat-.*\.js$/.test(n)).sort();
  if (hit.length === 0) throw new Error(`${LIB_DIR} 里没有 host-heartbeat-*.js`);
  if (hit.length > 1) {
    warn(`发现多个 host-heartbeat 产物，取最后一个：${hit.join(", ")}`);
  }
  return resolve(LIB_DIR, hit[hit.length - 1]);
}

const HB_PATH = findHostHeartbeat();

let pluginIndex;
let hb;
try {
  pluginIndex = await import(pathToFileURL(resolve(LIB_DIR, "index.js")).href);
  hb = await import(pathToFileURL(HB_PATH).href);
} catch (error) {
  console.error(`[sidecar] 加载插件运行时失败：${error.message}`);
  console.error(
    `[sidecar] 提示：插件目录树里若缺 peer 依赖（@deepseek-ai/dsh-atomic-write 等），` +
      `需要在 ${PLUGIN_DIR}/node_modules/ 下把它们链进来。`,
  );
  process.exit(4);
}

/** 别名映射照抄插件自己的 `lib/bin.js:2`。 */
const ALIAS = {
  store: "m",
  client: "O",
  keyProvider: "b",
  variantFor: "w",
  cnVariant: "S",
};
for (const [what, key] of Object.entries(ALIAS)) {
  if (hb[key] === undefined) {
    console.error(`[sidecar] host-heartbeat 里找不到别名 ${key}（期望是 ${what}）—— 插件版本可能变了。`);
    process.exit(4);
  }
}
if (typeof pluginIndex.createWorkBuddyShim !== "function") {
  console.error("[sidecar] lib/index.js 没导出 createWorkBuddyShim —— 插件版本可能变了。");
  process.exit(4);
}

const { createWorkBuddyShim } = pluginIndex;
const UpstreamClient = pluginIndex.WorkBuddyUpstreamClient ?? hb[ALIAS.client];

// ─────────────── 3. 凭据与 shim ───────────────

const variant = hb[ALIAS.variantFor](ARGS.variant) ?? hb[ALIAS.cnVariant];
if (variant === undefined) {
  console.error(`[sidecar] 认不出变体 ${ARGS.variant}`);
  process.exit(4);
}

const client = new UpstreamClient();
const store = new hb[ALIAS.store]({
  variant,
  refresh: (credential) => client.refreshToken(credential),
  keyProvider: hb[ALIAS.keyProvider](variant),
});

let status;
try {
  status = await store.status();
} catch (error) {
  console.error(`[sidecar] 读凭据状态失败：${error.message}`);
  process.exit(2);
}
if (status.state !== "signed-in") {
  console.error(`[sidecar] WorkBuddy 未登录（state=${status.state}）。`);
  console.error("[sidecar] 请先在 WorkBuddy 桌面端登录，然后重试。登录态文件：");
  try {
    console.error(`[sidecar]   ${await store.resolvedDesktopAuthPath()}`);
  } catch {
    /* 路径拿不到就算了，上面那句已经说明问题了 */
  }
  process.exit(2);
}
log(`WorkBuddy 已登录：${variant.displayName}（domain=${status.domain}，凭据来源=${status.source}）`);
if (status.expiresAtMs !== undefined) {
  log(`access token 到期时间：${new Date(status.expiresAtMs).toISOString()}`);
}

/**
 * shim 只需要 `catalog.current()` 返回数组（它自己 `.map()` 成 OpenAI 的
 * `/v1/models` 形状）。这里给一个只列默认模型名的桩，而不是去构造插件的
 * `WorkBuddyCatalog`：本层不关心完整名册（模型名由客户端请求体决定，透传即可），
 * 也就不必依赖 `FALLBACK_WORKBUDDY_MODELS` 这类内部常量。
 */
const catalogStub = {
  current: () => [{ id: ARGS.model, name: ARGS.model }],
};

const shim = createWorkBuddyShim({
  store,
  client,
  catalog: catalogStub,
  logger: { warn: (...a) => warn(...a), error: (...a) => warn(...a) },
});
try {
  await shim.ready;
} catch (error) {
  console.error(`[sidecar] 起 shim 失败：${error.message}`);
  process.exit(4);
}
const SHIM_BASE = shim.baseUrl();
const SHIM_TOKEN = shim.token();
log(`上游 shim 就绪：${SHIM_BASE}`);

// ─────────────── 4. SSE → OpenAI JSON 聚合器 ───────────────

/**
 * 把 shim 透传的上游 SSE 聚合成一个 `chat.completion` 对象。
 *
 * 形状依据是实测的原始 chunk（`tools/wb_sse_probe.mjs`），不是猜的：
 *
 *   - 每个 chunk 是 `choices[0].delta`；**非工具 chunk 也带 `tool_calls: []`**（空数组）。
 *   - 思考走 `delta.reasoning_content`（逐字碎片），正文走 `delta.content`。
 *   - 工具调用分片：**首片**带 `id` + `function.name` + 空 `arguments`，
 *     **后续片**只有 `function.arguments` 碎片，且 `function.name` 是空字符串。
 *     所以拼参数时「空 name 不覆盖已有 name」是必须的，否则名字会被抹掉。
 *   - `finish_reason` 与 `usage` 都落在**最后一个** chunk 上（可能同一片，也可能
 *     分别出现）；`usage` 带 `prompt_tokens` / `completion_tokens` /
 *     `completion_tokens_details.reasoning_tokens` / `credit`。
 *   - 正文以 `: heartbeat` 注释行开头，末尾是 `data: [DONE]`。
 */
function aggregateSse(rawText) {
  let id = "";
  let model = "";
  let created = 0;
  let content = "";
  let reasoning = "";
  let finish = null;
  let usage = null;
  /** @type {Map<number, {id: string, type: string, name: string, args: string}>} */
  const slots = new Map();

  for (const rawLine of rawText.split("\n")) {
    const line = rawLine.replace(/\r$/, "");
    if (!line.startsWith("data:")) continue; // 跳过 ": heartbeat" 等注释行
    const payload = line.slice(5).trim();
    if (payload === "" || payload === "[DONE]") continue;
    let chunk;
    try {
      chunk = JSON.parse(payload);
    } catch {
      warn(`SSE 里有一行不是合法 JSON，已跳过：${payload.slice(0, 120)}`);
      continue;
    }
    if (typeof chunk.id === "string" && chunk.id) id = chunk.id;
    if (typeof chunk.model === "string" && chunk.model) model = chunk.model;
    if (typeof chunk.created === "number" && chunk.created) created = chunk.created;
    if (chunk.usage) usage = chunk.usage;

    const choice = Array.isArray(chunk.choices) ? chunk.choices[0] : undefined;
    if (!choice) continue;
    if (choice.finish_reason) finish = choice.finish_reason;
    const delta = choice.delta ?? {};
    if (typeof delta.content === "string") content += delta.content;
    if (typeof delta.reasoning_content === "string") reasoning += delta.reasoning_content;

    if (Array.isArray(delta.tool_calls)) {
      for (const tc of delta.tool_calls) {
        const idx = typeof tc.index === "number" ? tc.index : 0;
        let slot = slots.get(idx);
        if (slot === undefined) {
          slot = { id: "", type: "function", name: "", args: "" };
          slots.set(idx, slot);
        }
        if (typeof tc.id === "string" && tc.id) slot.id = tc.id;
        if (typeof tc.type === "string" && tc.type) slot.type = tc.type;
        const fn = tc.function ?? {};
        // ★ 空 name 不覆盖：后续分片会带 name:""，直接赋值会把首片的名字抹掉。
        if (typeof fn.name === "string" && fn.name !== "") slot.name = fn.name;
        if (typeof fn.arguments === "string") slot.args += fn.arguments;
      }
    }
  }

  const toolCalls = [...slots.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([idx, s]) => {
      const name = s.name || `anonymous_tool_${idx}`;
      if (s.name === "") warn(`工具调用 #${idx} 从头到尾没拿到函数名 —— 上游分片可能变了。`);
      try {
        JSON.parse(s.args === "" ? "{}" : s.args);
      } catch {
        warn(`工具调用 #${idx}（${name}）的参数不是合法 JSON，原样透传交由上层报错：${s.args.slice(0, 200)}`);
      }
      return {
        id: s.id || `call_${idx}`,
        type: s.type || "function",
        function: { name, arguments: s.args },
      };
    });

  const message = { role: "assistant", content };
  if (reasoning !== "") message.reasoning_content = reasoning;
  if (toolCalls.length > 0) message.tool_calls = toolCalls;

  const response = {
    id: id || `chatcmpl-sidecar-${randomBytes(8).toString("hex")}`,
    object: "chat.completion",
    created: created || Math.floor(Date.now() / 1000),
    model: model || ARGS.model,
    choices: [
      {
        index: 0,
        message,
        // 兜底：上游万一只给了 [DONE] 没给 finish_reason，也别让上层看到 null。
        finish_reason: finish ?? (toolCalls.length > 0 ? "tool_calls" : "stop"),
      },
    ],
  };
  if (usage) response.usage = usage;
  return { response, toolCallCount: toolCalls.length, reasoningChars: reasoning.length };
}

// ─────────────── 5. HTTP 面 ───────────────

const TOKEN = ARGS.token || randomBytes(32).toString("base64url");
const BODY_LIMIT = 64 * 1024 * 1024;

function readBody(req) {
  return new Promise((res, rej) => {
    const chunks = [];
    let size = 0;
    req.on("data", (c) => {
      size += c.length;
      if (size > BODY_LIMIT) {
        rej(new Error("请求体过大"));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => res(Buffer.concat(chunks)));
    req.on("error", rej);
  });
}

function sendJson(res, status, body) {
  const text = JSON.stringify(body);
  res.writeHead(status, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(text) });
  res.end(text);
}

function sendOpenAiError(res, status, kind, message) {
  sendJson(res, status, { error: { message, type: kind, code: kind } });
}

/** 与 shim 同样的两道反 DNS-rebinding 校验（Host 必须 loopback；带了 Origin 就必须是 loopback）。 */
function hostOk(host) {
  if (typeof host !== "string" || host.trim() === "") return false;
  let h = host.trim().toLowerCase();
  if (h.startsWith("[")) h = h.slice(1, h.indexOf("]"));
  else h = h.split(":")[0];
  return h === "127.0.0.1" || h === "localhost" || h === "::1" || h === "0.0.0.0";
}
function originOk(origin) {
  if (typeof origin !== "string" || origin.trim() === "") return true;
  try {
    const { hostname } = new URL(origin);
    return hostname === "127.0.0.1" || hostname === "localhost" || hostname === "::1";
  } catch {
    return false;
  }
}
function bearerOk(req) {
  const h = req.headers.authorization;
  if (typeof h !== "string") return false;
  const m = /^Bearer\s+(.+)$/i.exec(h.trim());
  return m !== null && m[1] === TOKEN;
}

/**
 * 把下游请求体整理成要转发给 shim 的形状。
 *
 * 只做一件事：**给输出上限设一个下限**。其余原样转发 —— 强制 stream、
 * `developer → system` 改写、`tool_choice` 拍平这些活都由 shim 的
 * `prepareChatBody` 做，本层重复一遍只会制造两个会各自漂移的实现。
 */
function prepareBody(raw) {
  let obj;
  try {
    obj = JSON.parse(raw);
  } catch (error) {
    return { error: `请求体不是合法 JSON：${error.message}` };
  }
  if (obj === null || typeof obj !== "object" || Array.isArray(obj)) {
    return { error: "请求体必须是 JSON 对象" };
  }
  const bumps = [];
  if (ARGS.minMaxTokens > 0) {
    for (const key of ["max_tokens", "max_completion_tokens"]) {
      const current = obj[key];
      if (current === undefined || current === null) continue; // 不传就是上游默认，实测够用
      if (typeof current === "number" && current < ARGS.minMaxTokens) {
        obj[key] = ARGS.minMaxTokens;
        bumps.push(`${key} ${current} → ${ARGS.minMaxTokens}`);
      }
    }
  }
  return { text: JSON.stringify(obj), bumps, wantedModel: obj.model, wantsStream: obj.stream === true };
}

async function forwardToShim(path, payload, signal) {
  return fetch(`${SHIM_BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${SHIM_TOKEN}`,
    },
    body: payload,
    signal,
  });
}

async function handleChat(req, res) {
  const controller = new AbortController();
  // ★ 不能用 req.on("close")：IncomingMessage 是 Readable，body 读完就自动 destroy，
  //   于是 close 会在请求刚读完时立刻触发，把自己发往 shim 的 fetch 当场 abort 掉
  //   （实测报错是 `连不上 shim：This operation was aborted`，6ms 就 502）。
  //   真正的「客户端断线」判据是：响应还没写完，连接就关了。
  res.on("close", () => {
    if (!res.writableEnded) controller.abort();
  });

  let raw;
  try {
    raw = (await readBody(req)).toString("utf8");
  } catch (error) {
    sendOpenAiError(res, 413, "payload_too_large", String(error.message));
    return;
  }
  const prepared = prepareBody(raw);
  if (prepared.error !== undefined) {
    sendOpenAiError(res, 400, "invalid_request_error", prepared.error);
    return;
  }
  if (prepared.bumps.length > 0) {
    log(`抬高输出上限（hy3 会把思考算进 max_tokens，给小了正文必空）：${prepared.bumps.join("；")}`);
  }

  const started = Date.now();
  let upstream;
  try {
    upstream = await forwardToShim("/v1/chat/completions", prepared.text, controller.signal);
  } catch (error) {
    sendOpenAiError(res, 502, "sidecar_upstream_unreachable", `连不上 shim：${error.message}`);
    return;
  }
  const ct = upstream.headers.get("content-type") ?? "";
  if (!upstream.ok || !ct.includes("text/event-stream")) {
    // shim 用 JSON 报错（401 not_signed_in / 402 hard_credit / 429 soft_rate …），原样转发。
    const text = await upstream.text();
    res.writeHead(upstream.status, { "Content-Type": "application/json" });
    res.end(text);
    log(`✗ ${prepared.wantedModel ?? "?"} HTTP ${upstream.status}（${Date.now() - started}ms）`);
    return;
  }

  if (prepared.wantsStream) {
    // 客户端要流式：原样透传（上游 SSE 已是 OpenAI 形状，reasoning_content 是附加字段）。
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    });
    const reader = upstream.body.getReader();
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (!res.write(Buffer.from(value))) await new Promise((r) => res.once("drain", r));
      }
    } catch (error) {
      warn(`透传 SSE 中断：${error.message}`);
    }
    res.end();
    log(`→ ${prepared.wantedModel ?? "?"} 流式（${Date.now() - started}ms）`);
    return;
  }

  // 客户端要非流式：读完整段 SSE 聚合成 chat.completion。
  const sseText = await upstream.text();
  const { response, toolCallCount, reasoningChars } = aggregateSse(sseText);
  const usage = response.usage ?? {};
  log(
    `→ ${response.model} 非流式（${Date.now() - started}ms）` +
      ` prompt=${usage.prompt_tokens ?? "?"} completion=${usage.completion_tokens ?? "?"}` +
      ` 思考=${usage.completion_tokens_details?.reasoning_tokens ?? reasoningChars}字` +
      ` 正文=${(response.choices[0].message.content ?? "").length}字` +
      ` 工具调用=${toolCallCount}` +
      (usage.credit !== undefined ? ` credit=${usage.credit}` : ""),
  );
  sendJson(res, 200, response);
}

const server = createServer(async (req, res) => {
  try {
    if (!hostOk(req.headers.host)) {
      sendOpenAiError(res, 403, "host_not_allowed", "Host 必须是 loopback");
      return;
    }
    if (!originOk(req.headers.origin)) {
      sendOpenAiError(res, 403, "origin_not_allowed", "Origin 必须是 loopback");
      return;
    }
    if (!bearerOk(req)) {
      sendOpenAiError(res, 401, "unauthorized", "缺少或错误的 Authorization bearer");
      return;
    }
    const url = (req.url ?? "/").split("?")[0];
    if (req.method === "GET" && (url === "/healthz" || url === "/healthz/")) {
      let st = { state: "unknown" };
      try {
        st = await store.status();
      } catch (error) {
        st = { state: "error", message: String(error.message) };
      }
      sendJson(res, 200, {
        ok: st.state === "signed-in",
        sidecar: "wb_sidecar",
        pid: process.pid,
        baseUrl: `http://127.0.0.1:${ARGS.port}/v1`,
        model: ARGS.model,
        minMaxTokens: ARGS.minMaxTokens,
        // ★ 让"这个 token 是哪来的"可查：客户端 401 时第一个要问的问题就是
        //   "两边用的是不是同一个 token"，而"我明明填了 .env"与"它真的读到了"
        //   是两件事。只报来源与是否命中，不报值。
        tokenSource: ARGS.tokenSource,
        dotenv: { path: DOTENV.path, keysApplied: DOTENV.loaded },
        workbuddy: { state: st.state, domain: st.domain, variant: variant.id },
      });
      return;
    }
    if (req.method === "GET" && (url === "/v1/models" || url === "/v1/models/")) {
      const models = catalogStub.current().map((m) => ({
        id: m.id,
        object: "model",
        created: 0,
        owned_by: "workbuddy",
      }));
      sendJson(res, 200, { object: "list", data: models });
      return;
    }
    if (req.method === "POST" && (url === "/v1/chat/completions" || url === "/v1/chat/completions/")) {
      await handleChat(req, res);
      return;
    }
    sendOpenAiError(res, 404, "not_found", `没有这个路由：${req.method} ${url}`);
  } catch (error) {
    if (!res.headersSent) sendOpenAiError(res, 500, "internal", String(error));
    else res.end();
  }
});

// ─────────────── 6. 启动 ───────────────

const SIDECAR_URL = `http://127.0.0.1:${ARGS.port}/v1`;

let discoveryWritten = false;
function writeDiscovery() {
  if (!ARGS.discovery) return;
  try {
    mkdirSync(dirname(ARGS.sidecarFile), { recursive: true });
    writeFileSync(
      ARGS.sidecarFile,
      `${JSON.stringify(
        {
          version: 1,
          kind: "workbuddy-sidecar",
          pid: process.pid,
          port: ARGS.port,
          baseUrl: SIDECAR_URL,
          apiKey: TOKEN,
          model: ARGS.model,
          variant: variant.id,
          upstream: "workbuddy",
          shimBaseUrl: SHIM_BASE,
          startedAtMs: Date.now(),
        },
        null,
        2,
      )}\n`,
      { mode: 0o600 },
    );
    discoveryWritten = true;
  } catch (error) {
    warn(`写 discovery 文件失败（不影响服务）：${error.message}`);
  }
}

function shutdown(code) {
  if (discoveryWritten) {
    try {
      rmSync(ARGS.sidecarFile, { force: true });
    } catch {
      /* 删不掉就算了，文件里的 pid 足以让人判断它是不是活的 */
    }
  }
  server.close();
  server.closeAllConnections?.();
  Promise.resolve(shim.close()).finally(() => process.exit(code));
}

server.on("error", (error) => {
  if (error.code === "EADDRINUSE") {
    console.error(`[sidecar] 端口 ${ARGS.port} 已被占用。换一个：node tools/wb_sidecar.mjs --port 8788`);
    process.exit(3);
  }
  console.error(`[sidecar] 监听失败：${error.message}`);
  process.exit(3);
});

for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    log("收到退出信号，清理中…");
    shutdown(0);
  });
}

server.listen(ARGS.port, "127.0.0.1", () => {
  process.stdout.write(`
╭─ WorkBuddy (hy3) sidecar ──────────────────────────────────────────────╮
│  OpenAI 兼容端点   ${SIDECAR_URL.padEnd(52)}│
│  模型（默认名册）  ${ARGS.model.padEnd(52)}│
│  bearer            ${(TOKEN.length > 40 ? `${TOKEN.slice(0, 18)}…（共 ${TOKEN.length} 字符）` : TOKEN).padEnd(52)}│
│  token 来源        ${ARGS.tokenSource.padEnd(52)}│
│  .env              ${`${DOTENV.path}（读入 ${DOTENV.loaded} 项）`.padEnd(52)}│
│  上游 shim         ${SHIM_BASE.padEnd(52)}│
│  登录态            ${String(`${status.state} · ${status.domain}`).padEnd(52)}│
${ARGS.discovery ? `│  discovery 文件    ${ARGS.sidecarFile.padEnd(52)}│\n` : ""}╰────────────────────────────────────────────────────────────────────────╯

贴进 ticket-agent/.env —— 档案 hy3 已内置，所以只需两行：

  LLM_PROFILE=hy3
  WORKBUDDY_SIDECAR_TOKEN=${TOKEN}

（token 会写进 ${ARGS.sidecarFile.split("\\").pop()}；
  下次起 sidecar 时它会先读 .env 里的 WORKBUDDY_SIDECAR_TOKEN，
  所以这两行填一次就固定下来了，不用每次抄新 token。）

不选档案、走老路径的话是这套：

  LLM_BASE_URL=${SIDECAR_URL}
  LLM_MODEL=${ARGS.model}
  LLM_API_KEY=${TOKEN}
  LLM_PRICE_IN_PER_1K_CNY=0.0
  LLM_PRICE_OUT_PER_1K_CNY=0.0

自检：curl -s -H "Authorization: Bearer ${TOKEN}" ${SIDECAR_URL.replace("/v1", "")}/healthz
      cd ticket-agent && python -m ticket_agent.cli endpoint --profile hy3 --check
`);
  writeDiscovery();
  log("就绪。Ctrl-C 退出。");
});
