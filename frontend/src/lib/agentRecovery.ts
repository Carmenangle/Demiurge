import type { ChatMessage } from "../types/chat";

export interface AgentRecoveryDeps {
  fetchSnapshot: () => Promise<{ items: ChatMessage[] }>;
  fetchRunning: () => Promise<{ running: boolean }>;
  onSnapshot: (items: ChatMessage[]) => void;
  isActive: () => boolean;
  wait?: () => Promise<void>;
}

/** 首页使用共享的 "home" 标识但只保留临时草稿，不能从后端快照恢复历史。 */
export const shouldRecoverAgentRun = (threadId: string): boolean => threadId !== "home";

const waitForNextPoll = () => new Promise<void>((resolve) => setTimeout(resolve, 1500));

/** SSE 断开后继续追踪后台 Agent，直到最终快照已经可读。 */
export async function recoverAgentRun(deps: AgentRecoveryDeps): Promise<boolean> {
  const wait = deps.wait || waitForNextPoll;
  while (deps.isActive()) {
    try {
      const [snapshot, state] = await Promise.all([
        deps.fetchSnapshot(),
        deps.fetchRunning(),
      ]);
      if (!deps.isActive()) return false;
      deps.onSnapshot(snapshot.items || []);
      if (!state.running) return true;
    } catch {
      if (!deps.isActive()) return false;
    }
    await wait();
  }
  return false;
}
