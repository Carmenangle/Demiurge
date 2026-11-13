// 聊天室自发照片的 ComfyUI 出图链（2026-11-10 用户口径）。
//
// 用户原话：「多元数据插入等上方的功能栏对话都能用到，生图肯定靠的是多元数据插入的设置」。
// 聊天自拍不能再走生图 API：那条链路用的是设置里的出图模型密钥，与剧情插画不是同一套
// 模板/LoRA/角色底图。这里把「一条现成画面提示词 → 一张图」抽成一次性提交：
//   编译提示词 → 按多元数据插入预设组 LoRA/底图/尺寸 → 提交图片模板 → 轮询 → 落盘。
// 与 `portraitBaseImage.ts` 同一套真源（`illustrationWorkflowMedia` /
// `illustrationTemplateValues` / `submitWorkflow` / `pollWorkflowResult`），
// 区别只在于提示词由角色自己给出，而不是按角色条目编制定妆提示词。
import type { Chat } from "../api/ai/wire";
import { genPrompt, genProfilePrompt } from "../api/ai/prompts";
import { localViewUrl, saveLocal, submitWorkflow, interruptComfy } from "../api/comfyui";
import { listLoras } from "../api/loras";
import type { Template } from "../api/workflows";
import type { IllustrationSceneSpec } from "../api/chatStreamProtocol";
import type { MediaInsertPreset } from "../stores/settings";
import { applyLoraTriggerWords } from "./chatGeneration";
import { illustrationWorkflowMedia } from "./illustrationMedia";
import {
  ensureAnimaIllustrationStyle, illustrationTemplateValues, latentSizeFor,
  normalizePromptProfile, replacePromptQualityLine,
  type IllustrationAspectRatio,
} from "./imagePromptProfiles";
import { pollWorkflowResult } from "./workflowGenerationRuntime";

/** 聊天自拍按手机竖拍取景（3:4）；模板的 latentLongEdge 决定实际边长。 */
const CHAT_MEDIA_ASPECT_RATIO: IllustrationAspectRatio = "3:4";

/** Anima 档额外的写实负向：最新婚纱照被 professional photography / bridal portrait
 *  带到半写实并换了脸（2026-11-11 用户实锤）。只压画风，不压画面内容。 */
const ANIMA_CHAT_NEGATIVE = [
  "photorealistic", "photo-realistic", "realistic", "3d render", "professional photography",
].join(", ");

/** 正向里的写实/摄影标签：Anima 档必须从 tags 行删掉，不能只靠负向。
 *  实锤：`realistic photography` 留在正向时，Krea2 即使挂平涂 LoRA 也会出半写实婚纱照。 */
const ANIMA_PHOTO_TAGS = new Set([
  "photorealistic", "photo-realistic", "photo realistic", "realistic", "realism",
  "realistic photography", "professional photography", "photography", "photo",
  "3d render", "3d rendering",
]);

export function stripAnimaPhotoStyleTags(prompt: string): string {
  const lines = prompt.split("\n");
  const clean = (lines[0] || "").split(",")
    .map((tag) => tag.trim())
    .filter((tag) => tag && !ANIMA_PHOTO_TAGS.has(tag.toLowerCase()))
    .join(", ");
  lines[0] = clean;
  return lines.join("\n");
}

/** 聊天自拍 → Profile 编译器的最小画面合同。外貌锚点由后端 media_request 带入，
 *  和定妆/剧情插画一样用于锁发色、瞳色、身高与体型；不再只靠角色这一轮自觉写全。 */
export function chatMediaSceneSpec(args: {
  prompt: string;
  appearance?: string;
  speaker: string;
  repoId: string;
  aspectRatio?: IllustrationAspectRatio;
}): IllustrationSceneSpec {
  const scene = (args.prompt || "").trim();
  return {
    narrative: scene,
    draft_prompt: "",
    appearance: (args.appearance || "").trim(),
    wardrobe: "",
    locale: "",
    actors: [args.speaker],
    rating: "sfw",
    repo_id: args.repoId,
    aspect_ratio: args.aspectRatio || CHAT_MEDIA_ASPECT_RATIO,
  };
}

/** Profile 编译后的统一收尾：Anima 档换成预设质量行，并用风格 LoRA 锁死手绘二次元。 */
export function finalizeChatMediaPrompt(
  prompt: string,
  profile: string,
  qualityPrompt: string,
  hasStyleLora: boolean,
): string {
  if (normalizePromptProfile(profile) !== "anima_tags") return prompt;
  const withQuality = replacePromptQualityLine(
    stripAnimaPhotoStyleTags(prompt), qualityPrompt || "", "sfw",
  );
  return ensureAnimaIllustrationStyle(withQuality, hasStyleLora);
}

/** 负向提示词：Anima 档追加写实/摄影负向；其他档保持用户原样。 */
export function chatMediaNegativePrompt(base: string, profile: string): string {
  const negative = (base || "").trim();
  if (normalizePromptProfile(profile) !== "anima_tags") return negative;
  return negative ? `${negative}, ${ANIMA_CHAT_NEGATIVE}` : ANIMA_CHAT_NEGATIVE;
}

