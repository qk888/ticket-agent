/**问 sidecar 要模型清单，并按计费标记分组。
 *
 * 为什么需要它
 * ------------
 * 「多模型并行跑」的前提是知道有哪些模型**以及哪些不花钱**。
 * hy3 档案背后是 WorkBuddy 名册的 16 个模型，价差真实存在：
 * `hy3` 标 `billing.free=true`，`hy3-x` 标 `x0.05 credits / free:false`
 * （见 config.py 里那个档案的注释）。把收费模型混进并行跑批，
 * 一跑就扣额度，而报表的成本列还按 0 算 —— 与缺陷 ⑭ 同族。
 *
 * 用法：node tools/_modelprobe.mjs
 */

import process from "node:process";

const PORT = process.env.SIDECAR_PORT || "8787";
const BASE = `http://127.0.0.1:${PORT}/v1`;
const TOKEN =
  process.env.WORKBUDDY_SIDECAR_TOKEN ||
  "gR0J6JCE84RZSi_GJkhWVwFignXI2i38WJ5Xx7_V3sM";

async function main() {
  const r = await fetch(`${BASE}/models`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  if (!r.ok) {
    console.log(`✗ HTTP ${r.status}: ${await r.text()}`);
    process.exitCode = 1;
    return;
  }
  const j = await r.json();
  const list = Array.isArray(j.data) ? j.data : [];

  const rows = list.map((m) => {
    const b = m.billing ?? {};
    return {
      id: String(m.id ?? "?"),
      name: String(m.name ?? ""),
      free: b.free === true,
      credits: String(b.credits ?? "?"),
      onlyReasoning: m.onlyReasoning === true,
      canDisableThinking: m.canDisableThinking !== false,
    };
  });

  console.log(`共 ${rows.length} 个模型\n`);
  const free = rows.filter((x) => x.free);
  const paid = rows.filter((x) => !x.free);
  const unknown = rows.filter((x) => x.credits === "?" && x.free === false);

  console.log("── 免费（可并行，不扣额度）──");
  for (const x of free) {
    console.log(
      `  ${x.id.padEnd(22)} credits=${x.credits.padEnd(10)}` +
        ` reasoning-only=${x.onlyReasoning}`
    );
  }
  console.log("\n── 明确收费（避开，一跑就扣）──");
  for (const x of paid) {
    console.log(
      `  ${x.id.padEnd(22)} credits=${x.credits.padEnd(10)}` +
        ` reasoning-only=${x.onlyReasoning}`
    );
  }
  if (unknown.length) {
    console.log("\n── 计费未知（按收费对待）──");
    for (const x of unknown) console.log(`  ${x.id}`);
  }

  // 只有免费模型 > 1 个，「多模型并行」才有意义（否则只是换模型重跑）。
  console.log(
    `\n⇒ 免费模型 ${free.length} 个：` +
      (free.length > 1
        ? "多模型并行可行（每个模型一个 _prun.py，各自独立进度/库）"
        : "只有一个免费模型，多模型并行无收益；建议缩 --size")
  );
}

main().catch((e) => {
  console.log(`✗ ${e.message}`);
  process.exitCode = 1;
});
