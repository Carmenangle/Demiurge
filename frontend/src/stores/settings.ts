import { useEffect, useState } from "react";
import { fetchProxyStatus, getUserState, type ProxyStatus } from "../api/userState";
import { loadMobileLocalModels } from "../api/mobileRemote";
import {
  pushSettings, USER_STATE_RECONNECT_EVENT, clearUserStateFallback,
  markRemoteModelSync, markUserStateFallback,
} from "../lib/userStateSync";
import { overlayModelSettings } from "../lib/mobileModelSettings";
import { MOBILE_PROFILE, remoteActiveOnce } from "../lib/mobileProfile";
import {
  effectiveGlobalProxyUrl, globalProxyAddress, normalizeProxyMode, resolveEndpointProxy, type ProxyMode,
} from "../lib/modelProxy";
import { normalizeSearchProvider, type SearchProvider } from "../lib/searchSource";

export type ManualTheme = "bright" | "night" | "eye-care" | "green" | "gray" | "liquid-glass";
export type Theme = ManualTheme | "system";

const MANUAL_THEMES: readonly ManualTheme[] = [
  "bright",
  "night",
  "eye-care",
  "green",
  "gray",
  "liquid-glass",
];

// 模型来源三分（2026-10-30 用户定案）：云端 API / 本机 Ollama / 本地模型文件，三者互不混称。
// 对话的 local = 本机 GGUF 导入 Ollama（与视觉大模型同款链路，不新增推理运行时）。
export type ChatModelSource = "api" | "ollama" | "local";
export type RemoteModelSource = "api" | "ollama";

export const OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1";

/** 地址是否指向 Ollama。只用于旧数据归类与切换来源时的默认值，不参与运行时判定。 */
export function isOllamaEndpoint(baseUrl: string): boolean {
  const value = (baseUrl || "").toLowerCase();
  return value.includes("11434") || value.includes("ollama");
}

export interface ChatModel {
  id?: string;        // 多模型列表里的唯一 id（单模型旧数据可无）
  displayName?: string;
  source?: ChatModelSource; // 旧数据无此字段 ⇒ 按 baseUrl/ggufPath 推断
  apiKey: string;
  baseUrl: string;
  modelName: string;
  providerProfile?: "openai_compatible" | "claude_compatible";
  proxyMode?: ProxyMode;
  proxyUrl?: string; // 运行时解析值；不要求持久化
  /** 本地模型（GGUF）主文件路径；仅 source=local 有意义。 */
  ggufPath?: string;
  /** 本地模型视觉投影文件；仅视觉模型需要。 */
  mmprojPath?: string;
  /** 导入 Ollama 后使用的模型名（source=local 时用于重导入/展示）。 */
  ollamaName?: string;
}

/** 对话模型的有效来源：显式 source > ggufPath ⇒ local > Ollama 地址 ⇒ ollama > api。 */
export function chatModelSource(
  model: Pick<ChatModel, "source" | "baseUrl" | "ggufPath">,
): ChatModelSource {
  if (model.source === "local") return "local";
  if (model.source === "ollama") return "ollama";
  if (model.source === "api") return "api";
  if ((model.ggufPath || "").trim()) return "local";
  return isOllamaEndpoint(model.baseUrl) ? "ollama" : "api";
}

/** 远程模型（视觉/嵌入）的有效来源；source 缺失时按地址推断。 */
export function remoteModelSource(model: { source?: RemoteModelSource; baseUrl: string }): RemoteModelSource {
  if (model.source === "api" || model.source === "ollama") return model.source;
  return isOllamaEndpoint(model.baseUrl) ? "ollama" : "api";
}

function normalizeProviderProfile(
  value: ChatModel["providerProfile"], preserveExplicitValue: boolean,
): NonNullable<ChatModel["providerProfile"]> {
  if (preserveExplicitValue && (
    value === "claude_compatible" || value === "openai_compatible"
  )) return value;
  // Provider Profile 描述接口 wire，不描述模型家族。旧版按模型名推断会把
  // OpenAI-compatible 代理上的 Claude 错编译成 Claude wire，故一次性迁回默认档。
  return "openai_compatible";
}

// 嵌入模型（知识库 RAG 用）：OpenAI 兼容形式，可填智谱/OpenAI/Ollama 等
export type EmbeddingMode = "remote" | "local";

export interface EmbedModel {
  mode: EmbeddingMode;
  source?: RemoteModelSource; // remote 模式下区分云端 API / 本机 Ollama（两档只影响界面与默认值）
  apiKey: string;
  baseUrl: string;
  modelName: string;
  proxyMode?: ProxyMode;
  proxyUrl?: string; // 运行时解析值；不要求持久化
  /** 可选：本地嵌入模型目录；远程/Ollama 模式可留空。 */
  modelDir?: string;
  /** 可选：本地 Cross-Encoder Reranker 模型目录。 */
  rerankerDir?: string;
}

// 视觉大模型（Visual CI 用）：OpenAI 兼容形式，可填智谱/OpenAI/Ollama 等；本地模式支持 GGUF 导入 Ollama
export type VlmMode = "remote" | "local";

export interface VlmModel {
  mode: VlmMode;
  source?: RemoteModelSource; // remote 模式下区分云端 API / 本机 Ollama
  apiKey: string;
  baseUrl: string;
  modelName: string;
  proxyMode?: ProxyMode;
  proxyUrl?: string; // 运行时解析值；不要求持久化
  /** 本地 GGUF 模式：主模型文件路径（可导入 Ollama 后自动转 remote）。 */
  ggufPath?: string;
  /** 本地 GGUF 模式：视觉投影文件路径（mmproj，可自动配对）。 */
  mmprojPath?: string;
  /** 导入 Ollama 时使用的模型名。 */
  ollamaName?: string;
}

export interface ImageModel {
  id: string;
  displayName?: string;
  apiKey: string;
  baseUrl: string;
  modelName: string;
  supportsCustomSize?: boolean;
  proxyMode?: ProxyMode;
}

export interface VideoModel {
  id: string;
  displayName?: string;
  apiKey: string;
  baseUrl: string;
  modelName: string;
  proxyMode?: ProxyMode;
}

// 用户自定义的提示词风格存档：content 是整段风格模板（画风/结构/负面词，自由粘贴），
// AI 参照其组织形态来写提示词。切换器选中时 imageStyle = "preset:<id>"。
export interface StylePreset {
  id: string;
  name: string;
  content: string;
}

// 用户人设存档：可定制多个「我是谁」，按情况自由切换。name 填 {{user}} 宏，content 填 personaDescription。
// 运行时只把选中档的 name/content 透传后端（字段仍是 user_name/user_persona），后端零改。
export interface UserPersona {
  id: string;
  name: string;
  content: string;
}

// 多元数据插入预设（按作品绑定）：剧情高潮点用哪个 ComfyUI 工作流模板出图 + 可选角色 LoRA。
// 提示词由后端从高潮段生成所选 profile，运行时按 exposed 的隐藏 binding 注入原工作流字段。
// ⑥ 单角色的出图绑定：LoRA（ComfyUI 标签系用）+ 底图（gpt-image 系锁一致性用；无 LoRA 时必填）。
export interface CharacterLoraBinding {
  loraName?: string;    // 该角色的 LoRA 文件名（空=无角色 LoRA，回退风格 LoRA + 底图）
  loraWeight?: number;  // LoRA 权重（默认 0.8）
  baseImage?: string;   // 该角色底图（本地文件路径，走 local-view）；可选的一致性参考
}

// ⑦ 单角色的音色绑定：参考音轨（IndexTTS 系语音克隆，作品专属）
export interface CharacterVoiceBinding {
  voiceRef?: string;    // 参考音轨本地文件路径（<repo>/voices/<角色名>.wav，走 local-view）
}

