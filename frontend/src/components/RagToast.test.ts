import { describe, expect, it } from "vitest";

import { ragStatusLabel } from "./RagToast";

describe("RagToast", () => {
  it("shows a clear first-time worldbook indexing notice", () => {
    expect(ragStatusLabel({ state: "start", kind: "worldbook", count: 53 }).text)
      .toBe("正在将世界书条目索引化（共 53 条），首次处理可能较慢，剧情生成将继续…");
  });
});
