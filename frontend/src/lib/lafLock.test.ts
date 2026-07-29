import { describe, it, expect } from "vitest";
import { lockUrl, isLafMessage, isLafMessageFrom } from "./lafLock";

describe("lockUrl", () => {
  it("去尾斜杠后拼 ?laf_lock=1", () => {
    expect(lockUrl("http://127.0.0.1:8188")).toBe("http://127.0.0.1:8188/?laf_lock=1");
    expect(lockUrl("http://127.0.0.1:8188/")).toBe("http://127.0.0.1:8188/?laf_lock=1");
  });
});

describe("isLafMessage", () => {
  it("仅认 source==laf_lock", () => {
    expect(isLafMessage({ source: "laf_lock", type: "ready" })).toBe(true);
    expect(isLafMessage({ source: "other", type: "ready" })).toBe(false);
    expect(isLafMessage(null)).toBe(false);
    expect(isLafMessage(undefined)).toBe(false);
  });
  it("给 type 时同时校验类型", () => {
    expect(isLafMessage({ source: "laf_lock", type: "loaded" }, "loaded")).toBe(true);
    expect(isLafMessage({ source: "laf_lock", type: "ready" }, "loaded")).toBe(false);
  });
});

describe("isLafMessageFrom", () => {
  it("同时校验目标 frame 来源", () => {
    const frame = {} as Window;
    const other = {} as Window;
    const data = { source: "laf_lock", type: "ready" };
    expect(isLafMessageFrom({ source: frame, data } as MessageEvent, frame, "ready")).toBe(true);
    expect(isLafMessageFrom({ source: other, data } as MessageEvent, frame, "ready")).toBe(false);
  });
});
