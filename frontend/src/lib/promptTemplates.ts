// 提示词模版纯逻辑（2026-10-25）：分组/选择器选项/表单模型/哈希比对/旧存档迁移。
// 与后端 prompt_template_store 的约束镜像（保存前挡一层，最终校验以后端为准）。
import {
  IMAGE_KINDS, KIND_LABELS, VIDEO_KINDS,
  type PromptTemplate, type PromptTemplateBody, type PromptTemplateKind,
} from "../api/ai/promptTemplates";

export type TemplateGroup = "image" | "video";

export function templateKindGroup(kind: PromptTemplateKind): TemplateGroup {
  return (IMAGE_KINDS as readonly string[]).includes(kind) ? "image" : "video";
}

export function isCustomTemplateId(value: unknown): value is `custom:${string}` {
  return typeof value === "string" && /^custom:[a-f0-9]{32}$/.test(value);
}

/** 选择器/存储里的 custom:<hex> ⇒ 纯 hex（GET 详情用）；非 custom 形态返回空串。 */
export function customIdOf(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("custom:")) return "";
  return value.slice("custom:".length);
}

export function templateById(list: readonly PromptTemplate[], id: string): PromptTemplate | undefined {
  return list.find((t) => t.id === id);
}

/** 内置 + 自定义的选择器选项（自定义带「★」前缀，UI 再分组渲染）。 */
export function templateSelectorOptions(
  builtins: readonly PromptTemplate[], customs: readonly PromptTemplate[], group: TemplateGroup,
): { id: string; name: string; builtin: boolean }[] {
  const inGroup = (t: PromptTemplate) => templateKindGroup(t.kind) === group;
  return [
    ...builtins.filter(inGroup).map((t) => ({ id: t.id, name: t.name, builtin: true })),
    ...customs.filter(inGroup).map((t) => ({
      id: `custom:${t.id}`, name: `★ ${t.name || "未命名模版"}`, builtin: false,
    })),
  ];
}

/** 自定义模版的克隆基准是否已落后于当前内置（「有更新」角标 + 一键重置）。 */
export function templateIsStale(
  template: PromptTemplate, builtins: readonly PromptTemplate[],
): boolean {
  if (template.builtin) return false;
  const base = builtins.find((b) => b.id === template.base_id);
  if (!base) return false;                       // 基准内置不存在（理论不可达）⇒ 不提示
  return !!template.base_hash && template.base_hash !== base.content_hash;
}

// ── 表单模型（工作台编辑器；保存时整份上交，缺字段按空串） ─────────────────────

export interface TemplateForm {
  kind: PromptTemplateKind;
  name: string;
  system_text: string;
  extra_instructions: string;
  quality_tags: string;
  negative_tags: string;
  redline_lock: boolean;
  section_titles: Record<string, string>;
  section_order: string[];
  camera_band_overrides: Record<string, string>;
  beat_labels: string[];
  default_duration: number;
  negative_extra: string;
  meta_note: string;
}

export function formFromTemplate(t: PromptTemplate): TemplateForm {
  return {
    kind: t.kind,
    name: t.name,
    system_text: t.system_text || "",
    extra_instructions: t.extra_instructions || "",
    quality_tags: t.quality_tags || "",
    negative_tags: t.negative_tags || "",
    redline_lock: t.redline_lock !== false,
    section_titles: { ...(t.section_titles || {}) },
    section_order: [...(t.section_order || [])],
    camera_band_overrides: { ...(t.camera_band_overrides || {}) },
    beat_labels: [...(t.beat_labels || [])],
    default_duration: t.default_duration || 0,
    negative_extra: t.negative_extra || "",
    meta_note: t.meta_note || "",
  };
}

/** 空表单（新建）；seed 为克隆来源（内置全文或另一自定义模版）。 */
export function emptyForm(kind: PromptTemplateKind, seed?: PromptTemplate): TemplateForm {
  const base: TemplateForm = seed && seed.kind === kind ? formFromTemplate(seed) : {
    kind, name: "", system_text: "", extra_instructions: "", quality_tags: "",
    negative_tags: "", redline_lock: true, section_titles: {}, section_order: [],
    camera_band_overrides: {}, beat_labels: [], default_duration: 0,
    negative_extra: "", meta_note: "",
  };
  return { ...base, kind, name: "" };
}

export function formToBody(form: TemplateForm, baseId = ""): PromptTemplateBody {
  return {
    kind: form.kind,
    name: form.name.trim() || "未命名模版",
    ...(templateKindGroup(form.kind) === "image" ? {
      system_text: form.system_text,
      extra_instructions: form.extra_instructions,
      quality_tags: form.quality_tags,
      negative_tags: form.negative_tags,
      redline_lock: form.redline_lock,
    } : {
      section_titles: form.section_titles,
      section_order: form.section_order,
      camera_band_overrides: form.camera_band_overrides,
      beat_labels: form.beat_labels,
      default_duration: form.default_duration,
      negative_extra: form.negative_extra,
      meta_note: form.meta_note,
      redline_lock: form.redline_lock,
    }),
    ...(baseId ? { base_id: baseId } : {}),
  };
}

/** system_text + extra_instructions 合计字符数（后端上限 12000，前端实时计数同口径）。 */
export function combinedSystemChars(form: TemplateForm): number {
  return (form.system_text || "").length + (form.extra_instructions || "").length;
}

export const SYSTEM_CHAR_LIMIT = 12_000;

// ── 视频模版按模式过滤（UI 只在对应模式出现时展示） ───────────────────────────

/** 高潮点模式 ⇒ video_climax；首尾帧模式 ⇒ video_firstlast + video_transition（段桥）。 */
export function videoTemplatesForMode(
  builtins: readonly PromptTemplate[], customs: readonly PromptTemplate[],
  mode: "climax" | "firstlast",
): { id: string; name: string; builtin: boolean; kind: PromptTemplateKind }[] {
  const kinds: PromptTemplateKind[] = mode === "firstlast"
    ? ["video_firstlast", "video_transition"] : ["video_climax"];
  const pick = (list: readonly PromptTemplate[], builtin: boolean) =>
    list.filter((t) => kinds.includes(t.kind)).map((t) => ({
      id: builtin ? t.id : `custom:${t.id}`,
      name: builtin ? `${KIND_LABELS[t.kind]}（默认）` : `★ ${t.name || "未命名模版"}`,
      builtin, kind: t.kind,
    }));
  return [...pick(builtins, true), ...pick(customs, false)];
}

// ── 旧「生图风格存档」迁移（一次性；内容原样搬进自定义模版的追加段） ────────────

export interface LegacyStylePreset {
  id: string;
  name: string;
  content: string;
}

/** 旧存档 ⇒ 新建模版请求体（kind=anima_tags，system 用内置全文，内容进追加段）。 */
export function legacyPresetToBody(
  preset: LegacyStylePreset, builtinSystemText: string,
): PromptTemplateBody {
  return {
    kind: "anima_tags",
    name: preset.name || "旧风格存档",
    system_text: builtinSystemText,
    extra_instructions: preset.content || "",
  };
}

/** 选中的提示词模版 ⇒ 透传给后端的 styleTemplate（自定义=追加段；内置=空）。 */
export function promptTemplateInstructions(
  selectedId: string, customs: readonly PromptTemplate[],
): string {
  if (!isCustomTemplateId(selectedId)) return "";
  const t = customs.find((item) => `custom:${item.id}` === selectedId);
  return t?.extra_instructions || "";
}

export { IMAGE_KINDS, VIDEO_KINDS, KIND_LABELS };
