// 纯逻辑：URL hash 解析/构建 与 生图尺寸计算。
// 从 App.tsx 提出，脱离 1829 行大组件，接口即测试面（无需渲染整模块即可单测）。

export type View =
  | "home" | "repos" | "repo-detail" | "chat"     // 创作/资产
  | "assets"                                        // 资产库(全站聚合)
  | "workflows" | "ai-build" | "node-index"         // 工作流区
  | "models" | "node-manager"                       // 系统区
  | "tools"                                         // 多功能工具（非核心但偶尔要用的小工具）
  | "settings";                                     // 设置中心（整页路由）

import type { Repo } from "../stores/repos";

// 单段 hash 的 view 集合（无 repoId 参数），parse/build 共用避免重复。
const SIMPLE_VIEWS: View[] = ["repos", "assets", "workflows", "ai-build", "node-index", "models", "node-manager", "tools", "settings"];

// 导航模型（重设计 v2，对齐用户图一~图四）：
// - WorkMode：三种创作模式，由左上 `Demiurge ▾` 下拉切换（像 ChatGPT 切模型）。
//   三模式共用「首页」一个入口，首页内容随当前 WorkMode 变。
//   剧情模式自带自动生成，调用提前备好的格式模版；多元生成是调模版的试验台。
// - NavSection：左侧主导航目的地。home=首页(创作工作区)；三个管理类点进去钻入(左栏换子项+返回)。
export type WorkMode = "story" | "generate" | "code";

export const WORK_MODES: { id: WorkMode; label: string; hint: string }[] = [
  { id: "story", label: "剧情模式", hint: "推进剧情，高潮点自动生成并内嵌" },
  { id: "generate", label: "多元数据生成", hint: "调试格式模版的试验台" },
  { id: "code", label: "编辑模式", hint: "角色卡、作品脚本与排错" },
];

export type WorkspaceMode = "story" | "generate" | "edit";

export function workspaceModeForWire(mode: WorkMode): WorkspaceMode {
  return mode === "code" ? "edit" : mode;
}

export function isWorkMode(value: string): value is WorkMode {
  return WORK_MODES.some((m) => m.id === value);
}

export type HomeWorkspace = "need-work" | "chat" | "canvas";

export function resolveHomeWorkspace(workMode: WorkMode, hasActiveWork: boolean): HomeWorkspace {
  if (!hasActiveWork) return "need-work";
  switch (workMode) {
    case "generate": return "canvas";   // ← 画布接管多元数据生成模式
    case "story":
    case "code":    return "chat";
  }
}

export function resolveOpenedWorkRoute(workMode: WorkMode): { workMode: WorkMode; hash: string } {
  return { workMode, hash: `#/${workMode}` };
}

export type NavSection = "home" | "assets" | "workflows" | "system";

export const NAV_SECTIONS: { id: NavSection; label: string }[] = [
  { id: "home", label: "首页" },
  { id: "assets", label: "资产管理" },
  { id: "workflows", label: "工作流管理" },
  { id: "system", label: "系统管理" },
];

export function isNavSection(value: string): value is NavSection {
  return NAV_SECTIONS.some((s) => s.id === value);
}

// 三个管理类点进去钻入：左栏整体换成「返回 + 子项」。子项 id 复用老 View 语义。
export const SECTION_SUBNAV: Record<Exclude<NavSection, "home">, { id: string; label: string }[]> = {
  assets: [
    { id: "works", label: "作品" },
    { id: "character-cards", label: "角色卡" },
    { id: "worldbook", label: "世界书" },
    { id: "generations", label: "生成内容" },
    { id: "web-materials", label: "上网素材" },
  ],
  workflows: [
    { id: "templates", label: "工作流模板库" },
    { id: "ai-build", label: "AI 搭工作流" },
    { id: "node-index", label: "节点知识库" },
  ],
  system: [
    { id: "models", label: "模型下载" },
    { id: "lora-data", label: "LoRA 数据保存" },
    { id: "node-manager", label: "节点管理" },
    { id: "tools", label: "多功能工具" },
  ],
};

