// deletedMessageTombstones.ts — 已删消息墓碑（纯逻辑，可单测）
//
// 契约来源（AGENTS.md 架构合同）："会话快照是历史真源；已删除消息不得被缓存或旧状态复活。"
//
// 缺陷背景（2026-08-30 用户实锤：画布删除剧情楼层节点后，对话模式消息仍在、切回画布节点复活）：
//   画布删楼层 = deleteMessage 删本地状态 + 异步落库快照；而每次 Agent 回合结束
//   onDone → startAgentRecovery 会拉全量后端快照并 upsertMessages 合并（后台插画期间
//   每 1.5s 轮询持续合并）。upsert 只增不删——晚到的旧快照会把刚删的消息合并回状态：
//   画布重投影 → 节点复活，防抖快照再把它持久化 → 永久回魂。
//
// 方案：deleteMessage 记录墓碑；所有快照回灌/合并路径（agent 恢复、工作流回灌、
// reloadFromSnapshot）先经 filterDeleted 过滤。墓碑随页面刷新清空（重新加载 =
// 以后端为真源）；导入会话等显式回灌动作先 clear（见 ChatView）。

export class DeletedMessageTombstones {
  private byThread = new Map<string, Set<string>>();

  /** 删除消息时记录墓碑（按 thread 隔离）。 */
  record(threadId: string, messageId: string): void {
    if (!threadId || !messageId) return;
    let set = this.byThread.get(threadId);
    if (!set) {
      set = new Set();
      this.byThread.set(threadId, set);
    }
    set.add(messageId);
  }

  /** 过滤掉本会话已删除的消息：旧快照回灌不得复活它们。 */
  filterDeleted<T extends { id?: string }>(threadId: string, items: readonly T[]): T[] {
    const set = this.byThread.get(threadId);
    if (!set || set.size === 0) return [...items];
    return items.filter((item) => !(item && item.id && set.has(item.id)));
  }

  /** 显式以后端为真源的动作（如导入会话）之后允许同 id 内容回灌，清除该 thread 的墓碑。 */
  clear(threadId: string): void {
    this.byThread.delete(threadId);
  }
}

/** 模块级单例：跨 ChatView 重挂载保留（key=activeWork.id，切作品重挂载后仍要防旧快照回灌）。 */
export const deletedMessageTombstones = new DeletedMessageTombstones();
