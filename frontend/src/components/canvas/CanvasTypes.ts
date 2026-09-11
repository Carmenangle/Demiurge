// canvas/CanvasTypes.ts — 画布子组件共享的类型与常量
// 原内联在 views/CanvasStageFlow.tsx，拆出以减少 1600+ 行大文件体积。
import type { Node, NodeProps } from "@xyflow/react";
import type { CanvasNode, GenLike, InspirationKind } from "../../lib/canvasRuntime";
import type { RegexScript } from "../../lib/regexEngine";

export const CARD_W = 240;
export const SNAP_PX = 25;          // 进入吸附阈值（画布坐标）—— 选中即显示 + 拖动贴住
export const SNAP_RELEASE_PX = 50;  // 解除吸附阈值（滞回，防闪烁/乱飞）—— 略大于 2×SNAP_PX 滞回稳
export const HINT_PX = 120;         // 接近提示距离：选中节点 / 拖动 ±120px 内即显示半透明辅助线（Figma 行为）
export const INSPIRATION_CARD_W = 180;
export const INSPIRATION_CARD_H = 320; // 灵感卡 9:16 竖版（180×320 = 9:16）

/** 灵感卡 kind → 头部色条 + 图标 + 显示标签（驱动卡片视觉） */
export const INSPIRATION_META: Record<string, { color: string; icon: string; label: string }> = {
  character:         { color: "#a855f7", icon: "👤", label: "角色卡" },
  "worldbook-entry": { color: "#22c55e", icon: "📖", label: "世界书" },
  preset:            { color: "#3b82f6", icon: "⚙️", label: "预设" },
  "table-row":       { color: "#f59e0b", icon: "📊", label: "表格" },
};

export type CardNodeData = {
  node: CanvasNode;
  gens: GenLike[];
  imageUrls: string[];
  prompt: string;
  /** 卡片底部介绍文字（右键「编辑介绍文字」的自定义覆盖，优先于 prompt） */
  customLabel?: string;
  /** 用户是否手动 resize 过（true=锁定 w/h 跟随自定义尺寸，内容填满 wrapper；false/缺失=高度自适应） */
  customSize?: boolean;
  isSel: boolean;
  naturalSize: { w: number; h: number } | undefined;
  onSelect: (id: string) => void;
  onOpen: (n: CanvasNode) => void;
  onResize: (id: string, w: number, h: number) => void;
  /** 拉伸结束回调（NodeResizer onResizeEnd）：拉伸中不落 state，结束才写布局，避免投影重建闪烁 */
  onResizeEnd?: (id: string, w: number, h: number) => void;
  onImgLoaded: (url: string, w: number, h: number) => void;
  /** 卡片右键：阻止冒泡后直接弹菜单（不依赖 ReactFlow NodeWrapper 转发） */
  onNodeCtx: (e: React.MouseEvent, n: CanvasNode) => void;
  /** 灵感卡：双击触发「插入对话」/编辑/复制（在 inspiration-card 类型时使用） */
  onInspirationAction?: (action: "insert" | "copy" | "edit", n: CanvasNode) => void;
  /** 组节点：双击编辑组名（group 类型时使用） */
  onGroupRename?: (n: CanvasNode) => void;
  /** 工作流工具卡：选择完毕按钮 */
  onConfirmWorkflow?: (n: CanvasNode) => void;
  /** 工作流工具卡：运转工作流按钮 */
  onRunWorkflow?: (n: CanvasNode) => void;
  /** 工作流工具卡：更改按钮（回到未选择状态） */
  onChangeWorkflow?: (n: CanvasNode) => void;
  /** 工作流工具卡：ComfyUI 地址（未选择状态渲染 NodeCard iframe 用） */
  comfyUrl?: string;
  /** 工作流运转实时进度 0~100（null=刚提交/排队中） */
  wfProgress?: number | null;
  /** 工作流当前执行节点 id */
  wfProgressNode?: string;
  /** 剧情节点渲染：显示层正则（markdownOnly）处理楼层正文用 */
  displayRegex?: RegexScript[];
};

export type CardNodeProps = NodeProps<Node<CardNodeData, "card">>;

export type Guide = { x?: number; snapX?: boolean; y?: number; snapY?: boolean };
export type SnapAxisState = { active: boolean; pos: number };

export type ToastItem = { id: number; msg: string; kind: "info" | "error" | "success" };
export type ToastKind = ToastItem["kind"];

/** 拖放文件支持的图片扩展名 */
export const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"]);
