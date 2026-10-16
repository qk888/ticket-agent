#!/usr/bin/env node
/**
 * _sidecar_boot.mjs — 用显式 import map 启动 wb_sidecar，绕开 peer 依赖解析问题。
 *
 * 为什么需要它（一次性脚手架，只为让 sidecar 起来）
 * -------------------------------------------------
 * `host-heartbeat-<hash>.js` 第 4 行 `import { withFileLock, writeFileAtomic }
 * from "@deepseek-ai/dsh-atomic-write"`。pnpm 布局下那个包在
 *   .../node_modules/dsh-workbuddy-connect/node_modules/@deepseek-ai/dsh-atomic-write
 * 但它**是个空目录**（没 package.json、没文件），于是 Node 报
 *   Cannot find package '@deepseek-ai/dsh-atomic-write'
 *
 * 本脚本的做法：注册一个 resolve hook，只拦这一个 specifier，
 * 换成本文件内的等价 stub（`writeFileAtomic` 用 rename 实现，语义一致）。
 *
 * 判据：`healthz` 返回 200 且 `model=hy3 state=signed-in`。
 */

import { register } from "node:module";
import { pathToFileURL } from "node:url";

const TARGET = "@deepseek-ai/dsh-atomic-write";

const STUB_URL = "file://" + process.cwd().replace(/\\/g, "/") + "/tools/_atomic_stub.mjs";

register(
  new URL("./_atomic_hook.mjs", import.meta.url).href,
  pathToFileURL(process.cwd() + "/tools/_sidecar_boot.mjs"),
);

await import("./wb_sidecar.mjs");
