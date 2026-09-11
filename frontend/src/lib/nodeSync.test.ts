import { describe, expect, it } from "vitest";
import { formatNodeSyncResult } from "./nodeSync";

describe("节点知识库同步结果", () => {
  it("报告单包失败但不把整批描述为中断", () => {
    expect(formatNodeSyncResult({
      running: false, done: 3, total: 3, current: "", synced: 1, skipped: 1,
      failed: 1, failures: ["bad-pack: timed out"], error: "", finished: true,
    })).toContain("失败 1 个：bad-pack: timed out");
  });
});
