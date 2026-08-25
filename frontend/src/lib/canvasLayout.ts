// canvasLayout.ts — 画布节点布局 + 连线的持久化（后端 canvas.json，替换 localStorage）
//
// 真源仍是 generation_store（按作品 threadId 拉取）；这里只存「用户拖动后的视觉布局」
// 与「画布视口」，落盘到后端 <output_dir>/<repo_id>/canvas.json，不污染 generation_store/快照/角色卡。
// 切到对话视图再切回画布时从后端恢复节点位置。
//
// 形状：{ nodes: Record<nodeId, {x,y,w,h,custom?}>, edges: [{source,target}], viewport: {x,y,scale} }

import {
  getCanvasLayout, saveCanvasLayout,
  type CanvasLayoutWire,
} from "../api/userState";

export interface NodeLayout {
  x: number;
  y: number;
  w: number;
  h: number;
  /** 用户是否手动 resize 过（true=锁定 w/h 跟随自定义；false/缺失=高度随图片原比例自适应） */
  custom?: boolean;
  /** 卡片底部介绍文字自定义（右键「编辑介绍文字」写入，优先于 prompt 显示） */
  label?: string;
  /** 所属组 id（组内子节点的 position 为相对组坐标；刷新后据此还原组关系） */
  parentId?: string;
  /** 工作流工具卡：是否已选择完毕 */
  wfConfirmed?: boolean;
  /** 工作流工具卡：完整 Draft Graph */
  wfDraft?: unknown;
  /** 工作流工具卡：graphToPrompt 抓取结果 */
  wfCaptured?: unknown;
  /** 工作流工具卡：模板暴露的节点 id */
  wfExposedIds?: string[];
  /** 工作流工具卡：模板 id/名（持久化工具卡本身——切画布再回按 templateId 恢复节点，否则卡消失） */
  templateId?: string;
  templateName?: string;
}

export interface Viewport {
  x: number;        // 画布左上角相对视口左上角的偏移（px）
  y: number;
  scale: number;    // 缩放比例，1=原始
}

export interface CanvasLayout {
  nodes: Record<string, NodeLayout>;
  edges: { source: string; target: string }[];
  viewport: Viewport;
  /** 灵感卡持久化（独立于 generation 节点的视觉布局；content/title/kind 在这里存） */
  inspirationCards?: InspirationCardStored[];
  /** 参考图持久化（文件夹拖入画布的图片节点，独立于灵感卡） */
  referenceImages?: ReferenceImageStored[];
  /** 已删除投影节点黑名单：删除后投影过滤，防止 refresh 时复活（会话快照同契同款语义） */
  deletedIds?: string[];
}

/** 灵感卡持久化记录（与 canvas.json 同存；x/y/w/h 独立存，不进 nodes[].label） */
export interface InspirationCardStored {
  id: string;
  title: string;
  content: string;
  kind: "character" | "worldbook-entry" | "preset" | "table-row";
  sourceRef?: string;
  x: number;
  y: number;
  w: number;
  h: number;
  /** 所属组 id（组节点用 group 类型，children 设 parentId） */
  groupId?: string;
  /** 拖放/下载素材图片的本地 URL（可选，有值时卡片渲染该图片） */
  imageUrl?: string;
  /** 原始源 URL（对话灵感卡自动投影时保留；插入对话时作为图片参数上传用，避免代理地址后端无法访问） */
  sourceUrl?: string;
}

/** 参考图持久化记录（文件夹拖入画布的图片，独立于灵感卡） */
export interface ReferenceImageStored {
  id: string;
  /** 参考图标题（文件名，可编辑） */
  title: string;
  /** 参考图本地 URL（local-view） */
  imageUrl: string;
  x: number;
  y: number;
  w: number;
  h: number;
  /** 所属组 id */
  groupId?: string;
}

const DEFAULT_VIEWPORT: Viewport = { x: 0, y: 0, scale: 1 };
const MIN_SCALE = 0.2;
const MAX_SCALE = 3;

export async function loadLayout(repoId: string | null | undefined, outputDir: string): Promise<CanvasLayout> {
  if (!repoId) return { nodes: {}, edges: [], viewport: { ...DEFAULT_VIEWPORT }, inspirationCards: [], referenceImages: [], deletedIds: [] };
  try {
    const wire = await getCanvasLayout(repoId, outputDir);
    return {
      nodes: wire?.nodes && typeof wire.nodes === "object" ? wire.nodes : {},
      edges: Array.isArray(wire?.edges) ? wire.edges : [],
      viewport: {
        x: Number(wire?.viewport?.x) || 0,
        y: Number(wire?.viewport?.y) || 0,
        scale: clampScale(Number(wire?.viewport?.scale) || 1),
      },
      inspirationCards: Array.isArray(wire?.inspiration_cards) ? wire.inspiration_cards : [],
      referenceImages: Array.isArray(wire?.reference_images) ? wire.reference_images : [],
      deletedIds: Array.isArray(wire?.deleted_ids) ? wire.deleted_ids.filter((s) => typeof s === "string") : [],
    };
  } catch {
    return { nodes: {}, edges: [], viewport: { ...DEFAULT_VIEWPORT }, inspirationCards: [], referenceImages: [], deletedIds: [] };
  }
}

export async function saveLayout(
  repoId: string | null | undefined,
  outputDir: string,
  layout: CanvasLayout,
): Promise<void> {
  if (!repoId) return;
  const wire: CanvasLayoutWire = {
    nodes: layout.nodes,
    edges: layout.edges,
    viewport: layout.viewport,
    inspiration_cards: layout.inspirationCards || [],
    reference_images: layout.referenceImages || [],
    deleted_ids: layout.deletedIds || [],
  };
  try {
    await saveCanvasLayout(repoId, outputDir, wire);
  } catch { /* 后端不可用/失败不阻断 UI */ }
}

export function clampScale(s: number): number {
  if (!Number.isFinite(s)) return 1;
  return Math.max(MIN_SCALE, Math.min(MAX_SCALE, s));
}

export const CANVAS_MIN_SCALE = MIN_SCALE;
export const CANVAS_MAX_SCALE = MAX_SCALE;
