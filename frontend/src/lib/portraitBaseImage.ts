// 第 92 刀 · 角色底图一键定妆（前端编排链）
//
// 用户规格原话：「角色底图在没有的情况下，但是有绑定多元数据插入的生图工作流模版时，
// 可以利用该工作流一键生成，生成时会参考角色条目外貌，使用绑定的角色lora和风格lora去生成」。
//
// 边界（三条，改动前先读）：
// 1. 后端 `/ai/prompt/portrait` **只出提示词**；提交、轮询、落盘、写回底图全在本模块，
//    因为一次出图是分钟级长任务，塞进单个同步 HTTP 请求会把前端请求先判死。
// 2. LoRA／底图／values 一律走插画链的两个真源（`illustrationWorkflowMedia`、
//    `illustrationTemplateValues`）——定妆只是「演员表＝该角色一人」的普通图片提交，
//    另写一套组装口径就等于第二条会漂移的链路。
//    触发词＋建议提示词同样吃真源（`applyLoraTriggerWords`）：Profile 侧明令模型不得写触发词，
//    只有程序按文件名精确查表前置，LoRA 才真会生效；缺精确记录 ⇒ 抛错不提交。
// 3. 提交→轮询→落盘任一环失败都抛错、不返回路径：半途成功一旦被写回底图，
//    坏图就成了该角色后续所有出图的长期锚点。
import type { Chat, Embed } from "../api/ai/wire";
import { genPortraitPrompt } from "../api/ai/prompts";
import { saveLocal, submitWorkflow } from "../api/comfyui";
import { listLoras } from "../api/loras";
import { SEMANTIC_PROMPT } from "../api/workflows";
import type { MediaInsertPreset } from "../stores/settings";
import { applyLoraTriggerWords } from "./chatGeneration";
import { illustrationWorkflowMedia } from "./illustrationMedia";
import {
  illustrationTemplateValues, latentSizeFor, workflowFieldBinding,
  type IllustrationAspectRatio, type SemanticField,
} from "./imagePromptProfiles";
import { pollWorkflowResult } from "./workflowGenerationRuntime";

/** 定妆产物落 `<仓库文件夹>/portraits/`：与用户上传参考图（reference/）、生成图（pictures/）
 *  分开——底图会被长期引用并写进预设，混进 pictures/ 就会被当普通产出清理掉。 */
export const PORTRAIT_BASE_IMAGE_SUBDIR = "portraits";

/** 底图是后续所有出图都要引用的角色锚点 ⇒ 一律 sfw 立绘：`anima_quality_tags("nsfw")`
 *  会往质量行塞 `sensitive, explicit`，那属于剧情帧而非定妆帧。 */
const PORTRAIT_RATING = "sfw";
// 9:16（2026-11-08 用户定案）：2:3 出图（真机 1056×1536）会把脚底挤出画框，而底图是后续所有
// 出图的形象锚点——「全身立绘」必须真的全身。画幅同时喂 prompt 与 latent 尺寸（576×1024 @1024）。
const PORTRAIT_ASPECT_RATIO: IllustrationAspectRatio = "9:16";

/** 第 95 刀 · 定妆机位合同：正面朝向镜头、双眼看向镜头（用户 2026-11-06 修订：
 *  原「身体回转约 20°」与动作表里的「回望」叠加，编译器【机位由内容反推】会选背身机位）。
 *  成稿一旦带背身标签就不许提交——这张图会被写回 baseImage，成为该角色后续所有出图的长期锚点。 */
const PORTRAIT_BACK_VIEW_RE =
  /three[-\s]quarter\s+back\s+view|from\s+behind|rear\s+view|\bback\s+view\b|view\s+from\s+behind|(?:looking|head\s+turned)\s+back\s+over\s+(?:\w+\s+)?shoulder|背对镜头|背身|侧后角度|回望镜头|回头看/i;

export function portraitCameraViolation(prompt: string): string {
  const hit = PORTRAIT_BACK_VIEW_RE.exec(prompt);
  return hit ? hit[0] : "";
}

export interface PortraitTemplateLike {
  id: string;
  exposed: readonly SemanticField[];
}

/** 宿主视图（ChatView）注入的一次性运行参数：面板自己不再推导模型/路径配置。 */
export interface PortraitRuntime {
  chat: Chat;
  comfyUrl: string;
  presetDir: string;
  presetName: string;
  userName: string;
  /** 资产库入库用的嵌入模型（与自动插画入库同一档）。 */
  embed: Embed;
}

