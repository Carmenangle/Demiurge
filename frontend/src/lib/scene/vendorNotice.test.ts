// vendor 完整性锁：NOTICE.md 登记的 SHA-256 必须与磁盘上的 vendor 文件逐字节一致。
//
// 背景（2026-10-24 审计）：改动⑤⑥落地时更新了 NOTICE 的改动清单，但哈希段没跟着重算，
// 「上游可审计」的承诺静默失效。本锁让下一次脱节在 CI 直接红，而不是等下一次审计。
// EOL 注意：vendor 文件是纯 CRLF（见 NOTICE「再同步上游」），哈希按原始字节算，不做换行归一。
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { createHash } from "node:crypto";
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const VENDOR_FILES = ["we-scene-manifest.js", "we-scene-player-html.js"] as const;

describe("vendor NOTICE 哈希锁", () => {
  const notice = readFileSync(new URL("./vendor/NOTICE.md", import.meta.url), "utf8");

  it.each(VENDOR_FILES)("NOTICE 登记的 %s SHA-256 与文件一致", (name) => {
    const match = notice.match(new RegExp(`- \\\`${name}\\\` — \\\`([0-9a-f]{64})\\\``));
    expect(match, `NOTICE.md 缺少 ${name} 的 SHA-256 登记`).not.toBeNull();
    const actual = createHash("sha256")
      .update(readFileSync(new URL(`./vendor/${name}`, import.meta.url)))
      .digest("hex");
    expect(actual).toBe(match![1]);
  });
});
