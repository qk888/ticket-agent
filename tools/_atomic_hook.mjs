// _atomic_hook.mjs — resolve hook：把 pnpm 空目录里的几个 @deepseek-ai/* peer 依赖
// 换成同目录下的 stub / 真实软链目标。
//
// 背景：dsh-workbuddy-connect 的 lib/host-heartbeat-<hash>.js 顶部
//   import { withFileLock, writeFileAtomic } from "@deepseek-ai/dsh-atomic-write"
//   ... 以及 @deepseek-ai/dsh-home-paths 等
// 在 pnpm 布局下这些包位于
//   .../node_modules/dsh-workbuddy-connect/node_modules/@deepseek-ai/<pkg>
// 但它们**是空目录**（没 package.json），Node 因此报 Cannot find package。
// 顶层 node_modules/@deepseek-ai/ 下也只有 cosmokit / schemastery，没有这几个。
//
// 本 hook 只拦这几个 specifier，其余一律 next()，不影响插件其他依赖。

import { existsSync } from "node:fs";
import { pathToFileURL } from "node:url";

//: 需要替换的 specifier → stub 文件（同目录）
const STUBS = {
  "@deepseek-ai/dsh-atomic-write": "./_atomic_stub.mjs",
};

export async function resolve(specifier, context, next) {
  if (specifier in STUBS) {
    return { url: new URL(STUBS[specifier], import.meta.url).href, shortCircuit: true };
  }
  // 其余先按默认解析；失败时报原始错误，不吞。
  return next(specifier, context);
}