export interface MediaInsertPreset {
  // ⑦ 三开关：剧情自动生成时按勾选类型分发（图片/视频/音频，多选）；旧预设视为 enableImage=true
  enableImage?: boolean;   // 生成图片
  enableVideo?: boolean;   // 生成视频
  enableAudio?: boolean;   // 生成音频
  templateId: string;       // 图片工作流模板 id（空=未预设，不异步出图）
  loraMode?: "none" | "single" | "multi"; // 无 LoRA / 高潮角色栈或风格兜底 / 默认风格+高潮角色栈
  appearanceSource?: "worldbook" | "character_card"; // 稳定外貌取世界书角色条目或绑定角色卡
  // 画面人称（2026-09-20）：手选优先于后端自动判定（按锚点段）。空/未设 = 自动。
  // 第一人称 ⇒ 叙事者「我」不占画面；第三人称 ⇒ 主人公算一个主体。
  viewpoint?: "first_person" | "third_person";
  // 高潮点模式「批量生图数目」（2026-09-24）：仅高潮点模式出现，最小 1、无上限。
  // 未设/0 = 1 张（单张路径逐字不变）；≥ 锚点数 ⇒ 全出，< 锚点数 ⇒ 按判据 score 降序取前 N。
  batchImageCount?: number;
  promptProfile?: string; // 存储原样（旧数据可能是已下线的 krea2；2026-10-25 起可为 custom:<id>）；使用处经 normalizePromptProfile 归一
  // 2026-10-25 提示词模版：promptProfile 为 custom:<id> 时同时存其内置协议族；
  // 模版被删时后端按该 kind 降级内置（悬空引用防护）。
  promptProfileKind?: string;
  // 视频提示词模版 id（builtin:<kind> / custom:<hex> / 空=内置默认骨架）；
  // 按当前视频模式派发（kind 不匹配 ⇒ 后端用内置，不报错）。
  videoPromptTemplateId?: string;
  qualityPrompt?: string;   // Anima 固定质量行；空则使用后端 profile 默认值
  negativePrompt?: string;  // 独立负面提示词；仅模板暴露 negative_prompt 时注入
  latentLongEdge?: 1024 | 2048 | 4096; // 用户只定最长边；Agent 决定画幅比例
  loraName?: string;        // [兼容旧数据] 单角色 LoRA 文件名；已被 characterLoras 取代
  loraWeight?: number;      // [兼容旧数据] LoRA 权重（默认 0.8）
  characterLoras?: Record<string, CharacterLoraBinding>; // ⑥ 角色名→LoRA+底图；出图按在场角色取
  styleLora?: string;       // ⑥ 兜底风格 LoRA（角色无自己的 LoRA 时用）
  styleLoraWeight?: number; // ⑥ 风格 LoRA 权重（默认 0.8）
  styleBaseImage?: string;  // ⑥ 兜底风格底图（在场角色都无底图时，gpt-image 系用）
  videoTemplateId?: string; // 视频工作流模板 id（空=不出视频，恒用图片模板）
  smartVideo?: boolean;     // 智能模态：开=剧情动态强时(motion>=2)自动改用视频模板，否则用图片
  // 首尾帧生成（用户定稿 2026-08-28）：图片按剧情首尾帧（首帧+尾帧）生成，视频模式随之
  // 变为 firstlast 剧情影片；关闭时视频为 climax 高潮点动作代入。旧预设按 videoMode 回填。
  firstlast?: boolean;
  // V1.2 视频最小事实：时长与镜头运动是用户预设（不从模型输出读），映射视频模板 exposed 隐藏 binding；
  // 空值 = 模板原值（旧预设兼容，不迁移不报错）。
  videoDurationHint?: number;   // 视频时长（秒）；0/空=模板原值
  videoCamera?: "static" | "pan" | "zoom"; // 镜头运动；空=模板原值
  // V1.6/W3 转场视频时长（坑G）：短桥段不预设死值，有值才写（缺省交视频模型默认，绝不兑底正片时长）
  transitionDurationHint?: number;
  // V1.5/B1 视频模式：climax=高潮点动作代入（精简版）；firstlast=首尾帧剧情影片（七段式）。
  // [兼容旧数据] 现由 firstlast 选项推导（见上），保留字段仅为旧预设兼容。
  videoMode?: "climax" | "firstlast";
  // ⑦ 音频（IndexTTS 系）：模板 + 按角色参考音轨（每个作品专属、每角色一个）
  audioTemplateId?: string;  // 音频工作流模板 id（空=不配音）
  characterVoices?: Record<string, CharacterVoiceBinding>; // 角色名→参考音轨（音色）
}

// 液态玻璃主题（独立主题）的完整控制台配置。语义对齐 dsh-plugin-wallpaper-engine：
// glassAlpha / sidebarAlpha / contentAlpha 越大越透明；其余滑杆为直观百分比。
export type LiquidGlassWallpaperKind = "image" | "video";
export type LiquidGlassRopeForm = "icon" | "orb";
export type LiquidGlassFit = "cover" | "contain" | "center" | "fill";

export interface LiquidGlassWallpaper {
  id: string;
  path: string;
  name: string;
  kind: LiquidGlassWallpaperKind;
  preview?: string;   // 工坊作品自带预览图（GIF/PNG），优先用作列表封面
  lowRes?: boolean;   // 只有作品预览图且分辨率过低（该作品浏览器渲染不了，只能贴预览图）
  workshopId?: string; // 创意工坊作品 id（列表/跳转用，渲染不再按它取包）
  scenePkg?: boolean;  // true=作品带 scene.pkg，可在页面内实时渲染而非只用静态预览
  /**
   * 场景包来源标识（后端 `scene_package` 的唯一入参）：`workshop:<id>` 或 `local:<绝对路径>`。
   * 与 workshopId 分开是因为本地文件夹里的 Wallpaper Engine 工程没有工坊 id，但仍能实时渲染。
   */
  sceneSource?: string;
  /**
   * 该壁纸自己的 WE 预设覆盖值（键=general.properties 里的属性名）。
   * 只存用户改过的键；没写的键由渲染器回落到工程默认值，用户在 Wallpaper Engine
   * 里的取值则由后端在首次读取时作为初值给出（见 api/userState.fetchWallpaperSceneProperties）。
   */
  preset?: Record<string, string | number | boolean>;
}

export interface LiquidGlassSettings {
  accent: string;              // 主色（按钮/滑杆/选中态/高光）
  glassColor: string;          // 玻璃基色
  glassAlpha: number;          // 玻璃透明度 0~60（越大越透）
  glassBlur: number;           // 玻璃模糊 px
  border: number;              // 玻璃边框强度 0~100
  scrim: number;               // 壁纸暗化 0~100
  glassWindow: boolean;        // 设置窗口玻璃
  sidebarGlass: boolean;       // 侧栏玻璃
  sidebarBlur: number;         // 侧栏模糊 px
  sidebarAlpha: number;        // 侧栏透明度 0~200（越大越透）
  sidebarColor: string;        // 侧栏玻璃基色
  contentAlpha: number;        // 内容面透明度 0~80（越大越透）
  contentColor: string;        // 内容面底色（空=默认深色玻璃面）
  wallpapers: LiquidGlassWallpaper[];
  activeWallpaperId: string;
  wallpaperDir: string;        // 壁纸库存放地址（扫描该文件夹生成预览，不写入其中）
  wallpaperHidden: string[];   // 壁纸库中「已隐藏」的路径（不删除源文件）
  wallpaperStoreDir: string;   // 自定义壁纸存储位置（空=后端默认；支持 ~ 表示用户主目录）
  wallpaperFit: LiquidGlassFit;
  wallpaperBlur: number;       // 壁纸模糊 px
  wallpaperBrightness: number; // 亮度 %（100=原样）
  wallpaperContrast: number;   // 对比度 %
  wallpaperSaturate: number;   // 饱和度 %
  flip: boolean;               // 水平翻转
  playbackRate: number;        // 视频倍速 0.5~2
  pauseOnHidden: boolean;      // 最小化/切标签页暂停
  pauseOnBlur: boolean;        // 窗口失焦暂停
  pauseOnBattery: boolean;     // 电池供电暂停
  wallpaperPaused: boolean;    // 用户手动暂停
  rotationEnabled: boolean;    // 自动轮换
  rotationInterval: number;    // 轮换间隔（秒）
  ropeShown: boolean;          // 显示右侧拉绳
  ropeForm: LiquidGlassRopeForm;
  ropeScale: number;           // 拉绳大小 0.5~2.5
}

