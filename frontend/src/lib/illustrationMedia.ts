import type { MediaInsertPreset } from "../stores/settings";
import { illustrationTemplateValues } from "./imagePromptProfiles";
import type { SemanticField } from "./imagePromptProfiles";

export interface IllustrationLora {
  name: string;
  weight: number;
  character: boolean;
}

export function resolveIllustrationActors(
  eventActors: readonly string[],
  subjects: readonly { name?: string; description?: string; weight?: number }[] | undefined,
  knownNames: readonly string[],
): string[] {
  const known = new Set(knownNames.map((name) => name.trim()).filter(Boolean));
  return [...new Set([
    ...eventActors,
    ...(subjects || []).map((subject) => subject.name || ""),
  ].map((name) => name.trim()).filter((name) => name && known.has(name)))];
}

export function illustrationLoraConfigurationError(
  preset: MediaInsertPreset,
  media: { loras: IllustrationLora[]; characterLora: boolean },
): string {
  if (preset.appearanceSource === "character_card" || preset.loraMode === "none") return "";
  const mode = preset.loraMode || "single";
  const styleName = preset.styleLora || preset.loraName || "";
  if (mode === "multi" && !styleName) return "多 LoRA 模式尚未配置默认风格 LoRA";
  if (mode === "single" && !media.characterLora && media.loras.length === 0) {
    return "当前场景未命中角色 LoRA，且尚未配置兜底风格 LoRA";
  }
  return "";
}

export function illustrationRequestMedia(preset: MediaInsertPreset | undefined, cardNames: string[]) {
  const useCharacterCards = preset?.appearanceSource === "character_card";
  const characterLoras = useCharacterCards ? {} : preset?.characterLoras || {};
  return {
    characterBaseImages: Object.fromEntries(
      Object.entries(characterLoras)
        .filter(([, binding]) => binding.baseImage)
        .map(([name, binding]) => [name, binding.baseImage as string]),
    ),
    illustrationActorNames: [...new Set(
      useCharacterCards ? cardNames : Object.keys(characterLoras),
    )],
    styleBaseImage: useCharacterCards ? "" : preset?.styleBaseImage || "",
  };
}

export function illustrationWorkflowMedia(
  preset: MediaInsertPreset, actors: string[], cardNames: string[],
) {
  const useCharacterCards = preset.appearanceSource === "character_card";
  const characterLoras = useCharacterCards ? {} : preset.characterLoras || {};
  const mode = preset.loraMode || "single";
  const candidates = actors.length ? [...new Set(actors)] : cardNames.slice(0, 1);
  let baseImage = "";
  for (const name of candidates) {
    const binding = characterLoras[name];
    if (!binding) continue;
    if (binding.baseImage) { baseImage = binding.baseImage; break; }
  }
  const styleName = preset.styleLora || preset.loraName || "";
  const styleWeight = preset.styleLoraWeight ?? preset.loraWeight ?? 0.8;
  const loras: IllustrationLora[] = [];
  const addLora = (item: IllustrationLora) => {
    if (item.name && !loras.some((existing) => existing.name === item.name)) loras.push(item);
  };
  if (mode !== "none") {
    if (mode === "multi" && styleName) {
      addLora({ name: styleName, weight: styleWeight, character: false });
    }
    for (const name of candidates) {
      const binding = characterLoras[name];
      if (!binding?.loraName) continue;
      addLora({
        name: binding.loraName,
        weight: binding.loraWeight ?? 0.8,
        character: true,
      });
    }
    if (mode === "single" && loras.length === 0 && styleName) {
      addLora({ name: styleName, weight: styleWeight, character: false });
    }
  }
  if (!useCharacterCards && !baseImage) baseImage = preset.styleBaseImage || "";
  const primary = loras[0];
  return {
    loras,
    loraName: primary?.name || "",
    loraWeight: primary?.weight ?? 0.8,
    baseImage,
    characterLora: loras.some((lora) => lora.character),
  };
}

export type VideoMode = "climax" | "firstlast";

/** V1.5/B1 视频模式决策：事件 videoMode 优先，其次 preset.firstlast（首尾帧生成选项，
 * 用户定稿 2026-08-28：视频模式由图片生成选项推导），再退 preset.videoMode（旧预设兼容），缺省 climax。 */
