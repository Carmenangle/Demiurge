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

// 第 58 刀（2026-09-24）：**回合号高位水印**（按会话持久化、只升不降）。
// 真机实锤：19:09 与 19:52 两轮的回合号**同为 54** ⇒ 槽 id（`illo-req-<repo>-<turn>[-N]`）
// 撞车 ⇒ 新图的槽/图与上一轮混叠（「新图片不刷新不插入」的根因）。`max(显式 turnNo)+1`
// 在「最高回合的消息被删除/重新生成」时会**复用旧号** —— 「重生成复用被删回合号」是
// 有意设计（同回合幂等重写、旧行被替换），但**新消息**复用旧号就会与仍在的旧槽相撞。
// ⇒ 水印兜底：新回合号不得低于历史最高已用号。⚠ 不改「重生成复用被删回合号」——
//   重生成走显式 `storyTurn` 入参、且 `rememberStoryTurn` 只在更大时才抬水印。
const TURN_HWM_KEY = "laf_story_turn_hwm";

export function storyTurnHighWater(threadId: string): number {
  try {
    return Number(localStorage.getItem(`${TURN_HWM_KEY}:${threadId}`) || "") || 0;
  } catch {
    return 0;
  }
}

export function rememberStoryTurn(threadId: string, turnNo: number): void {
  try {
    if (Number.isFinite(turnNo) && turnNo > storyTurnHighWater(threadId)) {
      localStorage.setItem(`${TURN_HWM_KEY}:${threadId}`, String(turnNo));
    }
  } catch {
    // 存储不可用（隐私模式等）⇒ 水印失效，退回 max+1 行为
  }
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
