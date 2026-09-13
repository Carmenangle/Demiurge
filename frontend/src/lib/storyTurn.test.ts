import { describe, expect, it } from "vitest";
import { nextStoryTurnNo, sealTurnsOf } from "./storyTurn";
import type { ChatMessage } from "../types/chat";

const msg = (role: "user" | "assistant", text: string, turnNo?: number): ChatMessage => ({
  id: `${role}-${text}-${turnNo ?? ""}`, role, text, ...(turnNo ? { turnNo } : {}),
});

describe("storyTurn 剧情回合号（M1 记忆封口）", () => {
  it("显式 turnNo 链取 max+1（删除/重生成后不倒退）", () => {
    const messages = [
      msg("user", "去雪山"),
      msg("assistant", "救援发生", 3),
      msg("assistant", "", 4), // 空正文媒体楼层也占回合链
      msg("user", "继续"),
    ];
    expect(nextStoryTurnNo(messages)).toBe(5);
  });

  it("无 turnNo 链（旧会话）按带正文 assistant 条数派生，与后端派生规则一致", () => {
    const messages = [
      msg("user", "甲"),
      msg("assistant", "回复一"),
      msg("assistant", ""), // 空正文不计
      msg("user", "乙"),
    ];
    expect(nextStoryTurnNo(messages)).toBe(2);
  });

  it("sealTurnsOf 只收 assistant 的正回合号并去重升序", () => {
    const messages = [
      msg("user", "甲"),
      msg("assistant", "回复", 5),
      msg("assistant", "再回复", 3),
      msg("assistant", "无回合"),
      msg("user", "乙", 9), // user 楼层不带回合
    ];
    expect(sealTurnsOf(messages)).toEqual([3, 5]);
    expect(sealTurnsOf([msg("user", "只有用户")])).toEqual([]);
  });
});

// ── M1 审计 #1：与后端 to_prompt_history 的派生对拍（shared/fixtures/story-turn-parity.json，
//    后端对拍见 backend/tests/test_story_turn_parity.py）──
import parityFixture from "../../../shared/fixtures/story-turn-parity.json";

it("旧会话兜底派生与后端 load_prompt_history 回路对拍一致", () => {
  const messages = (parityFixture as ChatMessage[]).map((m, i) => ({ ...m, id: m.id || `f${i}` }));
  // 夹具含 3 条剧情 assistant（a1 正文 / a6 parts 文本 / a7 answer）→ 下一回合 4
  expect(nextStoryTurnNo(messages)).toBe(4);
});
