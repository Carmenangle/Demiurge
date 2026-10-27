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

// 导航模型：
// - NavSection：左侧主导航目的地。home=首页(创作工作区)；管理类点进去钻入(左栏换子项+返回)。
//   新手指引为独立一级项（系统管理下方），左栏钻入后子项=引导章节。
// - ⚠ WorkMode：**模式切换已废弃（2026-08-18 定案，模式下拉移除并合并进顶栏功能栏）**。
//   类型保留仅为兼容：按作品记忆 `laf_mode_<workId>`（默认 story）、wire 的 `workspace_mode`
//   字段、ChatView 输入框提示文案、顶栏「我/预设」按钮显隐。旧 hash `#/generate`、`#/code`
//   仍兼容解析。**禁止再据 WORK_MODES 推断「用户会在三种模式间选择」**；画布/对话是
//   ChatView 内部内容区切换（`laf_view_<workId>`），所有作品统一渲染 ChatView。
//   详见 docs/memory/frontend-ia.md 更正横幅与 docs/tech-manual/F-frontend/11 §3.2。
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

export type NavSection = "home" | "assets" | "workflows" | "system" | "guide";

export const NAV_SECTIONS: { id: NavSection; label: string }[] = [
  { id: "home", label: "首页" },
  { id: "assets", label: "资产管理" },
  { id: "workflows", label: "工作流管理" },
  { id: "system", label: "系统管理" },
  { id: "guide", label: "新手指引" },
];

export function isNavSection(value: string): value is NavSection {
  return NAV_SECTIONS.some((s) => s.id === value);
}

// 管理类点进去钻入：左栏整体换成「返回 + 子项」。子项 id 复用老 View 语义；
// guide 的子项与 lib/newcomerGuide.ts 章节一一对应（同步合同在 newcomerGuide.test.ts）。
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
  // 新手指引子项=引导章节（id 与 lib/newcomerGuide.ts 保持一致，label 可用短名）
  guide: [
    { id: "quick-start", label: "快速开始" },
    { id: "story", label: "剧情扮演" },
    { id: "canvas", label: "画布创作" },
    { id: "workflow", label: "AI 搭工作流" },
    // 固化流程合并为一项（2026-09-09）：页内按 01/02/03/自定义 分组；左栏只叫「固化流程」
    // （2026-09-14 用户定案：label 不列子项，分组在页内看）
    { id: "curing-flows", label: "固化流程" },
    { id: "tools", label: "多功能工具" },
  ],
};

// ── 返回链：hash → 视图意图（纯函数，App 只负责把意图落成 setState） ──
// 为什么必须有这一层：壳层的 Android 返回键与浏览器后退**只会改 URL，不会改 React 状态**。
// 各导航点早就在写 hash，但只有挂载时读一次 ⇒ 按返回「看着没反应」，历史耗尽后直接退出
// （2026-10-27 手机端实锤）。意图解析放这里是为了脱离组件即可单测。
export type RouteIntent =
  | { kind: "mode"; mode: WorkMode }
  /** browseRepoId：作品仓库两级的下钻位，只有 assets/works 用得上 */
  | { kind: "section"; section: Exclude<NavSection, "home">; subView: string; browseRepoId: string | null }
  | { kind: "home" }
  | { kind: "settings"; panel: string }
  /** 认不出的 hash（旧 `#/repo/<id>`、手输的乱串）：保持现状，不擅自跳页 */
  | { kind: "unknown" };

export function parseRouteHash(hash: string): RouteIntent {
  let raw = hash.replace(/^#\/?/, "");
  try {
    raw = decodeURIComponent(raw);
  } catch {
    // 畸形百分号转义：按原文继续判，绝不让监听器抛（抛了等于返回键失效）
  }
  const [seg, second, ...rest] = raw.split("/");
  if (!seg) return { kind: "home" };
  if (seg === "settings") return { kind: "settings", panel: second ?? "" };
  if (isWorkMode(seg)) return { kind: "mode", mode: seg };
  if (!isNavSection(seg) || seg === "home") return { kind: "unknown" };
  const items = SECTION_SUBNAV[seg];
  const subView = second && items.some((item) => item.id === second) ? second : items[0].id;
  // 下钻位只对「资产·作品」两级有意义；其余多段（如 guide 的 /doc/<路径>）忽略由页面自己监听
  const browseRepoId = seg === "assets" && subView === "works" && rest[0] === "repo" && rest[1]
    ? rest[1]
    : null;
  return { kind: "section", section: seg, subView, browseRepoId };
}

export function buildSettingsHash(panel = ""): string {
  return panel ? `#/settings/${panel}` : "#/settings";
}

export function buildWorksBrowseHash(repoId: string | null): string {
  return repoId ? `#/assets/works/repo/${encodeURIComponent(repoId)}` : "#/assets/works";
}

/** 该作品记忆的功能栏（无记录退回剧情模式）。写处在 goMode，读处在开作品与开屏恢复。 */
export function readWorkMode(workId: string): WorkMode {
  let saved = "";
  try { saved = localStorage.getItem(`laf_mode_${workId}`) || ""; } catch { /* 受限上下文没有 storage */ }
  return isWorkMode(saved) ? saved : "story";
}

/** 开屏那一次 hash 落地必须就地改写，不能用赋值。
 *  赋值会在「无 hash 的文档根」之外**多出一格历史**：手机返回键于是先退回一条画面
 *  一模一样的记录，用户看到的是「首页按第一下没反应」（2026-10-27 真机实测）。
 *  replaceState 只改当前那一条（就是文档根自己），前一站的记录不受影响；
 *  它不触发 popstate/hashchange，而这一刻的状态本来就是代码自己置的，无需回灌。 */
export function normalizeBootHash(hash: string): void {
  if (!window.location.hash) window.history.replaceState(null, "", hash);
  else window.location.hash = hash;
}

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