export function resolveVideoMode(
  preset: { videoMode?: VideoMode; firstlast?: boolean } | undefined,
  eventVideoMode?: string,
): VideoMode {
  if (eventVideoMode === "climax" || eventVideoMode === "firstlast") return eventVideoMode;
  if (preset?.firstlast) return "firstlast";
  if (preset?.videoMode === "climax" || preset?.videoMode === "firstlast") {
    return preset.videoMode;
  }
  return "climax";
}

/** V1.5/B3（R4）视频模板触发闸门：firstlast 是楼层触发不看 motion；climax 维持 smartVideo+motion。
 * 返回是否用视频模板（模板已配置为前提）。 */
export function resolveVideoTemplateChoice(
  preset: { videoTemplateId?: string; smartVideo?: boolean } | undefined,
  videoMode: VideoMode,
  motion: number,
): boolean {
  if (videoMode === "firstlast") {
    return !!preset?.videoTemplateId;
  }
  return !!(preset?.smartVideo && preset.videoTemplateId && motion >= 2);
}

// ===== V1.6/P5 首尾帧顺序链：先出首尾帧图（图片模板生图），双图 ready 再提视频 =====
// 决策 A（2026-08-26 用户拍板）：首尾帧生图复用现有图片模板（preset.templateId），
// 提示词用事件 firstFrameDesc/lastFrameDesc；不新增首尾帧专属模板字段。

export type FirstlastFrameTask =
  | { frame: "first"; kind: "reuse"; imageUrl: string }      // 首帧=上尾帧图（零生图，W2 已复用为底图）
  | { frame: "first"; kind: "generate"; desc: string }       // 首帧需生成（firstFrameDesc）
  | { frame: "last"; kind: "existing"; imageUrl: string }    // 尾帧=现成图（事件 lastFrameUrl）
  | { frame: "last"; kind: "generate"; desc: string };       // 尾帧需生成（lastFrameDesc）

export interface FirstlastFramePlan {
  tasks: FirstlastFrameTask[];
  /** 视频可否成片：首帧必须可用（reuse 或可生成）；尾帧可缺（降级首帧单图，R2）。 */
  canGenerateVideo: boolean;
}

/** P5 首尾帧图来源计划：reuse 免首帧生图；regenerate 首帧用 firstFrameDesc 生成；尾帧现成/生成。 */
export function planFirstlastFrameTasks(opts: {
  transition?: string;
  prevTailUrl?: string;      // resolvePrevTailDesc().lastFrameUrl（上尾帧图）
  firstFrameDesc?: string;
  lastFrameDesc?: string;
  lastFrameUrl?: string;     // 事件 lastFrameUrl（当前楼层尾帧图，有值直接复用）
}): FirstlastFramePlan {
  const tasks: FirstlastFrameTask[] = [];
  let hasFirst = false;
  if (opts.transition === "reuse" && (opts.prevTailUrl || "").trim()) {
    tasks.push({ frame: "first", kind: "reuse", imageUrl: opts.prevTailUrl!.trim() });
    hasFirst = true;
  } else if ((opts.firstFrameDesc || "").trim()) {
    tasks.push({ frame: "first", kind: "generate", desc: opts.firstFrameDesc!.trim() });
    hasFirst = true;
  }
  if ((opts.lastFrameUrl || "").trim()) {
    tasks.push({ frame: "last", kind: "existing", imageUrl: opts.lastFrameUrl!.trim() });
  } else if ((opts.lastFrameDesc || "").trim()) {
    tasks.push({ frame: "last", kind: "generate", desc: opts.lastFrameDesc!.trim() });
  }
  return { tasks, canGenerateVideo: hasFirst };
}

/** V1.6/P5+ 独立图片模式（2026-08-28 用户拍板：首尾帧是独立图片模式，视频模式跟随该选项推导）的槽位布局。 */
export type FirstlastMainSource = "first_prompt" | "last_prompt" | "last_frame_url" | "prev_tail_url";

export interface FirstlastSlotLayout {
  /** 楼层主槽显示的「本楼层新画面」来源：新生成首帧 / 新生成尾帧 / 事件尾帧现成图 / 上尾帧图（画面延续）。 */
  main: FirstlastMainSource;
  /** 尾帧是否需要 ":last" 副槽（主槽给了首帧且尾帧是新生成图时）。 */
  lastSlot: boolean;
}

