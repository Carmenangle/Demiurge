import { apiGet, apiGetBuffer, apiPost, apiUpload, apiUrl } from "./client";
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

// 实时检测全局代理（2026-09-10 升级：先读系统代理 WinINET/环境变量，再回退手填地址）。
// 继承全局模式：探到可用代理则走代理，否则自动直连。
export interface ProxyStatus {
  listening: boolean;        // 是否选中了可用代理（兼容旧字段）
  address: string;           // 生效代理地址（空=直连）；无可用代理时=系统代理或手填地址（供提示）
  source: "system" | "env" | "manual" | "none";
  system_address: string;    // 系统代理地址（探测前）
  manual_address: string;    // 应用内手填地址
  candidates: { address: string; source: string; listening: boolean }[];
  search_reachable?: boolean; // 仅 search 探测时返回：经生效代理真跑一次搜索是否拿到结果
  search_detail?: string;
}

// search=true 额外真跑一次最小联网搜索（数秒）；启动与常规刷新用默认的快速探测。
export function fetchProxyStatus(opts: { search?: boolean } = {}) {
  const q = opts.search ? "?search=true" : "";
  return apiGet<ProxyStatus>(`/user-state/proxy-status${q}`);
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

// 壁纸库：扫描用户存放地址（后端递归、分页、按类型过滤），返回缩略图直链。
export interface WallpaperLibraryItem {
  path: string;
  name: string;
  kind: "image" | "video";
  animated: boolean;
  size: number;
  mtime: number;
}

export interface WallpaperLibraryPage {
  folder: string;
  query?: string;
  total: number;
  visible_total: number;
  hidden_total: number;
  page: number;
  pages: number;
  page_size: number;
  truncated: boolean;
  items: WallpaperLibraryItem[];
}

export type WallpaperLibraryKind = "all" | "video" | "image";

export function scanWallpapers(args: {
  folder: string;
  kind?: WallpaperLibraryKind;
  page?: number;
  pageSize?: number;
  hidden?: string[];
  hiddenOnly?: boolean;
  query?: string;
}) {
  return apiPost<WallpaperLibraryPage>("/user-state/wallpapers/scan", {
    folder: args.folder,
    kind: args.kind || "all",
    page: args.page || 1,
    page_size: args.pageSize || 21,
    hidden: args.hidden || [],
    hidden_only: args.hiddenOnly === true,
    query: args.query || "",
  }, 60000);
}

export function wallpaperThumbUrl(folder: string, path: string): string {
  const q = new URLSearchParams({ folder, path });
  return apiUrl(`/user-state/wallpapers/thumb?${q.toString()}`);
}

/**
 * scene 作品的 `scene.pkg` 原始字节。
 *
 * 后端只按工坊 id 定位文件并原样吐出，不解析包格式（PKGV/LZ4/TEX 的解码真源在
 * 前端 vendored 渲染器，见 `src/lib/scene/`）；id 非纯数字或包不存在都会 404。
 */
export function fetchWallpaperScenePkg(workshopId: string, signal?: AbortSignal): Promise<ArrayBuffer> {
  const q = new URLSearchParams({ workshop_id: workshopId });
  return apiGetBuffer(`/user-state/wallpapers/workshop/scene-pkg?${q.toString()}`, signal);
}

// Wallpaper Engine 创意工坊：后端自动发现 Steam 库（含 libraryfolders.vdf），按 431960 扫描。
// web/application 无法在浏览器直接渲染，回落到作品自带 preview，并标 preview_only。
// scene 作品自带 scene.pkg，前端 vendored 渲染器可实时渲染（见 scene_pkg）。
export interface WallpaperWorkshopItem extends WallpaperLibraryItem {
  folder: string;          // 作品目录（缩略图端点需要按作品目录做包含性校验）
  title: string;
  source: "workshop";
  workshop_id: string;
  we_type: string;         // scene / video / web / application…
  preview_only: boolean;   // true=应用的是静态预览图，不是真正的动态场景
  scene_pkg: boolean;      // true=作品带 scene.pkg，可交给前端实时渲染
  preview: string;         // 作品自带预览图（常见 GIF）；空串=没有
  preview_width: number;   // 预览图原始像素，用于标注低清
  preview_height: number;
}

export interface WallpaperWorkshopPage {
  folder: string;
  roots: string[];
  found: boolean;
  query?: string;
  total: number;
  visible_total: number;
  hidden_total: number;
  page: number;
  pages: number;
  page_size: number;
  truncated: boolean;
  items: WallpaperWorkshopItem[];
}

export function scanWorkshopWallpapers(args: {
  kind?: WallpaperLibraryKind;
  page?: number;
  pageSize?: number;
  hidden?: string[];
  hiddenOnly?: boolean;
  query?: string;
} = {}) {
  return apiPost<WallpaperWorkshopPage>("/user-state/wallpapers/workshop", {
    kind: args.kind || "all",
    page: args.page || 1,
    page_size: args.pageSize || 21,
    hidden: args.hidden || [],
    hidden_only: args.hiddenOnly === true,
    query: args.query || "",
  }, 60000);
}

// 自定义壁纸：存储位置（支持 ~）、上传（JPG/PNG/MP4）与更改位置时的文件迁移。
export interface WallpaperStoreInfo {
  dir: string;
  raw: string;
  default: string;
  is_default: boolean;
  count: number;
  formats: string[];
}

export function getWallpaperStore(dir: string) {
  const q = new URLSearchParams({ dir });
  return apiGet<WallpaperStoreInfo>(`/user-state/wallpapers/store?${q.toString()}`, 15000);
}

export interface WallpaperMigration {
  ok: boolean;
  from: string;
  to: string;
  mapping: Record<string, string>;
  moved: number;
  count: number;
  skipped: string[];
  truncated: boolean;
}

export function migrateWallpaperStore(args: { fromDir: string; toDir: string }) {
  return apiPost<WallpaperMigration>("/user-state/wallpapers/store/migrate", {
    from_dir: args.fromDir,
    to_dir: args.toDir,
  }, 120000);
}

export function uploadWallpaper(file: File, dir: string) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("dir", dir);
  return apiUpload<{ ok: boolean; path: string; name: string; count: number }>(
    "/user-state/wallpapers/upload", fd, 120000,
  );
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
