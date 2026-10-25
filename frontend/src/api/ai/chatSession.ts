// 会话历史与快照（原 api/ai.ts 会话持久化段，2026-10-24 按资源域拆分）：
// 历史回填 / 快照读写（单调版本）/ 单条编辑 / 追加 / 导出导入 / 缓存清理 / 压缩 / 记忆封口。
import { apiGet, apiPost } from "../client";
import type { ChatMessage } from "../../types/chat";
import { chatBody, sseEmbed } from "./wire";

export interface ChatTurn {
  role: "user" | "assistant" | "system";
  content: string;
  images?: string[];   // 该条消息附带的图片（dataURI 或 URL）
}

// 拉取某仓库已落盘的对话历史（刷新/进入仓库时回填）
export function fetchHistory(threadId: string) {  return apiGet<{ items: ChatTurn[] }>(
    `/ai/chat/history?thread_id=${encodeURIComponent(threadId)}`,
  );
}

// M1 记忆级联封口（2026-09-06 用户定案）：删除消息/重新生成后按回合封口派生记忆
// （纪要/时序事实/角色状态）——注入侧等效清除，数据保留可审计；封口非物理删。
export interface MemorySealResult {
  chronicle_sealed: number;
  facts_sealed: number;
  state_undone: number;
  /** M1 审计 #2：审计窗口被截断、无法证明精确还原的状态字段（保守上报，不静默错账） */
  state_incomplete: string[];
  last_turn_rewound: boolean;
}

export function sealMemory(args: {
  outputDir: string;
  repoId: string;
  cardName?: string;
  cardNames?: string[];
  turns: number[];
  /** delete=回合永久封口（默认）；regenerate=旧条目隐藏、回合区间保持开放（重生成新写入立即可见） */
  mode?: "delete" | "regenerate";
}): Promise<MemorySealResult> {
  return apiPost<MemorySealResult>("/ai/memory/seal", {
    output_dir: args.outputDir,
    repo_id: args.repoId,
    card_name: args.cardName || "",
    card_names: args.cardNames || [],
    turns: args.turns,
    mode: args.mode || "delete",
  });
}

// 清除缓存：清对话线+前端快照+删本仓库 reference/ 上传参考图。资产库与知识库不动。返回删了几张参考图。
export function clearCache(threadId: string, outputDir: string) {
  return apiPost<{ ok: boolean; removed: number }>(
    "/ai/chat/clear-cache",
    { thread_id: threadId, output_dir: outputDir },
  );
}

// 压缩完整会话：旧消息替换为摘要文本和最后成果图；知识库与资产库不动。
export function compactHistory(
  threadId: string,
  chat: { baseUrl: string; apiKey: string; modelName: string },
  embed: { baseUrl: string; apiKey: string; modelName: string },
) {
  return apiPost<{
    ok: boolean;
    summary: string;
    image_count: number;
    message: Pick<ChatMessage, "id" | "role" | "text"> & Partial<Pick<ChatMessage, "image">>;
  }>(
    "/ai/chat/compact",
    { thread_id: threadId, ...chatBody(chat), ...sseEmbed(embed) },
  );
}

let lastSnapshotRevision = Date.now();

// 落盘前端完整消息流快照（含工作流卡/反推卡等非对话消息），作为可靠真源。
// 单调版本保证较早发出的防抖请求即使晚到，也不能覆盖后发的删除结果。
export function saveSnapshot(threadId: string, messages: unknown[]) {
  lastSnapshotRevision = Math.max(Date.now(), lastSnapshotRevision + 1);
  return apiPost<{ ok: boolean; saved: boolean }>("/ai/chat/snapshot/save", {
    thread_id: threadId,
    messages,
    revision: lastSnapshotRevision,
  });
}

// 读取某仓库的消息流快照（localStorage 缺失时回填，关浏览器/清端口不丢）
export function fetchSnapshot(threadId: string) {
  return apiGet<{ items: unknown[] }>(
    `/ai/chat/snapshot?thread_id=${encodeURIComponent(threadId)}`,
  );
}

// 按消息 id 原子落库单条编辑（只更新既有消息，目标不存在返回 updated=false）。
// 编辑若只走全量快照保存，防抖/网络抖动静默失败后，下一轮生成会用旧快照把编辑打回
// （2026-09-13 实锤：删掉的「（正文到此…）」在新生成后重新出现）。
export function editMessageRemote(threadId: string, message: unknown) {
  return apiPost<{ ok: boolean; updated: boolean }>("/ai/chat/message/edit", {
    thread_id: threadId,
    message,
  });
}

// 把一条已生成的消息（提示词/图片/配方文本）直接落盘到目标 thread，不调模型。
// 用于「发送至对话框 / 发送至对话」：从资产库或画布把内容送到指定作品对话，刷新后保留。
export function chatAppend(
  threadId: string, role: "user" | "assistant", text: string, images?: string[],
) {
  return apiPost<{ ok: boolean }>("/ai/chat/append", {
    thread_id: threadId,
    role,
    text,
    images: images || [],
  });
}

// 导出某作品的完整会话记录（剧情模式常用：备份/搬到别处）
export function exportSnapshot(threadId: string) {
  return apiGet<{ thread_id: string; messages: unknown[] }>(
    `/ai/chat/snapshot/export?thread_id=${encodeURIComponent(threadId)}`,
  );
}

// 导入会话记录到某作品：replace=整体覆盖，否则按消息 id 合并
export function importSnapshot(threadId: string, messages: unknown[], replace = true) {
  return apiPost<{ ok: boolean; count: number }>("/ai/chat/snapshot/import", {
    thread_id: threadId,
    messages,
    replace,
  });
}