/** 编译后强制追加角色固定外貌：编译器偶尔只产出通用女性标签，模型于是随机换发色/瞳色。 */
export function appendCharacterAnchor(prompt: string, appearance?: string): string {
  const anchor = (appearance || "").trim();
  if (!anchor) return prompt;
  const base = (prompt || "").trim();
  // 已经包含锚点主体时不再重复追加。
  if (base.includes(anchor.slice(0, 40))) return base;
  return base
    ? `${base}\n人物固定外貌（不得改动，忽略画面里冲突的发色/瞳色/体型）：${anchor}`
    : `人物固定外貌（不得改动）：${anchor}`;
}

export interface ChatMediaComfyArgs {
  /** 角色在回复里给出的画面提示词（通常是中文画面描述）。 */
  prompt: string;
  speaker: string;
  /** 多元数据插入里的角色键（世界书全名与预设短名可能不同，调用方先解析）。 */
  actorKey?: string;
  /** 角色固定外貌（后端 media_request.appearance）：喂给 Profile 编译器锁脸。 */
  appearance?: string;
  /** 偏置预设：与剧情插画同一条 Profile 编译口径。 */
  presetDir?: string;
  presetName?: string;
  userName?: string;
  repoId: string;
  outputDir: string;
  comfyUrl: string;
  chat: Chat;
  preset: MediaInsertPreset;
  template: Template;
  /** 聊天图默认 3:4；头像用独立的 1:1 再生成一张。 */
  aspectRatio?: IllustrationAspectRatio;
  /** 单次轮询硬超时；头像用更短的超时，避免卡住 ComfyUI 队列。 */
  pollTimeoutMs?: number;
  /** 提交成功回调：调用方据此持久化 prompt_id，刷新后可续接。 */
  onSubmitted?: (promptId: string) => void;
}

export interface ChatMediaComfyResult {
  /** 可直接放进 <img>/聊天气泡的本地视图地址。 */
  url: string;
  /** 落盘后的本机路径。 */
  path: string;
  /** 实际提交给 ComfyUI 的成稿提示词（便于 trace/排错）。 */
  prompt: string;
}

/**
 * 用多元数据插入预设生成一张聊天自拍。
 *
 * 失败必须抛错：调用方据此在聊天气泡里显示真实原因，不能静默成「没有返回内容」。
 */
export async function generateChatMediaImage(
  args: ChatMediaComfyArgs,
): Promise<ChatMediaComfyResult> {
  const scene = (args.prompt || "").trim();
  if (!scene) throw new Error("画面提示词为空");
  const preset = args.preset;
  const actor = (args.actorKey || args.speaker || "").trim();
  const profile = normalizePromptProfile(preset.promptProfile || "anima_tags");

  // 聊天提示词是角色口吻的中文画面描述；必须走与剧情插画同一条 Profile 编译器。
  // 通用 /ai/prompt 只按「主体/画风/光影」自由扩写，会把电影感/柔焦扩成
  // professional photography / bridal portrait，把 Krea2 拉成写实婚纱照
  // （2026-11-11 用户实锤：同一张脸变成深棕发陌生女性）。
  let prompt = scene;
  try {
    const compiled = await genProfilePrompt(
      profile,
      chatMediaSceneSpec({
        prompt: scene,
        appearance: args.appearance,
        speaker: args.speaker,
        repoId: args.repoId,
        aspectRatio: args.aspectRatio,
      }),
      args.chat,
      {
        presetDir: args.presetDir,
        presetName: args.presetName,
        userName: args.userName,
      },
    );
    prompt = (compiled?.prompt || "").trim() || scene;
  } catch {
    // Profile 编译失败退回通用编译器；中文原文直接提交会出随机画面。
    // 外貌锚点仍要带上，否则退回通用编译时又会变成「成熟优雅的女性」。
    try {
      const fallbackScene = args.appearance
        ? `${scene}\n人物固定外貌（不得改动）：${args.appearance}`
        : scene;
      const compiled = await genPrompt(fallbackScene, args.chat);
      prompt = (compiled?.prompt || "").trim() || fallbackScene;
    } catch { /* 两个编译器都失败：保留原文，至少还能出图 */ }
  }

  // 演员表＝当前发言角色一人：角色 LoRA/底图按预设取，风格 LoRA 由 loraMode 决定。
  const media = illustrationWorkflowMedia(preset, [actor], [actor]);
  prompt = appendCharacterAnchor(
    finalizeChatMediaPrompt(
      prompt, profile, preset.qualityPrompt || "",
      media.loras.some((item) => !item.character),
    ),
    args.appearance,
  );
  if (media.loras.length) {
    const items = (await listLoras()).items || [];
    prompt = applyLoraTriggerWords(
      prompt, profile, media.loras, items,
    );
  }
  const latentSize = latentSizeFor(
    args.aspectRatio || CHAT_MEDIA_ASPECT_RATIO, preset.latentLongEdge || 1024,
  );
  const values = illustrationTemplateValues(args.template.exposed, {
    prompt,
    negativePrompt: chatMediaNegativePrompt(preset.negativePrompt || "", profile) || undefined,
    loraName: media.loraName || undefined,
    loraWeight: media.loraWeight,
    baseImage: media.baseImage || undefined,
    latentSize,
  });
  const loras = media.loras.map((item) => ({ name: item.name, weight: item.weight }));

  const submitted = await submitWorkflow(
    args.template.id, values, args.comfyUrl, prompt, loras, preset.loraMode || "single",
  );
  const promptId = submitted?.prompt_id || "";
  if (!promptId) throw new Error("ComfyUI 没有返回任务 ID");
  args.onSubmitted?.(promptId);

  const outcome = await pollWorkflowResult(promptId, args.comfyUrl, "image", {
    timeoutMs: args.pollTimeoutMs,
  });
  if (outcome.kind !== "complete") {
    if (outcome.kind === "still_running" && args.pollTimeoutMs != null) {
      // 短超时的头像任务超时后主动中断，避免一直占着 ComfyUI 队列。
      void interruptComfy(args.comfyUrl, promptId).catch(() => undefined);
    }
    throw new Error(outcome.kind === "still_running"
      ? "拍摄任务仍在 ComfyUI 里运行，稍后可在后台活动查看"
      : outcome.error);
  }
  const image = outcome.result.images?.[0];
  if (!image) throw new Error("拍摄完成但没有产出图片");

  const saved = await saveLocal({
    img: image,
    repoId: args.repoId,
    outputDir: args.outputDir,
    url: args.comfyUrl,
  });
  const path = saved?.path || "";
  if (!path) throw new Error("拍摄产物未能保存到作品文件夹");
  return { url: localViewUrl(path), path, prompt };
}

