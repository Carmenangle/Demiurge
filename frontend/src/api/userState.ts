import { apiGet, apiPost } from "./client";
import type { Repo } from "../stores/repos";
import type { Settings } from "../stores/settings";

// 后端 data/user_state.json 的形状（repos/settings 任一可为空）
export interface UserState {
  repos: Repo[] | null;
  settings: Settings | null;
}

export function getUserState() {
  return apiGet<UserState>("/user-state");
}

// 实时检测全局代理地址是否在本机监听（继承全局模式：开则走代理，没开则直连）
export function fetchProxyStatus() {
  return apiGet<{ listening: boolean; address: string }>("/user-state/proxy-status");
}

// 整体覆盖写。repos 与 settings 分头变更，故这里合并当前两块一起 POST。
export function saveUserState(state: { repos: Repo[]; settings: Settings }) {
  return apiPost<{ ok: boolean }>("/user-state", state);
}

// 仓库改名：后端重命名输出文件夹 + 重写快照/RAG 里的图片路径
export function renameRepoFolder(args: {
  repo_id: string; old_name: string; new_name: string; output_dir: string;
}) {
  return apiPost<{ folder: string; snapshot?: number; rag?: number }>(
    "/user-state/rename-folder", args,
  );
}

// 删仓库：只删该仓库在「仓库文件夹」里的作品文件夹（快照/会话/图），不碰源库角色卡/世界书。
export function deleteRepoFolder(args: {
  repo_id: string; name: string; output_dir: string;
}) {
  return apiPost<{ deleted: boolean; folder: string }>(
    "/user-state/delete-folder", args,
  );
}

// 上传对话背景图，返回后端保存的本地路径（填进 chatBgPath）
export async function uploadChatBg(file: File): Promise<{ ok: boolean; path: string }> {
  const fd = new FormData();
  fd.append("file", file);
  const resp = await fetch("http://127.0.0.1:8010/api/user-state/upload-bg", { method: "POST", body: fd });
  if (!resp.ok) throw new Error(`上传失败: ${resp.status}`);
  return resp.json();
}

// 上传参考音轨到 <repo>/voices/，返回本地路径（填进 characterVoices[角色].voiceRef）
export async function uploadVoice(
  file: File, repoId: string, outputDir: string,
): Promise<{ ok: boolean; path: string }> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("repo_id", repoId);
  fd.append("output_dir", outputDir);
  const resp = await fetch("http://127.0.0.1:8010/api/user-state/upload-voice", { method: "POST", body: fd });
  if (!resp.ok) throw new Error(`上传失败: ${resp.status}`);
  return resp.json();
}

// 画布布局：每作品 canvas.json（布局/连线/视口），替换 localStorage 持久化
export interface CanvasLayoutWire {
  nodes: Record<string, { x: number; y: number; w: number; h: number; custom?: boolean; label?: string; parentId?: string }>;
  edges: { source: string; target: string }[];
  viewport: { x: number; y: number; scale: number };
  /** 灵感卡（角色卡 / 世界书条目 / 预设 / 表格行 各自一张；可被剧情对话引用） */
  inspiration_cards?: Array<{
    id: string; title: string; content: string;
    kind: "character" | "worldbook-entry" | "preset" | "table-row";
    sourceRef?: string;
    x: number; y: number; w: number; h: number; groupId?: string;
  }>;
  /** 参考图（文件夹拖入画布的图片节点，独立于灵感卡） */
  reference_images?: Array<{
    id: string; title: string; imageUrl: string;
    x: number; y: number; w: number; h: number; groupId?: string;
  }>;
  /** 已删除投影节点黑名单（id = 类型前缀 + generationId）；投影时过滤防止复活 */
  deleted_ids?: string[];
}

export function getCanvasLayout(repoId: string, outputDir: string) {
  const q = new URLSearchParams({ repo_id: repoId });
  if (outputDir) q.set("output_dir", outputDir);
  return apiGet<CanvasLayoutWire>(`/user-state/canvas-layout?${q.toString()}`);
}

export function saveCanvasLayout(repoId: string, outputDir: string, layout: CanvasLayoutWire) {
  return apiPost<{ ok: boolean }>("/user-state/canvas-layout", {
    repo_id: repoId,
    output_dir: outputDir,
    nodes: layout.nodes,
    edges: layout.edges,
    viewport: layout.viewport,
    // 灵感卡此前漏传 → 保存后刷新即丢；与删除黑名单一并随布局落盘
    inspiration_cards: layout.inspiration_cards || [],
    reference_images: layout.reference_images || [],
    deleted_ids: layout.deleted_ids || [],
  });
}