export interface PortraitGateInput {
  name: string;
  template?: PortraitTemplateLike | undefined;
  preset?: MediaInsertPreset | undefined;
  comfyUrl?: string | undefined;
  chat?: Chat | undefined;
}

/** 唯一可用性判据（返回空串＝可以定妆）。面板据此禁用按钮，本链据此拒绝开工——
 *  判据写两处就会「面板放行、链里死」，与自动插画配置校验同一条纪律。 */
export function portraitGateReason(input: PortraitGateInput): string {
  if (!input.name.trim()) return "先填角色名（与剧情中一致），才能一键定妆。";
  if (!input.template?.id) {
    return "未选择生图工作流模板：定妆要用多元数据插入绑定的那条模板。";
  }
  if (!input.template.exposed.some((field) => workflowFieldBinding(field) === SEMANTIC_PROMPT)) {
    return "所选模板没有提示词槽，定妆描述传不进去，请换一条暴露提示词的模板。";
  }
  if (!input.comfyUrl) return "未配置 ComfyUI 地址：定妆要直接提交工作流，请先到设置里填写。";
  if (!input.chat?.modelName) return "未选择可用的对话模型：定妆提示词由提示词编译器产出。";
  return "";
}

/** 外貌来源账本（后端 `character_portrait` 的四档）→ 面板回显措辞。
 *  代码与后端的账本同源，改一档就得同时改这里，所以词表只留这一份。 */
const APPEARANCE_ORIGIN_TEXT: Record<string, string> = {
  entry: "角色条目",
  table: "通用表",
  merged: "角色条目＋通用表",
  none: "未取到外貌（提示词里没有外貌段，底图只能靠 LoRA 认人）",
};

export function portraitAppearanceOriginText(origin: string): string {
  return APPEARANCE_ORIGIN_TEXT[origin] || origin || "未知";
}

export interface PortraitBaseImageArgs extends PortraitGateInput {
  name: string;
  repoId: string;
  outputDir: string;
  comfyUrl: string;
  chat: Chat;
  preset: MediaInsertPreset;
  template: PortraitTemplateLike;
  templateName?: string;
  promptProfile?: string;
  aspectRatio?: IllustrationAspectRatio;
  presetDir?: string;
  presetName?: string;
  userName?: string;
}

/** 定妆产物入库（资产库）的入参：路径由宿主转成可 fetch 的视图地址，链本身不认识 HTTP。
 *  2026-11-08 用户口径：「底图生成的内容也是要进入资产库的」——入库失败不阻断底图绑定，
 *  但成功与否要回传，面板据此如实告知。 */
export interface PortraitIndexInput {
  repoId: string;
  prompt: string;
  imagePath: string;
  templateName: string;
  loraNames: string;
}

export interface PortraitBaseImageDeps {
  genPrompt: typeof genPortraitPrompt;
  listLoras: typeof listLoras;
  submit: typeof submitWorkflow;
  poll: typeof pollWorkflowResult;
  save: typeof saveLocal;
  index: (input: PortraitIndexInput) => Promise<void>;
}

export interface PortraitBaseImageResult {
  path: string;
  prompt: string;
  appearanceOrigin: string;
  pose: string;
  /** 产物是否已写进资产库（false ＝ 入库失败，底图绑定仍然有效）。 */
  indexed: boolean;
}