/** 刷新后只续接轮询/落盘，不重新提交 ComfyUI 任务。 */
export interface ChatMediaResumeArgs {
  promptId: string;
  prompt: string;
  repoId: string;
  outputDir: string;
  comfyUrl: string;
  pollTimeoutMs?: number;
}

export async function resumeChatMediaImage(
  args: ChatMediaResumeArgs,
): Promise<ChatMediaComfyResult> {
  const promptId = (args.promptId || "").trim();
  if (!promptId) throw new Error("没有可续接的拍摄任务");
  const outcome = await pollWorkflowResult(promptId, args.comfyUrl, "image", {
    timeoutMs: args.pollTimeoutMs,
  });
  if (outcome.kind !== "complete") {
    throw new Error(outcome.kind === "still_running"
      ? "拍摄任务仍在 ComfyUI 里运行，稍后可在后台活动查看"
      : outcome.error);
  }
  const image = outcome.result.images?.[0];
  if (!image) throw new Error("拍摄完成但没有产出图片");
  const saved = await saveLocal({
    img: image,
    repoId: args.repoId,
    outputDir: args.outputDir,
    url: args.comfyUrl,
  });
  const path = saved?.path || "";
  if (!path) throw new Error("拍摄产物未能保存到作品文件夹");
  return { url: localViewUrl(path), path, prompt: args.prompt };
}

/**
 * 哪些失败值得等 GPU 空闲后重拍。
 * 「仍在 ComfyUI 里运行」也重拍：先等队列清空，避免和原任务抢显卡；
 * 模板/提示词/配置错误重拍也没用，直接交给调用方降级。
 */
export function isRetryableChatMediaError(error: unknown): boolean {
  const detail = error instanceof Error ? error.message : String(error);
  return !/提示词为空|模板不存在|未配置|未绑定/.test(detail);
}

export interface ChatMediaRetryOptions {
  attempts?: number;
  delayMs?: number;
  /** 重拍前等待（例如等 GPU 空闲）；抛错即放弃重拍，交给调用方降级。 */
  beforeRetry?: (attempt: number, error: unknown) => Promise<void> | void;
  onRetry?: (attempt: number, error: unknown) => void;
}

/** 可测试的重拍骨架：首次失败 → 通知调用方 → 等 GPU 空闲/退避 → 再试一次。 */
export async function runChatMediaWithRetry<T>(
  run: () => Promise<T>,
  options: ChatMediaRetryOptions = {},
): Promise<T> {
  const attempts = Math.max(1, options.attempts ?? 2);
  let last: unknown;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await run();
    } catch (error) {
      last = error;
      if (attempt >= attempts - 1 || !isRetryableChatMediaError(error)) throw error;
      options.onRetry?.(attempt + 1, error);
      if (options.beforeRetry) {
        await options.beforeRetry(attempt + 1, error);
      } else {
        await new Promise((resolve) => setTimeout(resolve, options.delayMs ?? 3000));
      }
    }
  }
  throw last;
}

/**
 * 聊天图失败后确认并重拍一次：第一次失败只提示「正在重拍」，不把技术原因
 * 铺到聊天主区；第二次仍失败才交给调用方显示一句短提示。
 */
export function generateChatMediaImageWithRetry(
  args: ChatMediaComfyArgs,
  options: ChatMediaRetryOptions = {},
): Promise<ChatMediaComfyResult> {
  return runChatMediaWithRetry(() => generateChatMediaImage(args), options);
}