export const DEFAULT_LIQUID_GLASS: LiquidGlassSettings = {
  accent: "#8fb4ff",
  glassColor: "#ffffff",
  glassAlpha: 12,
  glassBlur: 24,
  border: 35,
  scrim: 25,
  glassWindow: true,
  sidebarGlass: true,
  sidebarBlur: 24,
  sidebarAlpha: 120,
  sidebarColor: "#ffffff",
  contentAlpha: 30,
  contentColor: "#0d1524",
  wallpapers: [],
  activeWallpaperId: "",
  wallpaperDir: "",
  wallpaperHidden: [],
  wallpaperStoreDir: "",
  wallpaperFit: "cover",
  wallpaperBlur: 0,
  wallpaperBrightness: 100,
  wallpaperContrast: 100,
  wallpaperSaturate: 100,
  flip: false,
  playbackRate: 1,
  pauseOnHidden: true,
  pauseOnBlur: false,
  pauseOnBattery: false,
  wallpaperPaused: false,
  rotationEnabled: false,
  rotationInterval: 30,
  ropeShown: true,
  ropeForm: "icon",
  ropeScale: 1,
};

const HEX_COLOR_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;
const LIQUID_GLASS_FITS: readonly LiquidGlassFit[] = ["cover", "contain", "center", "fill"];
const LIQUID_GLASS_ROPE_FORMS: readonly LiquidGlassRopeForm[] = ["icon", "orb"];

function normalizeHexColor(value: unknown, fallback: string): string {
  return typeof value === "string" && HEX_COLOR_RE.test(value) ? value : fallback;
}

function clampLiquidGlassNumber(
  value: unknown, lo: number, hi: number, fallback: number, roundInt = false,
): number {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  const clamped = Math.min(hi, Math.max(lo, n));
  return roundInt ? Math.round(clamped) : clamped;
}

