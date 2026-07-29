import { useEffect, useState } from "react";
import { getUserState } from "../api/userState";
import { pushSettings } from "../lib/userStateSync";

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
}

// 嵌入模型（知识库 RAG 用）：OpenAI 兼容形式，可填智谱/OpenAI/Ollama 等
export type EmbeddingMode = "remote" | "local";

export interface EmbedModel {
  mode: EmbeddingMode;
  apiKey: string;
  baseUrl: string;
  modelName: string;
  /** 可选：本地嵌入模型目录；远程/Ollama 模式可留空。 */
  modelDir?: string;
  /** 可选：本地 Cross-Encoder Reranker 模型目录。 */
  rerankerDir?: string;
}

export interface ImageModel {
  id: string;
  displayName?: string;
  apiKey: string;
  baseUrl: string;
  modelName: string;
  supportsCustomSize?: boolean;
}

export interface VideoModel {
  id: string;
  displayName?: string;
  apiKey: string;
  baseUrl: string;
  modelName: string;
}

// 用户自定义的提示词风格存档：content 是整段风格模板（画风/结构/负面词，自由粘贴），
// AI 参照其组织形态来写提示词。切换器选中时 imageStyle = "preset:<id>"。
export interface StylePreset {
  id: string;
  name: string;
  content: string;
}

export interface Settings {
  theme: Theme;
  chatModels: ChatModel[];          // 对话模型（可多个供应商）
  activeChatModelId?: string;       // 当前选中的对话模型 id
  embedModel: EmbedModel;  // 知识库 RAG 嵌入模型
  imageModels: ImageModel[];
  activeImageModelId?: string;
  videoModels: VideoModel[];
  activeVideoModelId?: string;
  imageStyle?: string;  // 生图提示词风格：""(自动)/sd/gpt/banana，或 "preset:<id>" 指向自定义存档
  stylePresets?: StylePreset[];  // 用户自定义风格存档
  workflowDir: string; // 工作流默认读取路径（后端扫描该目录及子目录的 .json）
  outputDir: string; // 输出图片默认存放路径
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
  contextReminderTokens: number; // 累计上下文达到该估算 token 数时提醒压缩
  contextMaxTokens: number; // 每轮传给 Agent 的历史上下文估算 token 硬上限
}

export const DEFAULT_CONTEXT_REMINDER_TOKENS = 12_000;
export const DEFAULT_CONTEXT_MAX_TOKENS = 20_000;

export function normalizeContextBudgets(reminder: unknown, max: unknown) {
  const parsedMax = Number(max);
  const safeMax = Number.isFinite(parsedMax)
    ? Math.min(200_000, Math.max(4_000, Math.round(parsedMax)))
    : DEFAULT_CONTEXT_MAX_TOKENS;
  const parsedReminder = Number(reminder);
  const safeReminder = Number.isFinite(parsedReminder)
    ? Math.min(safeMax - 1_000, Math.max(1_000, Math.round(parsedReminder)))
    : Math.min(DEFAULT_CONTEXT_REMINDER_TOKENS, safeMax - 1_000);
  return { reminder: safeReminder, max: safeMax };
}

const KEY = "laf_settings";

const DEFAULT: Settings = {
  theme: "system",
  chatModels: [],
  embedModel: { mode: "remote", apiKey: "ollama", baseUrl: "http://localhost:11434/v1", modelName: "qwen3-embedding:latest" },
  imageModels: [],
  videoModels: [],
  imageStyle: "",
  stylePresets: [],
  workflowDir: "",
  outputDir: "",
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
  contextReminderTokens: DEFAULT_CONTEXT_REMINDER_TOKENS,
  contextMaxTokens: DEFAULT_CONTEXT_MAX_TOKENS,
};

// 旧数据迁移：单 chatModel 字段 → chatModels 列表（向后兼容）
function migrate(s: Record<string, unknown>): Settings {
  const merged = { ...DEFAULT, ...s } as Settings & { chatModel?: ChatModel };
  merged.theme = normalizeTheme(s.theme);
  const savedEmbed = (s.embedModel || {}) as Partial<EmbedModel>;
  merged.embedModel = {
    ...DEFAULT.embedModel,
    ...savedEmbed,
    mode: savedEmbed.mode === "local" || savedEmbed.mode === "remote"
      ? savedEmbed.mode
      : savedEmbed.modelDir?.trim() ? "local" : "remote",
  };
  const contextBudgets = normalizeContextBudgets(
    s.contextReminderTokens,
    s.contextMaxTokens,
  );
  merged.contextReminderTokens = contextBudgets.reminder;
  merged.contextMaxTokens = contextBudgets.max;
  if ((!merged.chatModels || merged.chatModels.length === 0) && merged.chatModel) {
    const old = merged.chatModel;
    if (old.baseUrl || old.modelName || old.apiKey) {
      const id = crypto.randomUUID();
      merged.chatModels = [{ ...old, id }];
      merged.activeChatModelId = id;
    }
  }
  // 给缺 id 的对话模型补 id
  merged.chatModels = (merged.chatModels || []).map((m) =>
    m.id ? m : { ...m, id: crypto.randomUUID() },
  );
  delete merged.chatModel;
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
  return (
    s.chatModels.find((m) => m.id === s.activeChatModelId) ||
    s.chatModels[0] ||
    { apiKey: "", baseUrl: "", modelName: "" }
  );
}

export function modelDisplayName(model: { displayName?: string; modelName: string }): string {
  return model.displayName?.trim() || model.modelName.trim() || "未命名模型";
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
        { id: crypto.randomUUID(), apiKey: "", baseUrl: "", modelName: "新模型" },
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

// 从 imageStyle 取选中存档的 content（内置风格返回空串）。供生图链路透传给后端。
export function activeStyleTemplate(s: Settings): string {
  const v = s.imageStyle || "";
  if (!v.startsWith("preset:")) return "";
  const id = v.slice("preset:".length);
  return (s.stylePresets || []).find((p) => p.id === id)?.content || "";
}
