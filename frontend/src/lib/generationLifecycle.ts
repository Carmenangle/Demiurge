// 生成生命周期：前端 Agent 与 ComfyUI 两条生成通道的单一真相源（纯 reducer，无 React 依赖，可直接单测）。
//
// 取代此前散在 streamingId / wfRunning / imgStartedRef / pendingPromptRef 四信号的临时组合，
// 以及影子镜像 queueRef↔queued。状态转移集中在 reduce()，派生判断集中在 selectors，
// 副作用（abort / 轮询 / 取消请求）仍留在 ChatView，根据通道状态变化执行。
//
// 领域定义见 CONTEXT.md「生成生命周期 / 排队 / 引导」。

import type { RichContent } from "../components/RichInput";

// 排队的待发消息
export interface QueueItem {
  id: string;
  text: string;      // 预览用（队列条显示）
  content: RichContent;
}

export interface AgentGeneration {
  botId: string;
  imageStarted: boolean;  // 本轮是否已触发云端生图
}

export interface WorkflowGeneration {
  promptId: string;
}

export interface GenState {
  // 对话与 ComfyUI 是正交通道：工作流运行时仍可开始下一轮 Agent，彼此完成也不能清掉对方。
  agent: AgentGeneration | null;
  workflow: WorkflowGeneration | null;
  queue: QueueItem[];
}

export const initialGenState: GenState = { agent: null, workflow: null, queue: [] };

export type GenAction =
  | { t: "agentStart"; botId: string }        // 智能体一轮开始（进入 agent 态，未出图）
  | { t: "agentImage"; botId: string }         // 本轮已触发生图（仅当前 botId 可更新）
  | { t: "agentDone"; botId: string }          // 智能体流结束（仅当前 botId 可结束）
  | { t: "workflowStart"; promptId: string }   // /s 工作流提交（进入 workflow 态）
  | { t: "workflowDone"; promptId: string }    // 工作流完成/超时（仅当 promptId 匹配才回 idle）
  | { t: "stop" }                              // 强制停止当前生成（回 idle，队列不动）
  | { t: "reset" }                             // 切仓库：清状态 + 清队列
  | { t: "enqueue"; item: QueueItem }
  | { t: "dequeue" }                           // 移除队首（配合 selectQueueHead 取值后调用）
  | { t: "removeQueued"; id: string };

export function reduce(s: GenState, a: GenAction): GenState {
  switch (a.t) {
    case "agentStart":
      return { ...s, agent: { botId: a.botId, imageStarted: false } };
    case "agentImage":
      return s.agent?.botId === a.botId
        ? { ...s, agent: { ...s.agent, imageStarted: true } }
        : s;
    case "agentDone":
      return s.agent?.botId === a.botId
        ? { ...s, agent: null }
        : s;
    case "workflowStart":
      return { ...s, workflow: { promptId: a.promptId } };
    case "workflowDone":
      // 所有权守卫内置：只清匹配 promptId，防旧任务的迟到回调误清新任务。
      return s.workflow?.promptId === a.promptId
        ? { ...s, workflow: null }
        : s;
    case "stop":
      return { ...s, agent: null, workflow: null };
    case "reset":
      return initialGenState;
    case "enqueue":
      return { ...s, queue: [...s.queue, a.item] };
    case "dequeue":
      return { ...s, queue: s.queue.slice(1) };
    case "removeQueued":
      return { ...s, queue: s.queue.filter((q) => q.id !== a.id) };
    default:
      return s;
  }
}

// ===== selectors（派生判断，取代各处手拼布尔）=====

export const isBusy = (s: GenState): boolean => !!s.agent || !!s.workflow;

// 只有 Agent 正在生成正文时，新对话才进入持久队列；ComfyUI 不占用对话通道。
export const blocksDialogueSubmission = (s: GenState): boolean => !!s.agent;

// 正在流式的 bot 气泡 id（渲染 streaming 态 + onDone 更新用）；非 agent 态为 null
export const streamingBotId = (s: GenState): string | null =>
  s.agent?.botId || null;

// 当前 /s 工作流的 prompt_id（强停时要中断 ComfyUI 用）；非 workflow 态为 null
export const runningPromptId = (s: GenState): string | null =>
  s.workflow?.promptId || null;

// 可直接打断合并（无需确认）：智能体纯文本流式、还没出图
export const canInterruptFreely = (s: GenState): boolean =>
  !!s.agent && !s.agent.imageStarted && !s.workflow;

// 打断需二次确认：工作流在跑，或智能体已触发生图（云调用可能作废/工作流会停）
export const needsConfirm = (s: GenState): boolean =>
  !!s.workflow || !!s.agent?.imageStarted;

export const queueHead = (s: GenState): QueueItem | undefined => s.queue[0];
export const queuedItem = (s: GenState, id: string): QueueItem | undefined =>
  s.queue.find((q) => q.id === id);
