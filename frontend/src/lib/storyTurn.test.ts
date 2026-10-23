import { describe, expect, it, vi } from "vitest";
import { nextStoryTurnNo, resolveStoryTurn, sealTurnsOf, syncStoryTurnHighWater } from "./storyTurn";
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

// ── 2026-10-21 修跳号：显式 storyTurn（重生成复用号）不得被高位水印顶高 ──
describe("resolveStoryTurn 回合号定夺（重生成复用 vs 新回合水印兜底）", () => {
  const history = (turnNos: number[]) =>
    turnNos.map((t) => msg("assistant", "正文", t));

  it("重生成复用被删回合号：原样返回且不抬水印（旧实现三项取大 ⇒ hwm+1 顶高 1）", () => {
    const r = resolveStoryTurn(11, history([2, 3, 4, 5, 6]), 11);
    expect(r).toEqual({ turnNo: 11, remember: false });
  });

  it("同一条消息反复重试停在同一个号上（真机：9 层楼 turnNo 虚到 16 的回归锁）", () => {
    // 每轮：楼层里最高号 = 上轮复用号；水印 = 该复用号（入口曾把它记成水印）
    let turnNo = 8;
    const seen: number[] = [];
    for (let i = 0; i < 8; i += 1) {
      const r = resolveStoryTurn(turnNo, history([2, 3, 4, 5, 6, 7, turnNo]), turnNo);
      expect(r.remember).toBe(false);
      seen.push(r.turnNo);
      turnNo = r.turnNo;
    }
    expect(seen).toEqual([8, 8, 8, 8, 8, 8, 8, 8]);
  });

  it("新回合：最高回合被删后**不**再用水印顶回，按链尾+1（2026-10-21 跳号修正）", () => {
    // 旧实现（第 58 刀）会取 max(链尾+1=54, 水印+1=55) ⇒ 55；真机 9 轮会话因此生成出
    // turnNo=17、纪要标「回合 15-17」。水印 54 对应的回合已被删除 ⇒ 钳到链尾，新回合复用 54。
    const r = resolveStoryTurn(undefined, history([50, 51, 52, 53]), 54);
    expect(r).toEqual({ turnNo: 54, remember: true });
  });

  it("水印仍覆盖链尾时正常参与（不误伤第 58 刀的正常场景）", () => {
    // 楼层 50..54 都在，水印 54 = 链尾 ⇒ 下一回合 55
    const r = resolveStoryTurn(undefined, history([50, 51, 52, 53, 54]), 54);
    expect(r).toEqual({ turnNo: 55, remember: true });
  });

  it("新回合无水印时按链尾+1", () => {
    expect(resolveStoryTurn(undefined, history([2, 3]), 0)).toEqual({ turnNo: 4, remember: true });
    expect(resolveStoryTurn(0, history([]), 0)).toEqual({ turnNo: 1, remember: true });
  });

  it("显式号非法（0/负数/NaN）时按新回合处理，不静默复用", () => {
    expect(resolveStoryTurn(0, history([2, 3]), 0).turnNo).toBe(4);
    expect(resolveStoryTurn(-1, history([2, 3]), 0).turnNo).toBe(4);
    expect(resolveStoryTurn(Number.NaN, history([2, 3]), 0).turnNo).toBe(4);
  });
});

// ── 2026-10-21：水印自愈（删除/重生成末尾回合后水印会停在旧高位）──
describe("syncStoryTurnHighWater 水印与消息链对齐", () => {
  const history = (turnNos: number[]) => turnNos.map((t) => msg("assistant", "正文", t));
  const stub = () => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => { store.set(k, v); },
    });
    return store;
  };

  it("水印高于链尾 ⇒ 校准回链尾并写回", () => {
    const store = stub();
    store.set("laf_story_turn_hwm:t1", "16");
    expect(syncStoryTurnHighWater("t1", history([1, 2, 3]))).toBe(3);
    expect(store.get("laf_story_turn_hwm:t1")).toBe("3");
    vi.unstubAllGlobals();
  });

  it("水印不高于链尾 ⇒ 原样返回、不写回", () => {
    const store = stub();
    store.set("laf_story_turn_hwm:t1", "2");
    expect(syncStoryTurnHighWater("t1", history([1, 2, 3]))).toBe(2);
    expect(store.get("laf_story_turn_hwm:t1")).toBe("2");
    vi.unstubAllGlobals();
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
