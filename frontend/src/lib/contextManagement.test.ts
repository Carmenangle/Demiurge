import { describe, expect, it } from "vitest";
import {
  clampChatInputHeight,
  clampSelectionScroll,
  compactedChatMessage,
  contextTokenEstimate,
  estimateTextTokens,
  loadChatInputHeight,
  nextContextReminderBucket,
  recoverCompactedSummaryImage,
} from "./contextManagement";

describe("context management", () => {
  it("reminds once at every 12000 estimated tokens", () => {
    expect(nextContextReminderBucket(11_999, 0)).toBeNull();
    expect(nextContextReminderBucket(12_000, 0)).toBe(1);
    expect(nextContextReminderBucket(23_999, 1)).toBeNull();
    expect(nextContextReminderBucket(24_000, 1)).toBe(2);
  });

  it("estimates mixed Chinese and English text without counting empty media projections", () => {
    expect(estimateTextTokens("测试abcd")).toBe(3);
    expect(contextTokenEstimate([
      { role: "user", text: "继续" },
      { role: "assistant", text: "done" },
      { role: "assistant", text: "", image: "asset.png" },
    ])).toBe(11);
  });

  it("limits textarea selection auto-scroll jumps", () => {
    expect(clampSelectionScroll(100, 300, 16)).toBeLessThanOrEqual(108);
    expect(clampSelectionScroll(100, 104, 16)).toBe(104);
    expect(clampSelectionScroll(300, 100, 16)).toBeGreaterThanOrEqual(292);
  });

  it("uses 100px as the saved input height default and clamps invalid values", () => {
    const empty = { getItem: () => null };
    const saved = { getItem: () => "180" };
    expect(loadChatInputHeight(empty)).toBe(100);
    expect(loadChatInputHeight(saved)).toBe(180);
    expect(clampChatInputHeight(20)).toBe(72);
    expect(clampChatInputHeight(999)).toBe(360);
  });

  it("keeps the final result image on a compacted summary message", () => {
    expect(compactedChatMessage({
      id: "summary-1",
      text: "【历史摘要】",
      image: "/api/assets/final.png",
    })).toMatchObject({
      id: "summary-1",
      role: "assistant",
      text: "【历史摘要】",
      image: "/api/assets/final.png",
    });
  });

  it("repairs an older text-only summary from checkpoint history", () => {
    const repaired = recoverCompactedSummaryImage(
      [{ id: "summary-1", role: "assistant", text: "【历史摘要】\n内容" }],
      [{ role: "assistant", content: "【历史摘要】\n内容", images: ["/api/assets/final.png"] }],
    );
    expect(repaired[0].image).toBe("/api/assets/final.png");
  });
});