/**
 * 首尾帧槽位布局（纯函数）：楼层主槽显示本楼层新画面——
 * regenerate 时新画面起点=首帧（新生成），尾帧新图进 ":last" 副槽；
 * reuse 时首帧复用上楼层尾帧图，本楼层唯一新画面=尾帧（新生成→主槽；事件现成图→直接显示；
 * 连尾帧都没有→画面延续，主槽显示上尾帧图）。视频开启时调用方改用 ":first"/":last" 副槽（主槽留给视频正片，避免双 pollResult 同槽竞态）。
 */
export function firstlastSlotLayout(
  plan: FirstlastFramePlan,
): FirstlastSlotLayout | null {
  const first = plan.tasks.find((t) => t.frame === "first");
  const last = plan.tasks.find((t) => t.frame === "last");
  const lastIsGenerate = last?.kind === "generate";
  if (first?.kind === "generate") {
    return { main: "first_prompt", lastSlot: lastIsGenerate };
  }
  if (first?.kind === "reuse") {
    if (lastIsGenerate) return { main: "last_prompt", lastSlot: false };
    if (last?.kind === "existing") return { main: "last_frame_url", lastSlot: false };
    return { main: "prev_tail_url", lastSlot: false };
  }
  // 无首帧（视频模式不可成片；独立图片模式放宽：只有尾帧也能出）
  if (lastIsGenerate) return { main: "last_prompt", lastSlot: false };
  if (last?.kind === "existing") return { main: "last_frame_url", lastSlot: false };
  return null;
}

/** P5 首尾帧生图的图片模板 values：prompt=帧画面描述（决策 A），复用插画模板的 LoRA/底图/负面/latent。 */
export function firstlastFrameValues(
  exposed: readonly SemanticField[],
  desc: string,
  media: {
    negativePrompt?: string;
    loraName?: string;
    loraWeight?: number;
    baseImage?: string;
  },
  latentSize: { width: number; height: number },
): Record<string, unknown> {
  return illustrationTemplateValues(exposed, {
    prompt: desc,
    negativePrompt: media.negativePrompt?.trim() || undefined,
    loraName: media.loraName?.trim() || undefined,
    loraWeight: media.loraWeight,
    baseImage: media.baseImage?.trim() || undefined,
    latentSize,
  });
}

/** W3 转场视频 values：图片1=上尾帧图（转场起点）、图片2=当前首帧图（终点）。
 *  复用正片视频模板（不猜模板有 transition 分支），videoMode 仍走 firstlast；
 *  时长走 preset.transitionDurationHint（坑G：有值才写，缺省交视频模型默认，绝不兑底正片时长）。 */
export function transitionVideoValues(
  exposed: readonly SemanticField[],
  prompt: string,
  media: {
    negativePrompt?: string;
    loraName?: string;
    loraWeight?: number;
  },
  latentSize: { width: number; height: number },
  opts: {
    transitionDurationHint?: number;
    camera?: "static" | "pan" | "zoom";
    prevTailDesc?: string;
    firstFrameDesc?: string;
    prevTailImage?: string;   // 图片1=上尾帧图（已上传 ComfyUI input）
    firstFrameImage?: string; // 图片2=当前首帧图（P5 生成的 input 文件名）
  },
): Record<string, unknown> {
  return illustrationTemplateValues(exposed, {
    prompt,
    negativePrompt: media.negativePrompt?.trim() || undefined,
    loraName: media.loraName?.trim() || undefined,
    loraWeight: media.loraWeight,
    latentSize,
    videoMode: "firstlast",
    videoDuration: opts.transitionDurationHint || undefined, // 坑G：有值才写，缺省交模型默认
    videoCamera: opts.camera,
    firstFrameDesc: opts.prevTailDesc?.trim() || undefined,   // 转场起点描述
    lastFrameDesc: opts.firstFrameDesc?.trim() || undefined,  // 转场终点描述
    prevTailDesc: opts.prevTailDesc?.trim() || undefined,
    firstFrameImage: opts.prevTailImage?.trim() || undefined,
    lastFrameImage: opts.firstFrameImage?.trim() || undefined,
  });
}
