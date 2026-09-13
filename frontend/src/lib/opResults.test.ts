import { describe, it, expect } from "vitest";
import { fmtOpResults } from "./opResults";

describe("fmtOpResults", () => {
  it("成功项：✓ #id input，无括注", () => {
    expect(fmtOpResults([{ ok: true, node_id: "5", input: "steps" }]))
      .toBe("✓ #5 steps");
  });
  it("失败项：✗ 带失败原因括注", () => {
    expect(fmtOpResults([{ ok: false, node_id: "7", input: "image", msg: "无图" }]))
      .toBe("✗ #7 image（无图）");
  });
  it("失败缺 msg 用默认「失败」", () => {
    expect(fmtOpResults([{ ok: false, node_id: "7", input: "" }]))
      .toBe("✗ #7 （失败）");
  });
  it("多项换行拼接", () => {
    expect(fmtOpResults([
      { ok: true, node_id: "1", input: "a" },
      { ok: false, node_id: "2", input: "b", msg: "x" },
    ])).toBe("✓ #1 a\n✗ #2 b（x）");
  });
  it("空数组返回空串", () => {
    expect(fmtOpResults([])).toBe("");
  });
});
