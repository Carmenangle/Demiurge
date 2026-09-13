// 剧情回合号工具（M1，2026-09-06 用户定案）：
// 「当前会话展示内容就是完整的记忆与上下文」——删除/重新生成消息时，被删回合派生的
// 记忆（纪要/时序事实/角色状态）要按回合级联封口。回合号持久化在 assistant 消息上
// （turnNo，随快照落盘），删除/重生成后不倒退，封口级联靠它对齐。

import type { ChatMessage } from "../types/chat";
import { promptHistory } from "./chatGeneration";

/** 下一个剧情回合号：优先沿用显式 turnNo 链（max+1）；无链（旧会话）时按后端
 * `_next_story_turn` 同款规则派生——数的是 promptHistory 过滤后的带正文 assistant
 * 条数（剔除 system 提示/顶层媒体气泡/非剧情路由，双端一致，见 shared/fixtures
 * /story-turn-parity.json 对拍），+1。 */
export function nextStoryTurnNo(messages: readonly ChatMessage[]): number {
  const explicit = messages.filter(
    (m) => m.role === "assistant" && typeof m.turnNo === "number" && m.turnNo > 0,
  );
  if (explicit.length > 0) {
    return Math.max(...explicit.map((m) => m.turnNo as number)) + 1;
  }
  return promptHistory(messages).filter((h) => h.role === "assistant").length + 1;
}

/** 一组消息里待封口的回合号（去重升序；只有 assistant 楼层携带回合）。 */
export function sealTurnsOf(messages: readonly ChatMessage[]): number[] {
  const turns = [...new Set(
    messages
      .filter((m) => m.role === "assistant" && typeof m.turnNo === "number" && m.turnNo > 0)
      .map((m) => m.turnNo as number),
  )];
  return turns.sort((a, b) => a - b);
}
