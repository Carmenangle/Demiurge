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
// ⇒ 水印兜底：新回合号不得低于历史最高已用号。
//
// ⚠ 2026-10-21 修：水印只兜**新回合**，显式 storyTurn（重生成复用号）**不参与取大、也不
//   抬水印**。真机实锤：同一条消息反复「重新生成」时，入口 `rememberStoryTurn` 把复用号
//   记成水印 ⇒ 下一次 hwm+1 比 reusedTurn 大 1 ⇒ `Math.max(三项)` 把复用号顶高 —— 每重试
//   一次 +1，中间结果全被替换 ⇒ 9 层楼 turnNo 虚到 16（跳号非真缺口，缺口计算与对话层号
//   全错位）。「重生成复用被删回合号」是有意设计，不能被水印破坏；而「新消息复用旧号撞槽」
//   由新回合侧的 hwm+1 继续兜住（删掉最高回合再发新消息，history max 变小仍被水印顶回）。
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

/** 本回合最终号 + 该不该抬水印（纯函数，供 runFreeText 与测试共用）。
 *
 * - 显式 `storyTurn`（重生成复用被删回合号）⇒ **原样返回、不抬水印**。同一条消息
 *   反复重试必须停在同一个号上，否则中间尝试会把它一路顶高（2026-10-21 真机跳号根因）。
 * - 新回合 ⇒ `max(链尾+1, 有效水印+1)`，并抬水印：最高回合消息被删/重生成时链尾会复用
 *   旧号，此时靠水印顶回，避免与仍在的插画槽 id 撞车（第 58 刀）。
 * - 2026-10-21 追加：**水印只在它仍覆盖当前链尾时有效**。用户删掉/重生成掉末尾若干回合后，
 *   水印会停在旧高位；若继续拿它 +1 就会跳号（真机：9 个回合的会话生成出 turnNo=17，
 *   纪要标成「回合 15-17」）。水印高于链尾即说明对应回合已不存在，钳到链尾。 */
export function resolveStoryTurn(
  explicit: number | undefined,
  messages: readonly ChatMessage[],
  highWater: number,
): { turnNo: number; remember: boolean } {
  if (typeof explicit === "number" && explicit > 0) {
    return { turnNo: explicit, remember: false };
  }
  const chainNext = nextStoryTurnNo(messages);
  const chainTail = chainNext - 1;
  const effectiveHighWater = Math.min(highWater, Math.max(0, chainTail));
  return {
    turnNo: Math.max(chainNext, effectiveHighWater + 1),
    remember: true,
  };
}

/** 把会话水印与当前消息链对齐（纯副作用集中在 localStorage；返回校准后的水印）。
 *
 * 水印高于链尾 ⇒ 对应回合已被删除/覆盖，写回链尾，防后续新回合继续跳号。
 * 正常情况（水印 ≤ 链尾）原样返回，不做任何写入。 */
export function syncStoryTurnHighWater(
  threadId: string,
  messages: readonly ChatMessage[],
): number {
  const chainTail = Math.max(0, nextStoryTurnNo(messages) - 1);
  const highWater = storyTurnHighWater(threadId);
  if (highWater <= chainTail) return highWater;
  try {
    localStorage.setItem(`${TURN_HWM_KEY}:${threadId}`, String(chainTail));
  } catch {
    // 存储不可用（隐私模式等）⇒ 水印失效，退回 max+1 行为
  }
  return chainTail;
}
