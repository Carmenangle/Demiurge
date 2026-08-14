import { useEffect, useState } from "react";
import { getUserState } from "../api/userState";
import { pushSettings } from "../lib/userStateSync";
import {
  normalizeProxyMode, resolveEndpointProxy, resolveModelProxy, type ProxyMode,
} from "../lib/modelProxy";

export type ManualTheme = "bright" | "night" | "eye-care" | "green" | "gray";
export type Theme = ManualTheme | "system";

const MANUAL_THEMES: readonly ManualTheme[] = [
  "bright",
  "night",
  "eye-care",
  "green",
  "gray",
];

export interface ChatModel {
  id?: string;        // 多模型列表里的唯一 id（单模型旧数据可无）
  displayName?: string;
  apiKey: string;
  baseUrl: string;
  modelName: string;
  providerProfile?: "openai_compatible" | "claude_compatible";
  proxyMode?: ProxyMode;
  proxyUrl?: string; // 运行时解析值；不要求持久化
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

export interface MediaInsertPreset {
  templateId: string;       // 图片工作流模板 id（空=未预设，不异步出图）
  loraMode?: "none" | "single" | "multi"; // 无 LoRA / 高潮角色栈或风格兜底 / 默认风格+高潮角色栈
  appearanceSource?: "worldbook" | "character_card"; // 稳定外貌取世界书角色条目或绑定角色卡
  promptProfile?: import("../lib/imagePromptProfiles").PromptProfileId;
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
}

export interface Settings {
  theme: Theme;
  chatModels: ChatModel[];          // 对话模型（可多个供应商）
  activeChatModelId?: string;       // 当前选中的对话模型 id
  providerProfileSemanticsVersion?: number; // v2 起只保留用户明确选择，禁止按模型名猜 wire
  embedModel: EmbedModel;  // 知识库 RAG 嵌入模型
  vlmModel: VlmModel;      // 视觉大模型（Visual CI 验收用）
  imageModels: ImageModel[];
  activeImageModelId?: string;
  videoModels: VideoModel[];
  activeVideoModelId?: string;
  imageStyle?: string;  // 生图提示词风格：""(自动)/sd/gpt/banana，或 "preset:<id>" 指向自定义存档
  stylePresets?: StylePreset[];  // 用户自定义风格存档
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
  mediaInsert?: Record<string, MediaInsertPreset>; // 多元数据插入预设，按作品(repoId)绑定 ComfyUI 模板+LoRA
  comfyuiPath: string; // ComfyUI 本体目录（含 main.py），用于后端启动
  comfyuiPython: string; // ComfyUI 自己的 Python；禁止使用本工具 Runtime 代替
  comfyuiUrl: string; // ComfyUI 访问地址，iframe 嵌入与 API 调用
  modelsDir: string; // ComfyUI models 目录（模型下载落盘，留空则用 comfyuiPath/models）
  hfToken: string; // HuggingFace token（下载鉴权模型用）
  civitaiToken: string; // Civitai API key（下载鉴权模型用）
  proxyUrl: string; // 联网搜索代理地址（灵感搜索走此代理访问外网）
  proxyEnabled: boolean; // 是否启用代理（关则直连外网）
  smitheryKey?: string; // Smithery MCP 市场 API Key（浏览/连接托管 MCP 服务器用）
  chatBgPath?: string; // 小仓库对话背景图（本地文件路径，走 local-view 读取）
  chatBgOpacity?: number; // 对话背景透明度 0~1（默认 0.15）
  chatBgFit?: "cover" | "contain"; // 填充方式：cover 铺满裁剪 / contain 完整显示
  chatBgScale?: number; // 缩放 0.5~2（默认 1）
  chatBgPosX?: number; // 水平位置 0~100（默认 50 居中）
  chatBgPosY?: number; // 垂直位置 0~100（默认 50 居中）
  activeAgentId?: string; // 当前对话选中的 Agent 预设 id（空=内置默认行为）
  streamOutput: boolean; // 智能体回复是否按模型增量实时输出
  contextReminderTokens: number; // 累计上下文达到该估算 token 数时提醒压缩
  contextMaxTokens: number; // 每轮传给 Agent 的历史上下文估算 token 硬上限
  historyPerRole: number; // 每角色（用户/AI）读取的最近历史条数，再在 token 上限内裁剪
}

export const DEFAULT_CONTEXT_REMINDER_TOKENS = 12_000;
export const DEFAULT_CONTEXT_MAX_TOKENS = 20_000;
export const DEFAULT_HISTORY_PER_ROLE = 6;

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

const DEFAULT: Settings = {
  theme: "system",
  chatModels: [],
  embedModel: { mode: "remote", apiKey: "ollama", baseUrl: "http://localhost:11434/v1", modelName: "qwen3-embedding:latest", proxyMode: "on" },
  vlmModel: { mode: "remote", apiKey: "ollama", baseUrl: "http://localhost:11434/v1", modelName: "gemma4:latest", proxyMode: "on" },
  imageModels: [],
  videoModels: [],
  imageStyle: "",
  stylePresets: [],
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
  mediaInsert: {},
  comfyuiPath: "",
  comfyuiPython: "",
  comfyuiUrl: "http://127.0.0.1:8188",
  modelsDir: "",
  hfToken: "",
  civitaiToken: "",
  proxyUrl: "http://127.0.0.1:7897",
  proxyEnabled: true,
  smitheryKey: "",
  chatBgPath: "",
  chatBgOpacity: 0.15,
  chatBgFit: "cover",
  chatBgScale: 1,
  chatBgPosX: 50,
  chatBgPosY: 50,
  streamOutput: false,
  contextReminderTokens: DEFAULT_CONTEXT_REMINDER_TOKENS,
  contextMaxTokens: DEFAULT_CONTEXT_MAX_TOKENS,
  historyPerRole: DEFAULT_HISTORY_PER_ROLE,
};

// 旧数据迁移：单 chatModel 字段 → chatModels 列表（向后兼容）
function migrate(s: Record<string, unknown>): Settings {
  const merged = { ...DEFAULT, ...s } as Settings & { chatModel?: ChatModel };
  merged.theme = normalizeTheme(s.theme);
  merged.streamOutput = s.streamOutput === true;
  const savedEmbed = (s.embedModel || {}) as Partial<EmbedModel>;
  merged.embedModel = {
    ...DEFAULT.embedModel,
    ...savedEmbed,
    mode: savedEmbed.mode === "local" || savedEmbed.mode === "remote"
      ? savedEmbed.mode
      : savedEmbed.modelDir?.trim() ? "local" : "remote",
    proxyMode: normalizeProxyMode(savedEmbed.proxyMode),
  };
  const savedVlm = (s.vlmModel || {}) as Partial<VlmModel>;
  merged.vlmModel = {
    ...DEFAULT.vlmModel,
    ...savedVlm,
    mode: savedVlm.mode === "local" || savedVlm.mode === "remote"
      ? savedVlm.mode
      : (savedVlm.ggufPath?.trim() || savedVlm.mmprojPath?.trim()) ? "local" : "remote",
    proxyMode: normalizeProxyMode(savedVlm.proxyMode),
  };
  const contextBudgets = normalizeContextBudgets(
    s.contextReminderTokens,
    s.contextMaxTokens,
  );
  merged.contextReminderTokens = contextBudgets.reminder;
  merged.contextMaxTokens = contextBudgets.max;
  const parsedTurns = Number(s.historyPerRole);
  merged.historyPerRole = Number.isFinite(parsedTurns)
    ? Math.min(50, Math.max(1, Math.round(parsedTurns)))
    : DEFAULT_HISTORY_PER_ROLE;
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

function load(): Settings {
  try {
    return migrate(JSON.parse(localStorage.getItem(KEY) || "{}"));
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
  return { ...model, proxyUrl: resolveModelProxy(model.proxyMode, s.proxyUrl, s.proxyEnabled) };
}

export function resolvedEmbedModel(s: Settings): EmbedModel {
  return {
    ...s.embedModel,
    proxyUrl: resolveEndpointProxy(
      s.embedModel.baseUrl, s.embedModel.proxyMode, s.proxyUrl, s.proxyEnabled,
    ),
  };
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

export function applyTheme(theme: Theme) {
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = resolveTheme(theme, prefersDark);
}

export function useSettings() {
  const [settings, setSettings] = useState<Settings>(load);
  const [hydrated, setHydrated] = useState(false); // 后端为准：回填完成前不回写后端

  // 启动时拉后端存档，有则以后端为准覆盖本地（跨浏览器/换机恢复）
  useEffect(() => {
    let alive = true;
    getUserState()
      .then((s) => {
        if (alive && s.settings) {
          const migrated = migrate(s.settings as unknown as Record<string, unknown>);
          setSettings(migrated);
          localStorage.setItem(KEY, JSON.stringify(migrated));
        }
      })
      .catch(() => { /* 后端离线：沿用 localStorage */ })
      .finally(() => { if (alive) setHydrated(true); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    localStorage.setItem(KEY, JSON.stringify(settings));
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
    settings, update, addImageModel, updateImageModel, removeImageModel,
    addStylePreset, updateStylePreset, removeStylePreset,
  };
}

// 导出设置为 JSON。keepKeys=false 时清空所有密钥/令牌字段（模型 apiKey、下载令牌等），
// 只留结构与非敏感配置，便于分享/迁移不泄露凭证。
export function exportSettings(s: Settings, keepKeys: boolean): string {
  const out: Settings = JSON.parse(JSON.stringify(s));
  if (!keepKeys) {
    out.chatModels = out.chatModels.map((m) => ({ ...m, apiKey: "" }));
    out.imageModels = out.imageModels.map((m) => ({ ...m, apiKey: "" }));
    out.videoModels = out.videoModels.map((m) => ({ ...m, apiKey: "" }));
    out.embedModel = { ...out.embedModel, apiKey: "" };
    out.vlmModel = { ...out.vlmModel, apiKey: "" };
    out.hfToken = "";
    out.civitaiToken = "";
    out.smitheryKey = "";
  }
  return JSON.stringify(out, null, 2);
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
export function activeStyleTemplate(s: Settings): string {
  const v = s.imageStyle || "";
  if (!v.startsWith("preset:")) return "";
  const id = v.slice("preset:".length);
  return (s.stylePresets || []).find((p) => p.id === id)?.content || "";
}
