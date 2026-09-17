// 工作流进度的「跨导航持久化分片」（T2.2-B4-a：2026-09-17 从 lib/useChatSession.ts 外移）。
//
// ▍为什么状态本体不住在 React state 里：进度/节点名要在**切换页面（组件卸载）**后仍保留 ⇒
//   必须放在模块级容器里，组件重挂载时按当前线程键读回。
// ▍为什么按线程键分片：P18（2026-09-15）的硬约束——此前是模块级单例（`{ current }`），
//   跨仓库共享同一格会读到上一个仓库的进度/节点名，故改成 `Map<threadId, …>`。
// ▍搬移原则：只搬收口 —— 分片 Map、注释、两条 `useState`、两条包装 setter、
//   切线程重对齐 `useEffect` 与搬移前**逐字一致**（同层级、零去缩进），
//   仅把闭包符号 `threadId` 收成入参。⇒ 行为面由 useWfProgressShard.test.tsx 承担。
//
// ▍红线（改动即破行为面）：
//   ① 包装 setter 必须**先写分片再 setState**——只 setState 则切走再回来进度归零（跨导航持久化失效）；
//   ② 重对齐 effect 依赖数组保持 `[threadId]`：只有切线程才重对齐，同一线程内的重渲染不得覆写进度；
//   ③ 分片读写必须带 `threadId` 键（P18 硬约束）——退回单例即跨仓库串进度；
//   ④ 调用点必须留在视图内**原 `useState` 的位置**（hook 调用序＝状态初始化序）。

import { useEffect, useState } from "react";

// 跨导航持久化：切换页面时进度条/节点名不丢失。
// P18（2026-09-15）：改**按线程键分片**——此前是模块级单例（`{ current }`），跨仓库共享同一格，
// 切进另一个仓库会读到上一个仓库的进度/节点名（渲染虽被 `wfRunning || slowWatchPromptId` 部分挡住，
// 但「切进有在途任务的仓库、进入慢守望」的窗口里会短暂显示错仓库的进度与该仓库的节点名）。
const persistedWfProgress = new Map<string, number | null>();
const persistedWfNode = new Map<string, string>();
// 注：原 `persistedWfPromptParams` 已删 —— 全仓零引用的死代码（架构审查 P18 硬约束#3）。

export interface WfProgressShard {
  wfProgress: number | null;   // 工作流实时进度%（WS，null=无），跨导航持久化
  wfNode: string;              // 当前执行的节点显示名（WS executing 消息），跨导航持久化
  updateWfProgress: (v: number | null) => void;
  updateWfNode: (v: string) => void;
}

export function useWfProgressShard(threadId: string): WfProgressShard {
  const [wfProgress, setWfProgress] = useState<number | null>(persistedWfProgress.get(threadId) ?? null);  // 工作流实时进度%（WS，null=无），跨导航持久化
  const [wfNode, setWfNode] = useState<string>(persistedWfNode.get(threadId) ?? "");  // 当前执行的节点显示名（WS executing 消息），跨导航持久化
  // 包装 setter：同时写入**当前线程的分片**，确保切换页面回来后进度不丢失
  const updateWfProgress = (v: number | null) => { persistedWfProgress.set(threadId, v); setWfProgress(v); };
  const updateWfNode = (v: string) => { persistedWfNode.set(threadId, v); setWfNode(v); };
  // P18：切线程时把 state 重新对齐到目标线程的分片（否则沿用上一个仓库的进度/节点名）
  useEffect(() => {
    setWfProgress(persistedWfProgress.get(threadId) ?? null);
    setWfNode(persistedWfNode.get(threadId) ?? "");
  }, [threadId]);
  return { wfProgress, wfNode, updateWfProgress, updateWfNode };
}
