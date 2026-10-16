/**并发耐受探针：同一时刻打 N 个请求，看延迟会不会劣化。
 *
 * 为什么需要它
 * ------------
 * 并行分片的加速比是「接近线性，直到模型服务端饱和」。hy3 由 WorkBuddy
 * 插件在本机服务，我们**看不到它的并发上限**，所以只能实测：
 *   4 路延迟 ≈ 1 路延迟  → 远没饱和，加片有效
 *   16 路延迟 ≈ 4 路 × 4  → 已经饱和，加片只会让每片都变慢
 *
 * 用法：node tools/_concprobe.mjs [4 8 16]
 */

import process from "node:process";

const PORT = process.env.SIDECAR_PORT || "8787";
const URL = `http://127.0.0.1:${PORT}/v1/chat/completions`;
const TOKEN =
  process.env.WORKBUDDY_SIDECAR_TOKEN ||
  "gR0J6JCE84RZSi_GJkhWVwFignXI2i38WJ5Xx7_V3sM";

const SIZES = process.argv.slice(2).map(Number).filter((n) => n > 0);
const PLAN = SIZES.length ? SIZES : [4, 8, 16];

async function one(i) {
  const t0 = Date.now();
  const r = await fetch(URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${TOKEN}`,
    },
    body: JSON.stringify({
      model: "hy3",
      // ★ 只发一句最短问句 + 极小 max_tokens：量的是**排队与调度**，
      //   不是生成速度。thinking token 会吃掉 max_tokens，所以给 64 兜底。
      max_tokens: 64,
      messages: [{ role: "user", content: `说"好"一个字。序号${i}` }],
    }),
  });
  const ms = Date.now() - t0;
  if (!r.ok) return { i, ms, err: `HTTP ${r.status}` };
  const j = await r.json().catch(() => null);
  const usage = j && j.usage ? j.usage : {};
  return { i, ms, err: "", out: usage.completion_tokens ?? -1 };
}

for (const n of PLAN) {
  const t0 = Date.now();
  const rs = await Promise.all(Array.from({ length: n }, (_, i) => one(i)));
  const wall = Date.now() - t0;
  const lat = rs.map((r) => r.ms).sort((a, b) => a - b);
  const bad = rs.filter((r) => r.err);
  const mid = lat[Math.floor(lat.length / 2)] ?? -1;
  console.log(
    `并发 ${String(n).padStart(2)}｜墙钟 ${String(wall).padStart(6)}ms｜` +
      `中位 ${String(mid).padStart(6)}ms｜最快 ${lat[0]}ms｜最慢 ${lat[lat.length - 1]}ms｜` +
      `失败 ${bad.length}${bad.length ? `（${bad[0].err}）` : ""}`
  );
}
