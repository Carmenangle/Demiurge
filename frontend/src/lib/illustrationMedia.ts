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
  // 2026-10-20 实锤（用户：「总不能因为我只绑定了兜底的风格 LoRA 这个功能就失效了吧」）：
  // 演员池此前**只**取 `characterLoras` 的键 ⇒ 只绑风格 LoRA（兜底）时映射为空
  // ⇒ `illustrationActorNames=[]` ⇒ 后端 `known=[]` ⇒ `actor_count=0`
  // ⇒ 提示词里**没有任何角色条目** ⇒ 生图看不到角色面貌、提示词像乱拼（真机实锤
  // `illustration.request actor_count=0 / actors=[] / actor_candidates=[]`）。
  // 修：角色 LoRA 映射为空时回退到**本作品绑定的角色卡名**——
  // 「没有角色 LoRA」不等于「没有角色」，外貌描述本就不该依赖 LoRA 绑定。
  const loraNames = Object.keys(characterLoras);
  return {
    characterBaseImages: Object.fromEntries(
      Object.entries(characterLoras)
        .filter(([, binding]) => binding.baseImage)
        .map(([name, binding]) => [name, binding.baseImage as string]),
    ),
    illustrationActorNames: [...new Set(
      useCharacterCards || loraNames.length === 0 ? cardNames : loraNames,
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

/** 第 59 刀（视频连续化）段桥判定：firstlast + 后端只下发转场提示词（无正片提示词）
 * ⇒ 该槽 = 段桥（首帧=上一张画面、尾帧=本锚点画面；主槽按普通图出，桥视频走 transition 块）。
 * 衔接不上（桥提交失败/首帧生图失败）⇒ 退高潮片段延续（纯图/普通图）。 */
export function isSegmentBridgeEvent(opts: {
  useVideo: boolean;
  videoMode?: string;
  videoPrompt?: string;
  transitionVideoPrompt?: string;
}): boolean {
  return !!opts.useVideo && opts.videoMode === "firstlast"
    && !opts.videoPrompt && !!opts.transitionVideoPrompt;
}

/** V1.5/B3（R4）视频模板触发闸门；2026-10-31 改口：climax 不再用 motion 词表判「有没有动作」，
 * 改认**后端是否真的编排出视频提示词**（`video_prompt`）。词表闸会压掉成稿（后端已花一次 LLM
 * 编译出 2757 字视频分镜，前端按 motion=1 静默丢弃 ⇒ 视频槽不存在且无任何界面提示）。
 * firstlast 是楼层触发，不看视频成稿。返回是否用视频模板（模板已配置为前提）。 */
export function resolveVideoTemplateChoice(
  preset: { videoTemplateId?: string; smartVideo?: boolean } | undefined,
  videoMode: VideoMode,
  videoPrompt: string,
): boolean {
  if (videoMode === "firstlast") {
    return !!preset?.videoTemplateId;
  }
  return !!(preset?.smartVideo && preset.videoTemplateId && (videoPrompt || "").trim());
}

/** 视频时长（秒）：用户显式预设最高优先；否则用后端按剧情动作量判定的秒数
 * （2026-10-31：4–8 秒，提取失败 5）；都缺省 → undefined，保留模板默认。 */
export function resolveVideoDuration(
  preset: { videoDurationHint?: number } | undefined,
  params: { duration?: number } | undefined,
): number | undefined {
  const explicit = Number(preset?.videoDurationHint);
  if (Number.isFinite(explicit) && explicit > 0) return explicit;
  const judged = Number(params?.duration);
  return Number.isFinite(judged) && judged > 0 ? judged : undefined;
}

/** 视频首帧/尾帧底图引用归一（2026-10-31 真机实锤）：聊天槽位里的图片 url 已是
 * `/api/comfyui/local-view?path=…` 或 `http(s)://…` 可 fetch 地址，再套一层 localViewUrl
 * 会取回 404 JSON（本地图片不存在），被当图片上传后 ComfyUI 报
 * `LoadImage (#61): cannot identify image file`。只有裸本地路径才需要包装；
 * 文件名必须从 path/filename 查询参数取真实图片名，禁止把 `local-view?path=…` 当文件名。 */
export function resolveBaseImageSource(
  ref: string,
): { needsWrap: boolean; source: string; filename: string } {
  const source = ref.trim();
  const needsWrap = !(
    /^(?:https?:)?\/\//i.test(source)
    || source.startsWith("/api/")
    || source.startsWith("data:")
    || source.startsWith("blob:")
  );
  if (source.startsWith("data:")) return { needsWrap, source, filename: "base.png" };
  let filename = "";
  const queryAt = source.indexOf("?");
  if (queryAt >= 0) {
    for (const pair of source.slice(queryAt + 1).split("&")) {
      const [key, value = ""] = pair.split("=");
      if (key !== "path" && key !== "filename") continue;
      try { filename = decodeURIComponent(value).split(/[\\/]/).pop() || ""; }
      catch { filename = ""; }
      break;
    }
  }
  if (!filename) {
    try { filename = decodeURIComponent(source.split(/[?#]/)[0].split(/[\\/]/).pop() || ""); }
    catch { filename = source.split(/[\\/]/).pop() || ""; }
  }
  return { needsWrap, source, filename: filename || "base.png" };
}

/** 读取图片真实像素尺寸（2026-10-31 用户定案「图片的宽高要和视频的宽高一致」）。
 *  用**已到手的那份字节**（底图上传前先 fetch 过，不再二次请求）量出首帧图的实际宽高，
 *  写进视频节点的 width/height，覆盖模板里分辨率选择器的连线 ⇒ 成片与首帧逐像素对齐。
 *  环境没有 createImageBitmap（jsdom/老浏览器）或解码失败 → undefined ⇒ 视频退回模板
 *  自带尺寸，绝不因为量不到尺寸而把整条链判失败。 */
export function imagePixelSizeFromBlob(
  blob: Blob,
): Promise<{ width: number; height: number } | undefined> {
  const createBitmap = (globalThis as { createImageBitmap?: unknown }).createImageBitmap;
  if (typeof createBitmap !== "function") return Promise.resolve(undefined);
  return (createBitmap as (source: Blob) => Promise<{ width: number; height: number }>)
    .call(globalThis, blob)
    .then((bitmap) => {
      const width = bitmap.width;
      const height = bitmap.height;
      if (width > 0 && height > 0) return { width, height };
      return undefined;
    })
    .catch(() => undefined);
}

/** 视频节点尺寸对齐（2026-10-31 实测 object_info：MiniMaxH3*ToVideo 的 width/height
 *  = INT min 32 / max 16384 / step 32）：首帧尺寸按 32 就近取整——标准图片模板的成图
 *  本就 32 对齐（latent 是 32 的倍数、×1.5 仍对齐）⇒ 取整是恒等变换，逐像素相同；
 *  越界（超 16384 或退化值）→ undefined，交回模板默认而不是提交非法尺寸。 */
export function alignSizeForVideoNode(
  size: { width: number; height: number } | undefined,
): { width: number; height: number } | undefined {
  if (!size) return undefined;
  const align = (value: number) => Math.max(32, Math.round(value / 32) * 32);
  const width = align(size.width);
  const height = align(size.height);
  return width <= 16384 && height <= 16384 ? { width, height } : undefined;
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

// ===== 第 34 刀：首尾帧固定落点（用户规格 2026-09-21，零 wire 前端自算）=====
// 用户口径：「固定插入对话的 content 的首段和尾段下发」——第一张图在第一段下方、
// 最后一张图在最后一段下方；与高潮点（插在**判定的段落**）不同，这里无需任何判定。

/** 段落分隔＝空行（与后端 `story_frames._PARAGRAPH_SPLIT` 同口径）。 */
const BLANK_LINE = /\r?\n[^\S\r\n]*\r?\n/;

/** 首段末＝正文第一个空行的起点；正文没有空行 ⇒ 全文末尾。 */
export function firstParagraphEnd(text: string): number {
  const match = BLANK_LINE.exec(text);
  return match ? match.index : text.length;
}

/** 末段末＝正文末尾（最后一张图必然存在，落在最末一段下方）。 */
export function lastParagraphEnd(text: string): number {
  return text.length;
}

/**
 * 首尾帧主槽落点：主槽装「本楼层新画面」——
 * - 独立图片模式且主槽装的是**新生成首帧**（`layout.main === "first_prompt"`）⇒ 首段末；
 * - 其余（尾帧新图 / 事件现成尾帧图 / 上尾帧图延续 / 视频模式下的正片）⇒ 末段末。
 *
 * ⚠ 视频模式（`video: true`）下主槽承载的是**正片**（首帧图走 `:first` 副槽），
 * 正片是整楼层的成片 ⇒ 固定落在末段末。
 */
export function firstlastMainOffset(opts: {
  transition?: string;
  prevTailUrl?: string;
  firstFrameDesc?: string;
  lastFrameDesc?: string;
  lastFrameUrl?: string;
  video: boolean;
  text: string;
}): number {
  if (opts.video) return lastParagraphEnd(opts.text);
  const layout = firstlastSlotLayout(planFirstlastFrameTasks(opts));
  return layout?.main === "first_prompt"
    ? firstParagraphEnd(opts.text)
    : lastParagraphEnd(opts.text);
}

/** P5 首尾帧生图的图片模板 values：prompt=帧画面描述（决策 A），复用插画模板的 LoRA/底图/负面/latent。 */export function firstlastFrameValues(
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