// URL hash <-> 视图状态：刷新后停留在当前页面
export function parseHash(): { view: View; repoId: string | null } {
  const h = decodeURIComponent(window.location.hash.replace(/^#\/?/, ""));
  const [seg, id] = h.split("/");
  if ((SIMPLE_VIEWS as string[]).includes(seg)) return { view: seg as View, repoId: null };
  if (seg === "repo" && id) return { view: "repo-detail", repoId: id };
  if (seg === "chat" && id) return { view: "chat", repoId: id };
  return { view: "home", repoId: null };
}

export function buildHash(view: View, repoId: string | null): string {
  if ((SIMPLE_VIEWS as string[]).includes(view)) return `#/${view}`;
  if (view === "repo-detail" && repoId) return `#/repo/${repoId}`;
  if (view === "chat" && repoId) return `#/chat/${repoId}`;
  return "#/home";
}

export function resolveActivityChatTarget(
  repos: readonly Repo[], threadId: string,
): { repoId: string; workId: string } | null {
  const work = repos.find((repo) => repo.id === threadId);
  if (!work?.parentId || !repos.some((repo) => repo.id === work.parentId)) return null;
  return { repoId: work.parentId, workId: work.id };
}

// 比例 + 分辨率档 → 像素宽高。最长边按档位取（1k=1280,2k=2560,4k=3840），
// 另一边按比例缩放并对齐到最接近的 16 的倍数。返回 "宽x高" 字符串。
export const ASPECTS = ["21:9", "2:1", "16:9", "4:3", "1:1", "3:4", "9:16", "1:2", "9:21"];
export const RES_TIERS: Record<string, number> = { "1k": 1280, "2k": 2560, "4k": 3840 };
export const CUSTOM_SIZE_MIN = 64;
export const CUSTOM_SIZE_MAX = 3840;
export const IMAGE_SIZE_STEP = 16;
export const IMAGE_QUALITIES = {
  auto: "自动",
  low: "低",
  medium: "中",
  high: "高",
} as const;
export type ImageQuality = keyof typeof IMAGE_QUALITIES;

// 未知兼容接口默认不发送 quality。只对白名单 GPT Image 家族启用，避免 Banana/Gemini 拒绝未知字段。
export function supportsImageQuality(modelName: string): boolean {
  return modelName.trim().toLowerCase().includes("gpt-image");
}

export function calcSize(aspect: string, tier: string): string {
  const [aw, ah] = aspect.split(":").map(Number);
  const base = RES_TIERS[tier] || 1280;
  const align = (n: number) => Math.max(IMAGE_SIZE_STEP, Math.round(n / IMAGE_SIZE_STEP) * IMAGE_SIZE_STEP);
  let w: number, h: number;
  if (aw >= ah) { w = base; h = base * (ah / aw); }  // 横向/方形：最长边=宽
  else { h = base; w = base * (aw / ah); }            // 纵向：最长边=高
  return `${align(w)}x${align(h)}`;
}

export function normalizeCustomDimension(value: unknown, fallback = 1280): number {
  const numeric = Number(value);
  const parsed = Number.isFinite(numeric) ? Math.round(numeric) : Math.round(Number(fallback));
  const bounded = Math.min(CUSTOM_SIZE_MAX, Math.max(CUSTOM_SIZE_MIN, parsed));
  return Math.round(bounded / IMAGE_SIZE_STEP) * IMAGE_SIZE_STEP;
}

export interface ResolvedImageSize {
  size: string;
  mode: "preset" | "custom" | "fallback";
  aspect: string;
  resTier: string;
}

export function resolveImageSize(
  aspect: string,
  resTier: string,
  customEnabled: boolean,
  customWidth: number,
  customHeight: number,
  supportsCustomSize: boolean,
): ResolvedImageSize {
  if (!customEnabled) {
    return { size: calcSize(aspect, resTier), mode: "preset", aspect, resTier };
  }
  const width = normalizeCustomDimension(customWidth);
  const height = normalizeCustomDimension(customHeight);
  if (supportsCustomSize) {
    return { size: `${width}x${height}`, mode: "custom", aspect: `${width}:${height}`, resTier: "custom" };
  }

  const ratio = width / height;
  const nearestAspect = ASPECTS.reduce((best, candidate) => {
    const [bw, bh] = best.split(":").map(Number);
    const [cw, ch] = candidate.split(":").map(Number);
    return Math.abs(Math.log(ratio / (cw / ch))) < Math.abs(Math.log(ratio / (bw / bh)))
      ? candidate
      : best;
  }, ASPECTS[0]);
  const longest = Math.max(width, height);
  const nearestTier = Object.entries(RES_TIERS).reduce((best, candidate) => {
    const bestDistance = Math.abs(longest - best[1]);
    const candidateDistance = Math.abs(longest - candidate[1]);
    return candidateDistance < bestDistance || (candidateDistance === bestDistance && candidate[1] > best[1])
      ? candidate
      : best;
  })[0];
  return {
    size: calcSize(nearestAspect, nearestTier),
    mode: "fallback",
    aspect: nearestAspect,
    resTier: nearestTier,
  };
}