export function liquidGlassWallpaperKind(path: string): LiquidGlassWallpaperKind {
  return /\.(mp4|webm|mov|m4v|ogv)(?:[?#].*)?$/i.test(path) ? "video" : "image";
}

export function normalizeLiquidGlass(raw: unknown): LiquidGlassSettings {
  const o = (raw && typeof raw === "object" ? raw : {}) as Partial<LiquidGlassSettings>;
  const wallpapers: LiquidGlassWallpaper[] = [];
  const seenPaths = new Set<string>();
  for (const item of Array.isArray(o.wallpapers) ? o.wallpapers : []) {
    if (!item || typeof item !== "object") continue;
    const path = typeof item.path === "string" ? item.path.trim() : "";
    if (!path || seenPaths.has(path)) continue;
    seenPaths.add(path);
    const preview = typeof item.preview === "string" ? item.preview.trim() : "";
    // scene 实时渲染的两条线必须一起留：只有 id 没有 pkg 标记（或反之）都渲染不出来，
    // 少了这一步，重载后作品会静默退回静态预览（2026-10-23 第三刀）。
    const workshopId = typeof item.workshopId === "string" ? item.workshopId.trim() : "";
    const sceneSource = typeof item.sceneSource === "string" ? item.sceneSource.trim() : "";
    // 场景来源既可能是工坊 id，也可能是本地工程目录：只认工坊会把本地工程判成「不能实时渲染」。
    const scenePkg = item.scenePkg === true && (sceneSource !== "" || /^\d{1,20}$/.test(workshopId));
    const preset: Record<string, string | number | boolean> = {};
    if (item.preset && typeof item.preset === "object") {
      for (const [key, value] of Object.entries(item.preset)) {
        if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
          preset[key] = value;
        }
      }
    }
    wallpapers.push({
      id: typeof item.id === "string" && item.id.trim() ? item.id : crypto.randomUUID(),
      path,
      name: typeof item.name === "string" && item.name.trim()
        ? item.name.trim()
        : path.split(/[\\/]/).pop() || "壁纸",
      kind: liquidGlassWallpaperKind(path),
      ...(preview ? { preview } : {}),
      ...(item.lowRes === true ? { lowRes: true } : {}),
      ...(workshopId ? { workshopId } : {}),
      ...(sceneSource ? { sceneSource } : {}),
      ...(scenePkg ? { scenePkg: true } : {}),
      ...(Object.keys(preset).length > 0 ? { preset } : {}),
    });
  }
  // 显式空串＝用户主动「关闭壁纸」，重载后必须保持关闭；缺失/失效才回退到第一张。
  const activeId = o.activeWallpaperId === ""
    ? ""
    : wallpapers.some((w) => w.id === o.activeWallpaperId)
      ? String(o.activeWallpaperId)
      : (wallpapers[0]?.id || "");
  const hidden: string[] = [];
  const seenHidden = new Set<string>();
  for (const p of Array.isArray(o.wallpaperHidden) ? o.wallpaperHidden : []) {
    if (typeof p !== "string") continue;
    const path = p.trim();
    if (!path || seenHidden.has(path)) continue;
    seenHidden.add(path);
    hidden.push(path);
  }
  return {
    accent: normalizeHexColor(o.accent, DEFAULT_LIQUID_GLASS.accent),
    glassColor: normalizeHexColor(o.glassColor, DEFAULT_LIQUID_GLASS.glassColor),
    glassAlpha: clampLiquidGlassNumber(o.glassAlpha, 0, 60, DEFAULT_LIQUID_GLASS.glassAlpha, true),
    glassBlur: clampLiquidGlassNumber(o.glassBlur, 0, 60, DEFAULT_LIQUID_GLASS.glassBlur, true),
    border: clampLiquidGlassNumber(o.border, 0, 100, DEFAULT_LIQUID_GLASS.border, true),
    scrim: clampLiquidGlassNumber(o.scrim, 0, 100, DEFAULT_LIQUID_GLASS.scrim, true),
    glassWindow: o.glassWindow !== false,
    sidebarGlass: o.sidebarGlass !== false,
    sidebarBlur: clampLiquidGlassNumber(o.sidebarBlur, 0, 60, DEFAULT_LIQUID_GLASS.sidebarBlur, true),
    sidebarAlpha: clampLiquidGlassNumber(o.sidebarAlpha, 0, 200, DEFAULT_LIQUID_GLASS.sidebarAlpha, true),
    sidebarColor: normalizeHexColor(o.sidebarColor, DEFAULT_LIQUID_GLASS.sidebarColor),
    contentAlpha: clampLiquidGlassNumber(o.contentAlpha, 0, 80, DEFAULT_LIQUID_GLASS.contentAlpha, true),
    contentColor: o.contentColor === ""
      ? ""
      : normalizeHexColor(o.contentColor, DEFAULT_LIQUID_GLASS.contentColor),
    wallpapers,
    activeWallpaperId: activeId,
    wallpaperDir: typeof o.wallpaperDir === "string"
      ? o.wallpaperDir.trim().replace(/^"(.*)"$/, "$1").trim()
      : "",
    wallpaperHidden: hidden,
    wallpaperStoreDir: typeof o.wallpaperStoreDir === "string"
      ? o.wallpaperStoreDir.trim().replace(/^"(.*)"$/, "$1").trim()
      : "",
    wallpaperFit: LIQUID_GLASS_FITS.includes(o.wallpaperFit as LiquidGlassFit)
      ? o.wallpaperFit as LiquidGlassFit
      : DEFAULT_LIQUID_GLASS.wallpaperFit,
    wallpaperBlur: clampLiquidGlassNumber(o.wallpaperBlur, 0, 60, DEFAULT_LIQUID_GLASS.wallpaperBlur, true),
    wallpaperBrightness: clampLiquidGlassNumber(o.wallpaperBrightness, 20, 160, DEFAULT_LIQUID_GLASS.wallpaperBrightness, true),
    wallpaperContrast: clampLiquidGlassNumber(o.wallpaperContrast, 20, 160, DEFAULT_LIQUID_GLASS.wallpaperContrast, true),
    wallpaperSaturate: clampLiquidGlassNumber(o.wallpaperSaturate, 0, 200, DEFAULT_LIQUID_GLASS.wallpaperSaturate, true),
    flip: o.flip === true,
    playbackRate: clampLiquidGlassNumber(o.playbackRate, 0.5, 2, DEFAULT_LIQUID_GLASS.playbackRate),
    pauseOnHidden: o.pauseOnHidden !== false,
    pauseOnBlur: o.pauseOnBlur === true,
    pauseOnBattery: o.pauseOnBattery === true,
    wallpaperPaused: o.wallpaperPaused === true,
    rotationEnabled: o.rotationEnabled === true,
    rotationInterval: clampLiquidGlassNumber(o.rotationInterval, 10, 600, DEFAULT_LIQUID_GLASS.rotationInterval, true),
    ropeShown: o.ropeShown !== false,
    ropeForm: LIQUID_GLASS_ROPE_FORMS.includes(o.ropeForm as LiquidGlassRopeForm)
      ? o.ropeForm as LiquidGlassRopeForm
      : DEFAULT_LIQUID_GLASS.ropeForm,
    ropeScale: clampLiquidGlassNumber(o.ropeScale, 0.5, 2.5, DEFAULT_LIQUID_GLASS.ropeScale),
  };
}

export function activeLiquidGlassWallpaper(s: Settings): LiquidGlassWallpaper | null {
  return s.liquidGlass.wallpapers.find((w) => w.id === s.liquidGlass.activeWallpaperId) || null;
}

/**
 * 场景壁纸的来源标识（后端 scene 通道的唯一入参）。
 * 旧存档只有 workshopId、没有 sceneSource，按工坊口径补出来，避免升级后已应用的
 * 工坊壁纸失去实时渲染；非 scene 作品返回空串。
 */
export function liquidGlassSceneSource(w: LiquidGlassWallpaper | null | undefined): string {
  if (!w || w.scenePkg !== true) return "";
  if (w.sceneSource) return w.sceneSource;
  return w.workshopId ? `workshop:${w.workshopId}` : "";
}


/** ComfyUI 地址簿里的一台额外机台。`id` 稳定（路由引用它），`name` 只作显示。 */
export interface ComfyMachine {
  id: string;
  name: string;
  url: string;
}

/** 按模态选机：值是 `ComfyMachine.id`；缺省＝条目 0（`comfyuiUrl`）。 */
export interface ComfyRoute {
  image?: string;
  video?: string;
  audio?: string;
}

export interface Settings {
  theme: Theme;
  liquidGlass: LiquidGlassSettings; // 液态玻璃独立主题的全套外观配置（其他主题忽略）
  chatModels: ChatModel[];          // 对话模型（可多个供应商）
  activeChatModelId?: string;       // 当前选中的对话模型 id
  providerProfileSemanticsVersion?: number; // v2 起只保留用户明确选择，禁止按模型名猜 wire
  embedModel: EmbedModel;  // 知识库 RAG 嵌入模型
  vlmModel: VlmModel;      // 视觉大模型（Visual CI 验收用）
  imageModels: ImageModel[];
  activeImageModelId?: string;
  videoModels: VideoModel[];
  activeVideoModelId?: string;
  imageStyle?: string;  // [旧·兼容] 生图提示词风格：""(自动)/sd/gpt/banana，或 "preset:<id>" 指向自定义存档；2026-10-25 起由「提示词模版」接管（见 promptTemplateId），旧数据一次性迁移后不再写
  stylePresets?: StylePreset[];  // [旧·兼容] 用户自定义风格存档；已迁移进提示词模版（promptTemplatesMigrated 后清空）
  // 2026-10-25 提示词模版：全局选中的生图提示词模版（builtin:<kind> / custom:<hex> / 空=不启用）。
  // 选中自定义模版时其「追加指令」作为 styleTemplate 透传后端（替代旧风格存档）。
  promptTemplateId?: string;
  promptTemplatesMigrated?: boolean;  // 旧风格存档一次性迁移完成标记（失败保留旧数据下轮再试）
  workflowDir: string; // 工作流默认读取路径（后端扫描该目录及子目录的 .json）
  outputDir: string; // 仓库文件夹：作品私有产物（图片+会话记录+好感度+往事+参考图）落 <此>/<作品名>/
  characterDir: string; // 角色卡文件夹：导入的卡按「小仓库」落此目录（含卡本体+内嵌世界书/正则+对话）
  worldbookDir: string; // 世界书文件夹：独立世界书文件落此目录（Phase 2）
  presetDir: string; // 偏置预设文件夹：ST OpenAI 预设落此目录（仅剧情模式用）
  activePresetName: string; // 当前激活的偏置预设名（空=不用预设，走内置扮演提示）
  userName: string; // [兼容旧数据] 用户人设名；已迁移进 userPersonas，运行时读选中档
  userPersona: string; // [兼容旧数据] 用户人设描述；已迁移进 userPersonas
  userPersonas?: UserPersona[]; // 用户人设多档：可定制多个「我是谁」自由切换
  activeUserPersonaId?: string; // 当前选中的用户人设 id（空=不注入用户人设）
  illustrate: boolean; // 剧情插画开关：开=高潮点自动配图（复用生图模型，能动性 D 阶段）
  promptOnlyGeneration: boolean; // 试跑：只生成提示词，**不提交 ComfyUI**（图片/视频都不出图），成稿照常上报 trace 供审查
  mediaInsert?: Record<string, MediaInsertPreset>; // 多元数据插入预设，按作品(repoId)绑定 ComfyUI 模板+LoRA
  comfyuiPath: string; // ComfyUI 本体目录（含 main.py），用于后端启动
  comfyuiPython: string; // ComfyUI 自己的 Python；禁止使用本工具 Runtime 代替
  comfyuiUrl: string; // ComfyUI 访问地址，iframe 嵌入与 API 调用
  /** ComfyUI 额外机台（条目 0 就是 comfyuiUrl 本身，不在这里重复）。
   *  name 只是标签，**URL 才是真值**——历史/重新生成记的是 URL，改名不断历史。 */
  comfyExtra?: ComfyMachine[];
  /** 各模态用哪台机（值是 comfyExtra 的 id；省略＝跟随条目 0）。 */
  comfyRoute?: ComfyRoute;
  modelsDir: string; // ComfyUI models 目录（模型下载落盘，留空则用 comfyuiPath/models）
  hfToken: string; // HuggingFace token（下载鉴权模型用）
  civitaiToken: string; // Civitai API key（下载鉴权模型用）
  proxyUrl: string; // 联网搜索代理地址（手填兜底值；系统代理探测不到时用它）
  proxyEnabled: boolean; // 是否启用代理（关则直连外网）。由「继承全局」探测自动写回，不是用户开关
  // 2026-09-10「继承全局」升级：系统代理（WinINET/环境变量）探测结果。非空且监听时优先于 proxyUrl。
  systemProxyUrl?: string; // 探测到的系统代理地址（空=系统没配代理）
  systemProxySource?: "system" | "env" | "manual" | "none"; // 生效来源，供界面说明
  searchProvider?: SearchProvider; // 联网搜索源（默认 auto=后端按 bing-cn→ddg 回落）
  smitheryKey?: string; // Smithery MCP 市场 API Key（浏览/连接托管 MCP 服务器用）
  pixivRefreshToken?: string; // pixiv refresh token（「上网素材 → pixiv 市场」搜索/下载用；OAuth 令牌已含账号身份）
  danbooruLogin?: string; // danbooru 用户名（与 API Key 配对做 Basic 鉴权；单独 key 不生效）
  danbooruApiKey?: string; // danbooru API key（可选：与用户名配对可提额；都不填=匿名搜索）
  chatBgPath?: string; // 小仓库对话背景图（本地文件路径，走 local-view 读取）
  chatBgOpacity?: number; // 对话背景透明度 0~1（默认 0.15）
  chatBgFit?: "cover" | "contain"; // 填充方式：cover 铺满裁剪 / contain 完整显示
  chatBgScale?: number; // 缩放 0.5~2（默认 1）
  chatBgPosX?: number; // 水平位置 0~100（默认 50 居中）
  chatBgPosY?: number; // 垂直位置 0~100（默认 50 居中）
  activeAgentId?: string; // 当前对话选中的 Agent 预设 id（空=内置默认行为）
  agentAccessMode?: "approval" | "full"; // 智能编造 Agent 访问标准：approval=允许后访问（默认）/ full=完全访问
  streamOutput: boolean; // 智能体回复是否按模型增量实时输出
  contextReminderTokens: number; // 累计上下文达到该估算 token 数时提醒压缩
  contextMaxTokens: number; // 每轮传给 Agent 的历史上下文估算 token 硬上限（0=无限，仍受模型窗口钳制）；上下文长度的唯一用户设置
  compactTriggerPct: number; // 历史占用达有效预算该百分比即自动压缩（LLM 摘要旧段；0=关闭；默认 80）
  selfhealAttempts: number; // 截断自愈次数上限（0=不自愈，直接报错；默认 3）
  // 2026-09-04 设置整合：智能体参数预设（温度/采样/输入输出上限）的权威来源——不再由
  // preset JSON 顶层字段决定。默认值取自 GrayWill-0.46-demiurge.json（demiurge 版基线）。
  presetSampling: PresetSampling;
  // 通用：不知道该放哪个分区的设置项
  galleryRemoveFile: boolean; // 资产库删除时默认同时删除本机图片文件
}

export interface PresetSampling {
  temperature: number;            // 0~2
  topP: number;                   // 0~1
  topK: number;                   // >=1
  frequencyPenalty: number;       // -2~2
  presencePenalty: number;        // -2~2
  openaiMaxContext: number;       // 输入上下文窗口上限 tokens（>=1000）
  openaiMaxTokens: number;        // 单次输出上限 tokens（>=256），最终受 cap_min 收敛到模型上限
}

export const DEFAULT_CONTEXT_REMINDER_TOKENS = 12_000;
export const DEFAULT_CONTEXT_MAX_TOKENS = 20_000;
export const DEFAULT_COMPACT_TRIGGER_PCT = 80;
export const DEFAULT_SELFHEAL_ATTEMPTS = 3;

// 2026-09-04：智能体参数预设默认值 = GrayWill-0.46-demiurge.json 顶层基线。
// 用户在「设置 → 智能体 → 智能体参数预设」可改；未改走默认，与历史行为一致。
// cap_min 仍生效：openai_max_tokens=600000 落到 deepseek-v4-flash 上限 393216 钳到 393216。
export const DEFAULT_PRESET_SAMPLING: PresetSampling = {
  temperature: 1,
  topP: 0.98,
  topK: 50,
  frequencyPenalty: 0,
  presencePenalty: 0,
  openaiMaxContext: 2_000_000,
  openaiMaxTokens: 600_000,
};

export function normalizePresetSampling(raw: Partial<PresetSampling> | null | undefined): PresetSampling {
  // 钳制：参照 wire schema 的范围（min/max）。非法/缺失字段逐项回退到 GrayWill 默认。
  const n = (v: unknown, lo: number, hi: number, fallback: number, roundInt = false): number => {
    const x = Number(v);
    if (!Number.isFinite(x)) return fallback;
    const c = Math.min(hi, Math.max(lo, x));
    return roundInt ? Math.round(c) : c;
  };
  return {
    temperature: n(raw?.temperature, 0, 2, DEFAULT_PRESET_SAMPLING.temperature),
    topP: n(raw?.topP, 0, 1, DEFAULT_PRESET_SAMPLING.topP),
    topK: Math.round(n(raw?.topK, 1, Number.MAX_SAFE_INTEGER, DEFAULT_PRESET_SAMPLING.topK, true)),
    frequencyPenalty: n(raw?.frequencyPenalty, -2, 2, DEFAULT_PRESET_SAMPLING.frequencyPenalty),
    presencePenalty: n(raw?.presencePenalty, -2, 2, DEFAULT_PRESET_SAMPLING.presencePenalty),
    openaiMaxContext: Math.round(n(raw?.openaiMaxContext, 1_000, 4_000_000, DEFAULT_PRESET_SAMPLING.openaiMaxContext, true)),
    openaiMaxTokens: Math.round(n(raw?.openaiMaxTokens, 256, 4_000_000, DEFAULT_PRESET_SAMPLING.openaiMaxTokens, true)),
  };
}

export function normalizeContextBudgets(reminder: unknown, max: unknown) {
  const parsedMax = Number(max);
  // max=0 表示「无上限」（剧情模式不裁历史，全量入上下文）；其余仍钳到 [4000, 200000]。
  const safeMax = Number.isFinite(parsedMax) && parsedMax <= 0
    ? 0
    : Number.isFinite(parsedMax)
      ? Math.min(200_000, Math.max(4_000, Math.round(parsedMax)))
      : DEFAULT_CONTEXT_MAX_TOKENS;
  // 无上限时提醒值不受 max 约束，只需 >=1000；有上限时仍须低于上限 1000。
  const reminderCeil = safeMax === 0 ? Number.MAX_SAFE_INTEGER : safeMax - 1_000;
  const parsedReminder = Number(reminder);
  const safeReminder = Number.isFinite(parsedReminder)
    ? Math.min(reminderCeil, Math.max(1_000, Math.round(parsedReminder)))
    : Math.min(DEFAULT_CONTEXT_REMINDER_TOKENS, reminderCeil);
  return { reminder: safeReminder, max: safeMax };
}

// Demiurge 专用 key：与源项目 ComfyUI-Wrapping-paper（同为 127.0.0.1:5173 同源、旧 key laf_settings）
// 隔离 localStorage，避免两项目串数据（源项目模型配置/密钥漏进 Demiurge）。不迁移旧 key，干净起步。
const KEY = "demiurge_settings";

// 手机默认云端嵌入（2026-10-30 用户定案）：手机上不存在「本机 Ollama」，把回环默认值摆出来
// 只会误导。默认给云端 API 空档（填地址/Key 即用）；没填可用配置时，执行请求的电脑会
// 自动回退到它自己的嵌入设置（见后端 services/embed_resolution）。
const MOBILE_DEFAULT_EMBED: EmbedModel = {
  mode: "remote", source: "api", apiKey: "", baseUrl: "", modelName: "", proxyMode: "inherit",
};

const DEFAULT: Settings = {
  theme: "system",
  liquidGlass: DEFAULT_LIQUID_GLASS,
  chatModels: [],
  embedModel: MOBILE_PROFILE
    ? MOBILE_DEFAULT_EMBED
    : { mode: "remote", source: "ollama", apiKey: "ollama", baseUrl: OLLAMA_DEFAULT_BASE_URL, modelName: "qwen3-embedding:latest", proxyMode: "on" },
  vlmModel: { mode: "remote", source: "ollama", apiKey: "ollama", baseUrl: OLLAMA_DEFAULT_BASE_URL, modelName: "gemma4:latest", proxyMode: "on" },
  imageModels: [],
  videoModels: [],
  imageStyle: "",
  stylePresets: [],
  promptTemplateId: "",
  promptTemplatesMigrated: false,
  workflowDir: "",
  outputDir: "",
  characterDir: "",
  worldbookDir: "",
  presetDir: "",
  activePresetName: "",
  userName: "",
  userPersona: "",
  userPersonas: [],
  illustrate: false,
  promptOnlyGeneration: false,
  mediaInsert: {},
  comfyuiPath: "",
  comfyuiPython: "",
  comfyuiUrl: "http://127.0.0.1:8188",
  comfyExtra: [],
  comfyRoute: {},
  modelsDir: "",
  hfToken: "",
  civitaiToken: "",
  proxyUrl: "http://127.0.0.1:7897",
  proxyEnabled: true,
  systemProxyUrl: "",
  systemProxySource: "none",
  searchProvider: "auto",
  smitheryKey: "",
  pixivRefreshToken: "",
  danbooruLogin: "",
  danbooruApiKey: "",
  chatBgPath: "",
  chatBgOpacity: 0.15,
  chatBgFit: "cover",
  chatBgScale: 1,
  chatBgPosX: 50,
  chatBgPosY: 50,
  streamOutput: false,
  contextReminderTokens: DEFAULT_CONTEXT_REMINDER_TOKENS,
  contextMaxTokens: DEFAULT_CONTEXT_MAX_TOKENS,
  compactTriggerPct: DEFAULT_COMPACT_TRIGGER_PCT,
  selfhealAttempts: DEFAULT_SELFHEAL_ATTEMPTS,
  presetSampling: DEFAULT_PRESET_SAMPLING,
  agentAccessMode: "approval",
  galleryRemoveFile: false,
};

// 旧数据迁移：单 chatModel 字段 → chatModels 列表（向后兼容）
function migrate(s: Record<string, unknown>): Settings {
  const merged = { ...DEFAULT, ...s } as Settings & { chatModel?: ChatModel };
  merged.theme = normalizeTheme(s.theme);
  merged.liquidGlass = normalizeLiquidGlass(s.liquidGlass);
  merged.streamOutput = s.streamOutput === true;
  merged.agentAccessMode = s.agentAccessMode === "full" ? "full" : "approval";
  merged.searchProvider = normalizeSearchProvider(s.searchProvider);
  const savedEmbed = (s.embedModel || {}) as Partial<EmbedModel>;
  merged.embedModel = {
    ...DEFAULT.embedModel,
    ...savedEmbed,
    mode: savedEmbed.mode === "local" || savedEmbed.mode === "remote"
      ? savedEmbed.mode
      : savedEmbed.modelDir?.trim() ? "local" : "remote",
    // 旧数据只有 baseUrl：按地址补来源（11434/ollama ⇒ Ollama），新数据保留用户显式选择。
    source: remoteModelSource({
      source: savedEmbed.source,
      baseUrl: String(savedEmbed.baseUrl ?? DEFAULT.embedModel.baseUrl),
    }),
    proxyMode: normalizeProxyMode(savedEmbed.proxyMode),
  };
  const savedVlm = (s.vlmModel || {}) as Partial<VlmModel>;
  merged.vlmModel = {
    ...DEFAULT.vlmModel,
    ...savedVlm,
    mode: savedVlm.mode === "local" || savedVlm.mode === "remote"
      ? savedVlm.mode
      : (savedVlm.ggufPath?.trim() || savedVlm.mmprojPath?.trim()) ? "local" : "remote",
    source: remoteModelSource({
      source: savedVlm.source,
      baseUrl: String(savedVlm.baseUrl ?? DEFAULT.vlmModel.baseUrl),
    }),
    proxyMode: normalizeProxyMode(savedVlm.proxyMode),
  };
  const contextBudgets = normalizeContextBudgets(
    s.contextReminderTokens,
    s.contextMaxTokens,
  );
  merged.contextReminderTokens = contextBudgets.reminder;
  merged.contextMaxTokens = contextBudgets.max;
  // 2026-09-11 上下文 token 化：条数设置（contextCount/旧 historyPerRole）废弃移除，
  // 上下文长度由 token 预算唯一决定；新增压缩触发百分比。
  delete (merged as unknown as Record<string, unknown>).contextCount;
  delete (merged as unknown as Record<string, unknown>).historyPerRole;
  const parsedCompact = Number(s.compactTriggerPct);
  merged.compactTriggerPct = Number.isFinite(parsedCompact)
    ? Math.min(100, Math.max(0, Math.round(parsedCompact)))
    : DEFAULT_COMPACT_TRIGGER_PCT;
  const parsedSelfheal = Number(s.selfhealAttempts);
  merged.selfhealAttempts = Number.isFinite(parsedSelfheal)
    ? Math.min(5, Math.max(0, Math.round(parsedSelfheal)))
    : DEFAULT_SELFHEAL_ATTEMPTS;
  // 2026-09-04：旧 settings 缺 presetSampling → 逐字段 normalize，缺/非法回退 GrayWill 默认。
  merged.presetSampling = normalizePresetSampling(s.presetSampling as Partial<PresetSampling> | undefined);
  if ((!merged.chatModels || merged.chatModels.length === 0) && merged.chatModel) {
    const old = merged.chatModel;
    if (old.baseUrl || old.modelName || old.apiKey) {
      const id = crypto.randomUUID();
      merged.chatModels = [{ ...old, id }];
      merged.activeChatModelId = id;
    }
  }
  const preserveProviderProfile = Number(s.providerProfileSemanticsVersion) >= 2;
  // 给缺 id 的对话模型补 id；旧版曾按模型名自动写 profile，v2 一次性迁回 OpenAI wire。
  merged.chatModels = (merged.chatModels || []).map((m) => ({
    ...m, id: m.id || crypto.randomUUID(), proxyMode: normalizeProxyMode(m.proxyMode),
    providerProfile: normalizeProviderProfile(m.providerProfile, preserveProviderProfile),
    source: chatModelSource(m),
  }));
  merged.providerProfileSemanticsVersion = 2;
  merged.imageModels = (merged.imageModels || []).map((m) => ({
    ...m, proxyMode: normalizeProxyMode(m.proxyMode),
  }));
  merged.videoModels = (merged.videoModels || []).map((m) => ({
    ...m, proxyMode: normalizeProxyMode(m.proxyMode),
  }));
  delete merged.chatModel;
  // 用户人设：旧单值(userName/userPersona) → 迁进 userPersonas[0] 并设为选中
  merged.userPersonas = (merged.userPersonas || []).map((p) =>
    p.id ? p : { ...p, id: crypto.randomUUID() },
  );
  if (merged.userPersonas.length === 0 && (merged.userName?.trim() || merged.userPersona?.trim())) {
    const id = crypto.randomUUID();
    merged.userPersonas = [{ id, name: merged.userName || "", content: merged.userPersona || "" }];
    merged.activeUserPersonaId = id;
  }
  return merged;
}

export function normalizeTheme(value: unknown): Theme {
  // 旧白天主题就是现在的米黄护眼方案；旧暗色归入夜间方案。
  if (value === "light") return "eye-care";
  if (value === "dark") return "night";
  if (value === "system" || MANUAL_THEMES.includes(value as ManualTheme)) return value as Theme;
  return DEFAULT.theme;
}

// localStorage 持久化剥离密钥（2026-10-24 XSS→密钥链加固）：localStorage 可被任意同源
// 脚本/扩展读取，且与「密钥只落后端 user_state.json」的架构合同漂移。写盘前清空全部
// 密钥/令牌字段（与 exportSettings(keepKeys=false) 同一张清单，改字段两边同步）；
// 内存里的 settings 不受影响（编辑/回传后端照常用真值），密钥真源在后端，启动水合回填。
export function stripSecrets(s: Settings): Settings {
  const out: Settings = JSON.parse(JSON.stringify(s));
  out.chatModels = (out.chatModels || []).map((m) => ({ ...m, apiKey: "" }));
  out.imageModels = (out.imageModels || []).map((m) => ({ ...m, apiKey: "" }));
  out.videoModels = (out.videoModels || []).map((m) => ({ ...m, apiKey: "" }));
  out.embedModel = { ...out.embedModel, apiKey: "" };
  out.vlmModel = { ...out.vlmModel, apiKey: "" };
  out.hfToken = "";
  out.civitaiToken = "";
  out.smitheryKey = "";
  out.pixivRefreshToken = "";
  out.danbooruLogin = "";
  out.danbooruApiKey = "";
  return out;
}

// persist/load 的 Storage 参数是测试注入缝：jsdom 30 的 localStorage 在 Node 25 下不可用
// （Node 内置桩遮蔽，见 ARCHITECTURE 条 51），测试注入 MemoryStorage——lastSelection.ts 同款惯例。
function persist(s: Settings, storage: Storage = window.localStorage): void {
  storage.setItem(KEY, JSON.stringify(stripSecrets(s)));
}

/** 读 localStorage 存档并顺带清洗旧密钥存量（useSettings 初始化用；导出供回归测试）。 */
export function loadPersistedSettings(storage: Storage = window.localStorage): Settings {
  try {
    const migrated = migrate(JSON.parse(storage.getItem(KEY) || "{}"));
    // 一次性清洗：旧版本写进 localStorage 的完整 settings（含密钥）读入后立刻
    // 用剥离版覆写，之后所有写入路径都经 persist，密钥不再回潮。
    const cleaned = stripSecrets(migrated);
    storage.setItem(KEY, JSON.stringify(cleaned));
    return cleaned;
  } catch {
    return DEFAULT;
  }
}

// 取当前选中的对话模型（无选中则取第一个，再无则空配置）
export function activeChatModel(s: Settings): ChatModel {
  const model = (
    s.chatModels.find((m) => m.id === s.activeChatModelId) ||
    s.chatModels[0] ||
    { apiKey: "", baseUrl: "", modelName: "", proxyMode: "on" }
  );
  // 本地/Ollama 卡兜底到本机 Ollama：旧卡可能只存了模型名，没存地址。
  const isLocalServer = model.source === "ollama" || model.source === "local";
  const baseUrl = isLocalServer ? (model.baseUrl || OLLAMA_DEFAULT_BASE_URL) : model.baseUrl;
  return {
    ...model,
    baseUrl,
    apiKey: model.apiKey || (isLocalServer ? "ollama" : ""),
    // 回环端点必须直连（与视觉/嵌入同一口径）：全局代理不应接管 127.0.0.1 的 Ollama。
    proxyUrl: resolveEndpointProxy(baseUrl, model.proxyMode, globalProxyAddress(s), s.proxyEnabled),
  };
}

export function resolvedEmbedModel(s: Settings): EmbedModel {
  return {
    ...s.embedModel,
    proxyUrl: resolveEndpointProxy(
      s.embedModel.baseUrl, s.embedModel.proxyMode, globalProxyAddress(s), s.proxyEnabled,
    ),
  };
}

/** 视觉大模型（VLM）→ 图像描述/反推用的 Chat 形态（2026-10-18）。
 *  与 useChatSession 的 Visual CI 取值同源：local(gguf) 走 ollamaName，remote 走 baseUrl+modelName。
 *  ⚠ 图像描述/反推必须用视觉模型，不能用普通对话模型（云对话模型普遍拒识图，
 *    用户实测「素材生成视觉描述后仍空」的根绪就在此）。未配置返回 null。 */
export function activeVlmChat(
  s: Settings,
): { baseUrl: string; apiKey: string; modelName: string; proxyUrl?: string } | null {
  const vlm = s.vlmModel;
  if (!vlm) return null;
  if (vlm.mode === "local") {
    if (!vlm.ollamaName) return null;
    return { baseUrl: "http://localhost:11434/v1", apiKey: "ollama", modelName: vlm.ollamaName, proxyUrl: "" };
  }
  if (!vlm.baseUrl || !vlm.modelName) return null;
  return {
    baseUrl: vlm.baseUrl,
    apiKey: vlm.apiKey || "",
    modelName: vlm.modelName,
    // 回环端点（本地 Ollama 127.0.0.1）必须直连、不走全局代理，否则 describe 502
    //（resolveEndpointProxy 对 loopback 返空；resolveModelProxy 不豁免回环，不能用）。
    proxyUrl: resolveEndpointProxy(vlm.baseUrl, vlm.proxyMode, globalProxyAddress(s), s.proxyEnabled),
  };
}

/** activeVlmChat 返 null 时，给出「为什么不能用」的精准提示（2026-10-18：用户配了本地 GGUF
 *  却报「未配置视觉大模型」，根因是本地模式没走完「导入到 Ollama＋保存」）。 */
export function vlmGapMessage(s: Settings): string {
  const v = s.vlmModel;
  if (!v) return "未配置视觉大模型：请到「设置 → 模型」配置「视觉大模型（VLM）」。";
  if (v.mode === "local") {
    if (!v.ggufPath) return "本地视觉模型还没选 GGUF 文件：请到「设置 → 模型 → 视觉大模型」填写本地模型路径。";
    if (!v.ollamaName) {
      return "本地视觉模型（GGUF）还没导入 Ollama：请在「设置 → 模型 → 视觉大模型」点「导入到 Ollama」，"
        + "或直接切到「API / Ollama」模式、模型名填 Ollama 里已有的名字后保存。";
    }
    return "本地视觉模型已导入但不可用：请确认 Ollama 已启动、且导入的模型名正确。";
  }
  if (!v.baseUrl || !v.modelName) {
    return "未配置视觉大模型：请到「设置 → 模型 → 视觉大模型（VLM）」填写接口地址与模型名。";
  }
  return "视觉大模型不可用：请检查接口地址 / 模型名是否正确。";
}

export function modelDisplayName(model: { displayName?: string; modelName: string }): string {
  return model.displayName?.trim() || model.modelName.trim() || "未命名模型";
}

// 取当前选中的用户人设（无选中或已删则返回空档，即不注入用户人设）。
export function activeUserPersona(s: Settings): UserPersona {
  const empty = { id: "", name: "", content: "" };
  return (s.userPersonas || []).find((p) => p.id === s.activeUserPersonaId) || empty;
}

export function resolveTheme(theme: Theme, prefersDark: boolean): ManualTheme {
  if (theme !== "system") return theme;
  return prefersDark ? "night" : "bright";
}

// 液态玻璃主题把聊天背景升格为全应用壁纸；默认 15% 透明度会失去壁纸存在感，
// 故加一个可见度下限（仍随原滑杆单调变化）。
export function chatBgRenderOpacity(theme: Theme, opacity: number): number {
  if (theme !== "liquid-glass") return opacity;
  return Math.min(1, 0.55 + opacity * 0.45);
}

export function applyTheme(theme: Theme) {
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = resolveTheme(theme, prefersDark);
}

// 把 /proxy-status 的探测结果折叠进 settings（2026-09-10「继承全局」升级）：
// - proxyEnabled ← 是否探到可用代理（自动写回，不是用户开关）
// - systemProxyUrl ← 生效的系统/环境变量代理地址（仅当它真的可用时记录，否则留空，
//   由 effectiveGlobalProxyUrl 回退到手填 proxyUrl）
// 三个字段都没变化时返回原对象，避免无谓重渲染与回写。
export function withProxyStatus(p: Settings, st: ProxyStatus): Settings {
  const systemUrl = (st.source === "system" || st.source === "env") ? (st.address || "") : "";
  const nextSource = (st.source || "none") as Settings["systemProxySource"];
  if (p.proxyEnabled === st.listening
    && p.systemProxyUrl === systemUrl
    && p.systemProxySource === nextSource) {
    return p;
  }
  return { ...p, proxyEnabled: st.listening, systemProxyUrl: systemUrl, systemProxySource: nextSource };
}

export function useSettings() {
  const [settings, setSettings] = useState<Settings>(loadPersistedSettings);
  const [hydrated, setHydrated] = useState(false); // 后端为准：回填完成前不回写后端

  // 启动时拉后端存档，有则以后端为准覆盖本地（跨浏览器/换机恢复）。
  // 联机同库开着却连不上电脑时：保持「未水合」——界面跑本机缓存，但绝不把这份快照
  // 回写电脑（后端 set_state 整体替换 repos/settings）。电脑回来后由 MobileOnlineWatch
  // 广播 USER_STATE_RECONNECT_EVENT，这里重新水合一次。
  // 联机时模型设置归手机：电脑快照只提供非模型设置，再用手机本机模型覆盖其上
  //（手机没配过的字段继续跟电脑，见 lib/mobileModelSettings）。
  useEffect(() => {
    let alive = true;
    const hydrate = async () => {
      const remote = await remoteActiveOnce();
      if (!alive) return;
      try {
        const s = await getUserState();
        if (!alive) return;
        let hydrated: Settings | null = null;
        if (s.settings) {
          let migrated = migrate(s.settings as unknown as Record<string, unknown>);
          if (remote) {
            // 本机端点取不到（旧壳/本机后端离线）按「手机还没有自己的模型」处理：沿用电脑那份
            const local = await loadMobileLocalModels().catch(() => null);
            if (!alive) return;
            migrated = overlayModelSettings(migrated, local?.models ?? null);
          }
          hydrated = migrated;
          setSettings(migrated);
          persist(migrated); // localStorage 不落密钥（剥离见 stripSecrets）
        }
        // 分流保存开关与基线：联机才把模型字段另存手机本机（基线＝本次水合呈现的模型）
        markRemoteModelSync(remote, hydrated);
        clearUserStateFallback();
        setHydrated(true);
      } catch {
        if (!alive) return;
        if (remote) { markUserStateFallback(); return; } // 电脑的真值还没拿到，禁止回写
        setHydrated(true); // 电脑端/手机本机模式：后端离线沿用 localStorage（原行为）
      }
      // 启动时快速探测（不做出网验证，避免每次开机等网络超时）；完整验证在设置里手动触发。
      fetchProxyStatus()
        .then((st) => { if (alive) setSettings((p) => withProxyStatus(p, st)); })
        .catch(() => { /* 后端离线保持现值 */ });
    };
    void hydrate();
    const onReconnect = () => { void hydrate(); };
    window.addEventListener(USER_STATE_RECONNECT_EVENT, onReconnect);
    return () => {
      alive = false;
      window.removeEventListener(USER_STATE_RECONNECT_EVENT, onReconnect);
    };
  }, []);

  useEffect(() => {
    persist(settings); // localStorage 不落密钥（剥离见 stripSecrets）；密钥真源在后端 user_state.json
    applyTheme(settings.theme);
    if (hydrated) pushSettings(settings); // 回填完成后，本地变更（及升级时的本地存量）镜像到后端
  }, [settings, hydrated]);

  // 跟随系统时，监听系统主题变化
  useEffect(() => {
    if (settings.theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => applyTheme("system");
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [settings.theme]);

  const update = (patch: Partial<Settings>) => setSettings((p) => ({ ...p, ...patch }));
  const updateLiquidGlass = (patch: Partial<LiquidGlassSettings>) =>
    setSettings((p) => ({ ...p, liquidGlass: { ...p.liquidGlass, ...patch } }));

  // 继承全局的实时检测（2026-09-10 升级）：后端先读**系统代理**（WinINET/环境变量）、
  // 再回退手填地址，TCP 探活后返回生效值。结果经 withProxyStatus 写回
  // proxyEnabled / systemProxyUrl；opts.search=true 时额外真跑一次搜索（设置页手动触发）。
  const refreshProxyStatus = async (opts: { search?: boolean } = {}) => {
    try {
      const st = await fetchProxyStatus(opts);
      setSettings((p) => withProxyStatus(p, st));
      return st;
    } catch {
      return null; // 后端离线保持现值
    }
  };

  const addImageModel = () =>
    setSettings((p) => ({
      ...p,
      imageModels: [
        ...p.imageModels,
        { id: crypto.randomUUID(), apiKey: "", baseUrl: "", modelName: "新模型", proxyMode: "on" },
      ],
    }));

  const updateImageModel = (id: string, patch: Partial<ImageModel>) =>
    setSettings((p) => ({
      ...p,
      imageModels: p.imageModels.map((m) => (m.id === id ? { ...m, ...patch } : m)),
    }));

  const removeImageModel = (id: string) =>
    setSettings((p) => ({
      ...p,
      imageModels: p.imageModels.filter((m) => m.id !== id),
      activeImageModelId: p.activeImageModelId === id ? undefined : p.activeImageModelId,
    }));

  const addStylePreset = (name: string, content: string): string => {
    const id = crypto.randomUUID();
    setSettings((p) => ({ ...p, stylePresets: [...(p.stylePresets || []), { id, name, content }] }));
    return id;
  };

  const updateStylePreset = (id: string, patch: Partial<StylePreset>) =>
    setSettings((p) => ({
      ...p,
      stylePresets: (p.stylePresets || []).map((s) => (s.id === id ? { ...s, ...patch } : s)),
    }));

  const removeStylePreset = (id: string) =>
    setSettings((p) => ({
      ...p,
      stylePresets: (p.stylePresets || []).filter((s) => s.id !== id),
      imageStyle: p.imageStyle === `preset:${id}` ? "" : p.imageStyle,  // 删掉正选中的存档 → 回退自动
    }));

  return {
    settings, update, updateLiquidGlass, addImageModel, updateImageModel, removeImageModel,
    addStylePreset, updateStylePreset, removeStylePreset, refreshProxyStatus,
  };
}

// 导出设置为 JSON。keepKeys=false 时清空所有密钥/令牌字段（模型 apiKey、下载令牌等），
// 只留结构与非敏感配置，便于分享/迁移不泄露凭证。
// ⚠ 密钥字段清单单一属主＝stripSecrets（localStorage 持久化同用），不得另抄一份。
export function exportSettings(s: Settings, keepKeys: boolean): string {
  if (!keepKeys) {
    return JSON.stringify(stripSecrets(s), null, 2);
  }
  return JSON.stringify(s, null, 2);
}

// 从导出的 JSON 文本回填设置。走 migrate 做校验+旧字段迁移+补默认，非法 JSON 抛错。
export function importSettings(json: string): Settings {
  const parsed = JSON.parse(json);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("不是有效的设置 JSON");
  }
  return migrate(parsed as Record<string, unknown>);
}

// 从 imageStyle 取选中存档的 content（内置风格返回空串）。供生图链路透传给后端。
// ⚠ 2026-10-25 起「提示词模版」接管：新链路用 lib/promptTemplates.promptTemplateInstructions
//   （选中自定义模版的追加段）；本函数仅保留给旧数据/回归对照，不再有新调用点。
export function activeStyleTemplate(s: Settings): string {
  const v = s.imageStyle || "";
  if (!v.startsWith("preset:")) return "";
  const id = v.slice("preset:".length);
  return (s.stylePresets || []).find((p) => p.id === id)?.content || "";
}