/** 一键定妆：出提示词 → 按预设组装 LoRA/底图/尺寸 → 提交图片模板 → 轮询 → 落 portraits/。 */
export async function generatePortraitBaseImage(
  args: PortraitBaseImageArgs,
  deps: PortraitBaseImageDeps,
): Promise<PortraitBaseImageResult> {
  const blocked = portraitGateReason(args);
  if (blocked) throw new Error(blocked);
  const name = args.name.trim();
  const preset = args.preset;

  const compiled = await deps.genPrompt({
    outputDir: args.outputDir,
    repoId: args.repoId,
    name,
    profile: args.promptProfile || "anima_tags",
    appearanceSource: preset.appearanceSource === "character_card" ? "character_card" : "worldbook",
    aspectRatio: args.aspectRatio || PORTRAIT_ASPECT_RATIO,
    rating: PORTRAIT_RATING,
    presetDir: args.presetDir || "",
    presetName: args.presetName || "",
    userName: args.userName || "",
    chat: args.chat,
  });
  let prompt = (compiled?.prompt || "").trim();
  if (!prompt) {
    throw new Error("后端没有编排出定妆提示词（角色外貌或 Profile 编译未产出成品），已停止生成，未提交 ComfyUI。");
  }
  const cameraViolation = portraitCameraViolation(prompt);
  if (cameraViolation) {
    throw new Error(
      `定妆成稿出现背身机位（${cameraViolation}），与「正面看向镜头」的定妆合同不符，`
      + "已停止，未提交 ComfyUI；请再点一次一键定妆。",
    );
  }

  // 演员表＝该角色一人：illustrationWorkflowMedia 按预设取角色 LoRA，multi 模式再叠风格 LoRA，
  // 都没有则退回风格 LoRA；底图缺位时回退风格底图（与自动插画逐字同一口径）。
  const media = illustrationWorkflowMedia(preset, [name], [name]);
  // 触发词＋建议提示词由程序机械前置（与自动插画同一条真源）：模型侧被明令不许写，
  // 不前置就等于挂了两条 LoRA 却不生效，而这张图会被钉成该角色后续所有出图的形象锚点。
  if (media.loras.length) {
    const items = (await deps.listLoras()).items || [];
    prompt = applyLoraTriggerWords(prompt, args.promptProfile || "anima_tags", media.loras, items);
  }
  const latentSize = latentSizeFor(
    (args.aspectRatio || PORTRAIT_ASPECT_RATIO), preset.latentLongEdge || 1024,
  );
  const values = illustrationTemplateValues(args.template.exposed, {
    prompt,
    negativePrompt: preset.negativePrompt?.trim() || undefined,
    loraName: media.loraName || undefined,
    loraWeight: media.loraWeight,
    baseImage: media.baseImage || undefined,
    latentSize,
  });
  const loras = media.loras.map((item) => ({ name: item.name, weight: item.weight }));

  // 模板没有 LoRA 槽却带上了 LoRA 栈时，后端会 422「模板没有已标注且受支持的 LoRA 加载器」。
  // 这里**不**降级：自动插画链同口径把后端原因原样透出，静默清掉 LoRA 出一张不像角色的图，
  // 比报错更难发现（那张图会被钉成该角色的底图）。
  const submitted = await deps.submit(
    args.template.id, values, args.comfyUrl, prompt, loras, preset.loraMode || "single",
  );
  const promptId = submitted?.prompt_id || "";
  if (!promptId) throw new Error("ComfyUI 没有返回任务 ID，定妆未开始。");

  const outcome = await deps.poll(promptId, args.comfyUrl, "image");
  if (outcome.kind !== "complete") {
    throw new Error(outcome.kind === "still_running"
      ? "定妆任务仍在 ComfyUI 里运行，本次等待已超时：稍后可到「后台活动」确认，或重新点一次一键定妆。"
      : outcome.error);
  }
  const image = outcome.result.images?.[0];
  if (!image) throw new Error("定妆任务完成但没有产出图片，底图未写入。");

  const saved = await deps.save({
    img: image,
    repoId: args.repoId,
    outputDir: args.outputDir,
    url: args.comfyUrl,
    subdir: PORTRAIT_BASE_IMAGE_SUBDIR,
  });
  const path = saved?.path || "";
  if (!path) throw new Error("定妆产物未能保存到作品文件夹，底图未写入。");

  // 资产库入库：底图是长期锚点，也应像插画一样可被检索/回看（否则预设一丢，
  // 磁盘上的 portraits/ 就从界面彻底消失——2026-11-07/08 两次实锤的次生损失）。
  // 入库失败只降级回显，不回滚底图：绑定已经成立，丢资产库记录比丢底图轻。
  let indexed = false;
  try {
    await deps.index({
      repoId: args.repoId,
      prompt,
      imagePath: path,
      templateName: args.templateName || "",
      loraNames: media.loras.map((item) => item.name).join(","),
    });
    indexed = true;
  } catch { /* 入库失败不影响底图绑定，面板如实回显 */ }

  return {
    path, prompt, indexed,
    appearanceOrigin: compiled.appearance_origin || "",
    pose: compiled.pose || "",
  };
}
