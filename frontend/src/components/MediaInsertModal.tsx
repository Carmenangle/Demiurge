import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Settings } from "lucide-react";
import type { Template } from "../api/workflows";
import {
  SEMANTIC_PROMPT, SEMANTIC_NEGATIVE_PROMPT, SEMANTIC_LORA_NAME, SEMANTIC_BASE_IMAGE,
  SEMANTIC_LATENT_WIDTH, SEMANTIC_LATENT_HEIGHT,
  SEMANTIC_VOICE_TEXT, SEMANTIC_VOICE_REFERENCE,
  SEMANTIC_REFERENCE_IMAGE, SEMANTIC_REFERENCE_IMAGE_FILES,
} from "../api/workflows";
import { availableLoras, listLoras, type AvailableLora } from "../api/loras";
import { genPortraitPrompt, getProfilePromptDefaults, indexGeneration } from "../api/ai";
import { uploadChatBg, uploadVoice } from "../api/userState";
import { localViewUrl, saveLocal, submitWorkflow } from "../api/comfyui";
import { ensureComfyReady } from "../lib/comfyReadiness";
import { openLightbox } from "./Lightbox";
import type { MediaInsertPreset, CharacterLoraBinding, CharacterVoiceBinding } from "../stores/settings";
import {
  isCustomPromptProfile, normalizePromptProfile, PROMPT_PROFILE_OPTIONS, workflowFieldBinding,
} from "../lib/imagePromptProfiles";
import { imageTemplateCapabilities, resolveIllustrationImageMode } from "../lib/illustrationMedia";
import { videoTemplatesForMode } from "../lib/promptTemplates";
import {
  PORTRAIT_BASE_IMAGE_SUBDIR, generatePortraitBaseImage, portraitAppearanceOriginText,
  portraitGateReason, type PortraitRuntime,
} from "../lib/portraitBaseImage";
import { pollWorkflowResult } from "../lib/workflowGenerationRuntime";
import type { PromptTemplate } from "../api/ai/promptTemplates";
import { IllustrationPromptModal } from "./IllustrationPromptModal";
import { findBindingKey } from "../lib/characterRoster";

interface Props {
  templates: Template[];
  cardName?: string;   // 本作品主角色卡名，用于预填首行角色
  cardNames?: string[]; // 本作品绑定的全部角色卡
  /** 合集卡：后端从世界书识别出的角色名；用于首次自动补全角色行，避免逐个手输。 */
  rosterNames?: string[];
  modelsDir: string;   // ComfyUI models 目录，用于直接扫 loras 子目录列出可选模型
  repoId?: string;     // 本作品 id，音轨落 <repo>/voices/
  outputDir?: string;  // 仓库文件夹根，音轨落 <outputDir>/<作品>/voices/
  preset?: MediaInsertPreset;
  /** 从「底图生成完毕」小窗跳进来时，滚动并高亮到这个角色的行。 */
  focusCharacter?: string;
  // 第 92 刀：一键定妆的运行参数（对话模型 / ComfyUI 地址 / 预设档）。
  // 缺位＝宿主没备好这条链（如别的视图复用本面板），此时不出现定妆按钮。
  portraitRuntime?: PortraitRuntime;
  // 2026-10-25 提示词模版：内置只读 + 我的模版（生图模式/视频模式两个选择器共用）
  promptTemplateBuiltins?: PromptTemplate[];
  promptTemplateItems?: PromptTemplate[];
  onSave: (preset: MediaInsertPreset) => void;
  // 不关面板的即时落盘（一键定妆成功即用）：底图是分钟级产物，不能因为「忘了再点保存」
  // 只留在磁盘上而从预设里丢掉（2026-11-07 埃吉尔实锤）。缺省＝旧行为，提示文案同步降级。
  onPersist?: (preset: MediaInsertPreset) => void;
  /** 底图成功后回调（面板已关闭时，宿主据此暂存并弹「是否插入」确认窗）。 */
  onPortraitReady?: (name: string, preset: MediaInsertPreset, path: string) => void;
  onClose: () => void;
}

// 一行「角色名 + LoRA + 权重 + 底图」的可编辑结构（内部态，保存时回填 characterLoras）。
interface CharRow extends CharacterLoraBinding {
  name: string;
  // 已有底图时再定妆 = 两张候选并列（2026-11-08 用户口径：可观看但不能保存，删掉一张才能定稿）。
  // 只在面板内存在、不写进预设；删到一张即落成 baseImage 并自动落盘。
  baseCandidates?: string[];
}

// 一行「角色名 + 参考音轨」的可编辑结构（内部态，保存时回填 characterVoices）。
interface VoiceRow extends CharacterVoiceBinding {
  name: string;
}

// 底图缩略图右上角的删除小钮（2026-11-08 用户口径：候选/单一底图都要能就地删掉）。
const BASE_DELETE_BTN_STYLE: CSSProperties = {
  position: "absolute", top: -6, right: -6, width: 16, height: 16, borderRadius: "50%",
  border: "none", background: "#c0392b", color: "#fff", fontSize: 12, lineHeight: 1,
  padding: 0, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center",
};

function loraStatusSuffix(lora: AvailableLora, characterRow = false): string {
  if (lora.trigger_status === "not_required") {
    // 角色行上「已确认无需触发词」多半是漏登记（登记页空保存即成此态）——
    // 角色画面缺触发词会直接不像角色（2026-08-29 验收问题③），必须醒目提示，
    // 不能像风格行那样显示成安心的「通用·无需触发词」。
    return characterRow
      ? "（未登记触发词·画面可能不像角色——请在 LoRA 触发词页登记或确认通用）"
      : "（通用·无需触发词）";
  }
  if (lora.has_triggers || lora.trigger_status === "configured") return "";
  return characterRow
    ? "（触发词未登记·画面可能不像角色——请在 LoRA 触发词页登记）"
    : "（触发词未确认·仍可使用）";
}

export function suggestedWeightForLora(loras: readonly AvailableLora[], loraName: string): number {
  return loras.find((lora) => lora.lora_name === loraName)?.suggested_weight ?? 0.8;
}

function initialRows(
  preset?: MediaInsertPreset, cardName?: string, cardNames: string[] = [],
  rosterNames: string[] = [],
): CharRow[] {
  const map = preset?.characterLoras || {};
  const rows: CharRow[] = Object.entries(map).map(([name, b]) => ({ name, ...b }));
  // 已有配置（含用户删剩的行）优先，绝不把删掉的角色自动加回。
  if (rows.length) return rows;
  // 合集卡首次打开：按世界书名册直接铺好角色行；普通多卡玩法不自动预填（保持旧口径）。
  if (rosterNames.length) return rosterNames.map((name) => ({ name }));
  return [{ name: "" }];
}

function initialVoiceRows(preset?: MediaInsertPreset, _cardNames: string[] = []): VoiceRow[] {
  const map = preset?.characterVoices || {};
  const rows: VoiceRow[] = Object.entries(map).map(([name, v]) => ({ name, ...v }));
  // 不再自动预填绑定卡角色——与 LoRA 区一致：用户删除后保存，下次打开不应又被加回。
  return rows.length ? rows : [{ name: "" }];
}

// 多元数据插入：为本作品预设剧情高潮点异步出图用的 ComfyUI 工作流模板 + 按角色 LoRA/底图。
// 提示词由后端高潮点提取，运行时按在场角色取该角色 LoRA/底图，无则回退风格 LoRA + 风格底图。
export function MediaInsertModal({ templates, cardName, cardNames = [], rosterNames = [], modelsDir, repoId = "", outputDir = "", preset, focusCharacter, portraitRuntime, promptTemplateBuiltins = [], promptTemplateItems = [], onSave, onPersist, onPortraitReady, onClose }: Props) {
  // 三开关：剧情自动生成时按勾选类型分发。旧预设无 enable 字段 → 默认按已有配置回填。
  const [enableImage, setEnableImage] = useState(preset?.enableImage ?? true);
  const [enableVideo, setEnableVideo] = useState(
    preset?.enableVideo
      ?? !!(preset?.videoTemplateId || preset?.videoTemplates?.image2video
        || preset?.videoTemplates?.firstlast),
  );
  const [enableAudio, setEnableAudio] = useState(preset?.enableAudio ?? !!preset?.audioTemplateId);
  const [templateId, setTemplateId] = useState(preset?.templateId || "");
  // 2026-11-12 用户定案：图片模板按能力分槽。text2img 仍存 legacy `templateId`；
  // 其余三槽为空时逐镜回落 text2img（自动降级，不阻断出图）。
  const [img2imgTemplateId, setImg2imgTemplateId] = useState(
    preset?.imageTemplates?.img2img
      || (preset?.imageMode === "img2img" ? preset?.templateId || "" : ""),
  );
  const [inpaintTemplateId, setInpaintTemplateId] = useState(
    preset?.imageTemplates?.inpaint || "",
  );
  const [referenceTemplateId, setReferenceTemplateId] = useState(
    preset?.imageTemplates?.reference || "",
  );
  const [loraMode, setLoraMode] = useState<"none" | "single" | "multi">(
    preset?.loraMode || "single",
  );
  const [appearanceSource, setAppearanceSource] = useState<"worldbook" | "character_card">(
    preset?.appearanceSource === "character_card" ? "character_card" : "worldbook",
  );
  // 生图方式（2026-11-08）：文生图不读底图；图生图把角色/风格底图送进 ComfyUI 或云端图生图。
  // 旧预设按 resolveIllustrationImageMode 的隐式口径回填，保证升级后行为不变。
  const [imageMode, setImageMode] = useState<"text2img" | "img2img">(
    () => resolveIllustrationImageMode(preset),
  );
  // 画面人称（2026-09-20）："" = 自动（后端按锚点段判）；手选后优先于自动判定。
  const [viewpoint, setViewpoint] = useState<"" | "first_person" | "third_person">(
    preset?.viewpoint === "first_person" || preset?.viewpoint === "third_person"
      ? preset.viewpoint : "",
  );
  // 批量生图数目（2026-09-24）：仅**高潮点模式**（非首尾帧）出现，最小 1、无上限。
  // 未设/非法 ⇒ 1（单张，与改造前逐字一致）。
  const [batchImageCount, setBatchImageCount] = useState<number>(() => {
    const raw = preset?.batchImageCount;
    return Number.isFinite(raw) && (raw as number) >= 1 ? Math.floor(raw as number) : 1;
  });
  const [promptProfile, setPromptProfile] = useState(() => normalizePromptProfile(preset?.promptProfile));
  const [qualityPrompt, setQualityPrompt] = useState(preset?.qualityPrompt || "");
  const [negativePrompt, setNegativePrompt] = useState(preset?.negativePrompt || "");
  const [latentLongEdge, setLatentLongEdge] = useState<1024 | 2048 | 4096>(
    preset?.latentLongEdge === 2048 || preset?.latentLongEdge === 4096
      ? preset.latentLongEdge : 1024,
  );
  const [rows, setRows] = useState<CharRow[]>(() => initialRows(preset, cardName, cardNames, rosterNames));
  // 串行批量补齐时每次回调都闭包着点击那一刻的 rows；只看 state 会把上一张新底图漏掉，
  // 于是后一次自动落盘反而把前一张从预设里抹掉。rowsRef 始终指向最新行集。
  const rowsRef = useRef<CharRow[]>(rows);
  const commitRows = (next: CharRow[]) => { rowsRef.current = next; setRows(next); };
  const [styleLora, setStyleLora] = useState(preset?.styleLora || "");
  const [styleLoraWeight, setStyleLoraWeight] = useState(preset?.styleLoraWeight ?? 0.8);
  const [styleBaseImage, setStyleBaseImage] = useState(preset?.styleBaseImage || "");
  const [videoTemplateId, setVideoTemplateId] = useState(preset?.videoTemplateId || "");
  // 2026-11-12：视频模板按能力分槽；未填的槽回落 legacy `videoTemplateId`。
  const [image2videoTemplateId, setImage2videoTemplateId] = useState(
    preset?.videoTemplates?.image2video || "",
  );
  const [firstlastTemplateId, setFirstlastTemplateId] = useState(
    preset?.videoTemplates?.firstlast || "",
  );
  const [smartVideo, setSmartVideo] = useState(preset?.smartVideo ?? false);
  // 2026-10-25 视频提示词模版（按当前视频模式派发：高潮点/首尾帧各显示对应 kind）
  const [videoPromptTemplateId, setVideoPromptTemplateId] = useState(preset?.videoPromptTemplateId || "");
  // 首尾帧生成（用户定稿 2026-08-28）：旧预设按 videoMode === "firstlast" 回填
  const [firstlast, setFirstlast] = useState(preset?.firstlast ?? preset?.videoMode === "firstlast");
  // 2026-11-09 全局模式默认分镜序列；关闭回退旧整楼 firstlast 长片。
  const [shotSequence, setShotSequence] = useState(preset?.shotSequence ?? true);
  const [audioTemplateId, setAudioTemplateId] = useState(preset?.audioTemplateId || "");
  const [voiceRows, setVoiceRows] = useState<VoiceRow[]>(() => initialVoiceRows(preset, cardNames));
  const [uploadingVoice, setUploadingVoice] = useState("");  // 正在上传音轨的行 key（""=无）
  const [loras, setLoras] = useState<AvailableLora[]>([]);
  const [loraLoadState, setLoraLoadState] = useState<"idle" | "loading" | "ready" | "error">(
    modelsDir ? "loading" : "idle",
  );
  const [loraLoadError, setLoraLoadError] = useState("");
  const [loraReloadKey, setLoraReloadKey] = useState(0);
  const [uploading, setUploading] = useState("");  // 正在上传底图的行 key（""=无）
  // 第 92 刀 · 一键定妆：定妆是分钟级的 ComfyUI 任务，面板一次只放行一个在途任务。
  const [portraitBusy, setPortraitBusy] = useState(-1);  // 正在定妆的行号（-1=无）
  const [portraitBatch, setPortraitBatch] = useState(false); // 正在批量补齐缺失底图
  const [portraitNote, setPortraitNote] = useState<{ row: number; ok: boolean; text: string } | null>(null);
  // 提示词设置（右上角齿轮）：控制给 LLM 看的提示词。当前只开放高潮点判据一项。
  const [promptSettingsOpen, setPromptSettingsOpen] = useState(false);

  useEffect(() => {
    // 提示词设置子层打开时 Esc 只关子层（与 PresetModal 的嵌套确认框同口径）。
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !promptSettingsOpen) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, promptSettingsOpen]);

  useEffect(() => {
    // 从「底图生成完毕」小窗跳进来：滚到该角色行并短暂高亮，用户只需确认后保存。
    if (!focusCharacter) return;
    const target = Array.from(
      document.querySelectorAll<HTMLElement>("[data-character-row]"),
    ).find((el) => el.getAttribute("data-character-row") === focusCharacter);
    if (!target) return;
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    target.classList.add("portrait-focus");
    const timer = window.setTimeout(() => target.classList.remove("portrait-focus"), 2400);
    return () => window.clearTimeout(timer);
  }, [focusCharacter]);

  useEffect(() => {
    let active = true;
    let retryTimer: number | undefined;
    if (!modelsDir) {
      setLoras([]);
      setLoraLoadState("idle");
      setLoraLoadError("");
      return () => { active = false; };
    }
    setLoras([]);
    setLoraLoadState("loading");
    setLoraLoadError("");
    const load = async (attempt: number) => {
      try {
        const response = await availableLoras(modelsDir);
        if (!active) return;
        setLoras(response.items || []);
        setLoraLoadState("ready");
      } catch (error) {
        if (!active) return;
        if (attempt < 2) {
          retryTimer = window.setTimeout(() => { void load(attempt + 1); }, 500 * (attempt + 1));
          return;
        }
        setLoraLoadState("error");
        setLoraLoadError(error instanceof Error ? error.message : "未知错误");
      }
    };
    void load(0);
    return () => {
      active = false;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    };
  }, [modelsDir, loraReloadKey]);

  useEffect(() => {
    // 2026-10-25：anima 内置档 + 自定义 anima 模版都预填质量/负面词
    // （自定义模版的值经 /ai/prompt/templates 详情端点取，见 getProfilePromptDefaults）
    const customKind = isCustomPromptProfile(promptProfile)
      ? promptTemplateItems.find((t) => `custom:${t.id}` === promptProfile)?.kind
      : undefined;
    const animaFamily = promptProfile === "anima_tags" || customKind === "anima_tags";
    if (!animaFamily || (qualityPrompt && negativePrompt)) return;
    getProfilePromptDefaults(promptProfile).then((defaults) => {
      // `|| ""` 不是兜底而是守住 state 的类型声明：这两个 state 全程按 string 用
      // （保存时 `.trim()`），响应缺键时塞 undefined 进去会让面板直接炸。
      setQualityPrompt((current) => current || defaults.quality_prompt || "");
      setNegativePrompt((current) => current || defaults.negative_prompt || "");
    }).catch(() => {});
  }, [promptProfile, promptTemplateItems, qualityPrompt, negativePrompt]);

  // 2026-10-25 提示词模版：选中项的内置协议族（custom 模版被删时后端按它降级）；
  // anima 家族（内置 anima + 自定义 anima 模版）才显示/保存质量与负面词。
  const selectedCustomKind = isCustomPromptProfile(promptProfile)
    ? promptTemplateItems.find((t) => `custom:${t.id}` === promptProfile)?.kind
    : undefined;
  const animaFamily = promptProfile === "anima_tags" || selectedCustomKind === "anima_tags";

  const tpl = templates.find((t) => t.id === templateId);
  const hasPrompt = !!tpl?.exposed.some((f) => workflowFieldBinding(f) === SEMANTIC_PROMPT);
  const hasNegativePrompt = !!tpl?.exposed.some(
    (f) => workflowFieldBinding(f) === SEMANTIC_NEGATIVE_PROMPT,
  );
  const hasLoraSlot = !!tpl?.exposed.some((f) => workflowFieldBinding(f) === SEMANTIC_LORA_NAME);
  const hasLatentWidth = !!tpl?.exposed.some((f) => workflowFieldBinding(f) === SEMANTIC_LATENT_WIDTH);
  const hasLatentHeight = !!tpl?.exposed.some((f) => workflowFieldBinding(f) === SEMANTIC_LATENT_HEIGHT);
  // 2026-11-12：分槽模板的实际能力；槽位为空时逐镜回落 text2img，不阻断保存。
  const img2imgCaps = imageTemplateCapabilities(
    templates.find((item) => item.id === img2imgTemplateId),
  );
  const inpaintCaps = imageTemplateCapabilities(
    templates.find((item) => item.id === inpaintTemplateId),
  );
  const referenceCaps = imageTemplateCapabilities(
    templates.find((item) => item.id === referenceTemplateId),
  );
  const img2imgReady = img2imgCaps.includes("img2img") || img2imgCaps.includes("inpaint");
  const inpaintReady = inpaintCaps.includes("inpaint") && inpaintCaps.includes("img2img");
  const referenceReady = referenceCaps.includes("reference");

  const audioTpl = templates.find((t) => t.id === audioTemplateId);
  const hasVoiceText = !!audioTpl?.exposed.some((f) => workflowFieldBinding(f) === SEMANTIC_VOICE_TEXT);
  const hasVoiceRef = !!audioTpl?.exposed.some((f) => workflowFieldBinding(f) === SEMANTIC_VOICE_REFERENCE);

  const setRow = (i: number, patch: Partial<CharRow>) =>
    commitRows(rowsRef.current.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addRow = () => commitRows([...rowsRef.current, { name: "" }]);
  const delRow = (i: number) => commitRows(rowsRef.current.filter((_, idx) => idx !== i));
  // 合集卡：把名册里尚未配置的角色补进行内；只补缺，不覆盖用户已填的 LoRA/底图。
  // 世界书全名（腓特烈大帝）与已填短名（腓特烈）按包含/前缀视为同一角色，不重复添加。
  const addMissingRosterRows = () => {
    const existing = new Set(
      rowsRef.current.map((row) => row.name.trim()).filter(Boolean),
    );
    const missing = rosterNames.filter((name) => (
      name && !existing.has(name) && !findBindingKey(name, [...existing])
    ));
    if (!missing.length) return;
    const kept = rowsRef.current.filter((row) => row.name.trim() || Object.keys(row).length > 1);
    commitRows([...kept, ...missing.map((name) => ({ name }))]);
  };

  const setVoiceRow = (i: number, patch: Partial<VoiceRow>) =>
    setVoiceRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addVoiceRow = () => setVoiceRows((prev) => [...prev, { name: "" }]);
  const delVoiceRow = (i: number) => setVoiceRows((prev) => prev.filter((_, idx) => idx !== i));

  const pickVoice = async (i: number, f: File | null | undefined) => {
    if (!f) return;
    setUploadingVoice(String(i));
    try {
      const r = await uploadVoice(f, repoId, outputDir);
      if (r.ok) setVoiceRow(i, { voiceRef: r.path });
    } catch { /* 忽略：用户可重试 */ }
    finally { setUploadingVoice(""); }
  };

  const pickBase = async (i: number, f: File | null | undefined) => {
    if (!f) return;
    setUploading(String(i));
    try {
      const r = await uploadChatBg(f);
      if (r.ok) setRow(i, { baseImage: r.path, baseCandidates: undefined });
    } catch { /* 忽略：用户可重试 */ }
    finally { setUploading(""); }
  };

  const pickStyleBase = async (f: File | null | undefined) => {
    if (!f) return;
    setUploading("style");
    try {
      const r = await uploadChatBg(f);
      if (r.ok) setStyleBaseImage(r.path);
    } catch { /* 忽略 */ }
    finally { setUploading(""); }
  };

  // 校验：角色无 LoRA 时必须给底图（设定要求）。有名字的行才校验。
  // 底图只有在图生图模式下才算「一致性锚点」——文生图模式它不参与，别拿它掩盖缺锚点。
  const hasAnyBaseImage = rows.some((r) => r.name.trim() && !!r.baseImage) || !!styleBaseImage;
  const missingBase = rows.some((r) => r.name.trim()
    && (loraMode === "none" || !r.loraName)
    && !(imageMode === "img2img" && r.baseImage)
    && (loraMode === "none" || !styleLora)
    && !(imageMode === "img2img" && styleBaseImage));

  // 三开关分区各自的有效性：未勾选即视为满足；勾选则需模板齐全（图片还需提示词槽）。
  const anyEnabled = enableImage || enableVideo || enableAudio;
  const imageValid = !enableImage || (!!templateId && hasPrompt);
  const videoValid = !enableVideo
    || !!(videoTemplateId || image2videoTemplateId || firstlastTemplateId);
  const audioValid = !enableAudio || (!!audioTemplateId && hasVoiceText);
  // 多张候选底图＝用户还没做出选择：此时保存会把「哪张是底图」按旧值固化，必须删到一张才放行。
  const pendingCandidates = rows.some((r) => (r.baseCandidates?.length || 0) >= 2);
  const saveDisabled = !anyEnabled || !imageValid || !videoValid || !audioValid || pendingCandidates;

  // 预设的唯一组装口径：「保存」与「一键定妆」都读它。定妆用的 LoRA 栈／底图／负面词必须与
  // 自动插画运行时逐字一致，否则一键出来的底图和剧情成稿不是同一套配置（两处组装必然漂移）。
  const buildPreset = (rowsForSave: CharRow[] = rowsRef.current): MediaInsertPreset => {
    const characterLoras: Record<string, CharacterLoraBinding> = {};
    for (const r of rowsForSave) {
      const name = r.name.trim();
      if (!name) continue;
      characterLoras[name] = {
        loraName: loraMode === "none" ? undefined : r.loraName || undefined,
        loraWeight: loraMode !== "none" && r.loraName ? r.loraWeight ?? 0.8 : undefined,
        baseImage: r.baseImage || undefined,
      };
    }
    const characterVoices: Record<string, CharacterVoiceBinding> = {};
    for (const r of voiceRows) {
      const name = r.name.trim();
      if (!name || !r.voiceRef) continue;  // 音轨必须已选文件才算有效绑定
      characterVoices[name] = { voiceRef: r.voiceRef };
    }
    // 2026-10-25：custom 模版的内置协议族（模版被删时后端按它降级，悬空引用防护）
    return {
      enableImage, enableVideo, enableAudio,
      // 图片分区（未勾选则不保留，运行时按 enable 位分发）
      templateId: enableImage ? templateId : "",
      imageTemplates: enableImage ? {
        text2img: templateId || undefined,
        img2img: img2imgTemplateId || undefined,
        inpaint: inpaintTemplateId || undefined,
        reference: referenceTemplateId || undefined,
      } : undefined,
      loraMode: enableImage ? loraMode : undefined,
      appearanceSource: enableImage ? appearanceSource : undefined,
      imageMode: enableImage ? imageMode : undefined,
      viewpoint: enableImage && viewpoint ? viewpoint : undefined,
      batchImageCount: enableImage && batchImageCount > 1 ? batchImageCount : undefined,
      promptProfile: enableImage ? promptProfile : undefined,
      promptProfileKind: enableImage && selectedCustomKind ? selectedCustomKind : undefined,
      qualityPrompt: enableImage && animaFamily ? qualityPrompt.trim() : undefined,
      negativePrompt: enableImage && animaFamily ? negativePrompt.trim() : undefined,
      latentLongEdge: enableImage ? latentLongEdge : undefined,
      characterLoras: enableImage && Object.keys(characterLoras).length ? characterLoras : undefined,
      styleLora: enableImage && loraMode !== "none" ? styleLora || undefined : undefined,
      styleLoraWeight: enableImage && loraMode !== "none" && styleLora ? styleLoraWeight : undefined,
      styleBaseImage: enableImage ? styleBaseImage || undefined : undefined,
      // 视频分区
      firstlast: enableImage ? firstlast : undefined,
      // 全局模式默认分镜序列；关闭时保留旧整楼 firstlast 长片。
      shotSequence: enableImage && firstlast ? shotSequence : undefined,
      videoMode: enableImage && firstlast ? "firstlast" : undefined,  // 新预设只存 firstlast 选项
      videoTemplateId: enableVideo ? videoTemplateId : undefined,
      videoTemplates: enableVideo ? {
        image2video: image2videoTemplateId || undefined,
        firstlast: firstlastTemplateId || undefined,
      } : undefined,
      smartVideo: enableVideo ? smartVideo : undefined,
      // 2026-10-25 视频提示词模版（按模式派发；空=内置默认骨架）
      videoPromptTemplateId: enableVideo && videoPromptTemplateId ? videoPromptTemplateId : undefined,
      // 音频分区
      audioTemplateId: enableAudio ? audioTemplateId : undefined,
      characterVoices: enableAudio && Object.keys(characterVoices).length ? characterVoices : undefined,
    };
  };

  const save = () => onSave(buildPreset());

  // 一键定妆的可用性判据（唯一真源在 lib/portraitBaseImage）：取第一个已填名字的行代表整组——
  // 行内差异只有「角色名」，而按钮本就只给填了名字的行，所以这里拿到的是模板/链路配置类原因。
  const portraitDraft = buildPreset(rows);
  const namedRow = rows.find((r) => r.name.trim());
  const portraitBlocked = !portraitRuntime || !namedRow
    ? ""
    : portraitGateReason({
      name: namedRow.name, template: tpl, preset: portraitDraft,
      comfyUrl: portraitRuntime.comfyUrl, chat: portraitRuntime.chat,
    });

  // 立即落预设（不关面板）：即使图片分区当前收起（buildPreset 会不带 characterLoras），
  // 这条底图绑定也要保住——否则「生成成功但预设里没有」会再次重演（2026-11-07 埃吉尔实锤）。
  const persistPortrait = (nextRows: CharRow[], name: string, path: string) => {
    const persisted = buildPreset(nextRows);
    persisted.characterLoras = {
      ...(persisted.characterLoras || {}),
      [name]: { ...(persisted.characterLoras?.[name] || {}), baseImage: path },
    };
    onPersist?.(persisted);
  };

  const runPortrait = async (i: number) => {
    const runtime = portraitRuntime;
    const row = rowsRef.current[i];
    const name = (row?.name || "").trim();
    // 按钮此时不会渲染或被禁用（无 runtime / 无模板 / 判据拦住 / 候选未决），短路只为收窄类型。
    if (!runtime || !tpl || !name || (row.baseCandidates?.length || 0) >= 2) return;
    setPortraitBusy(i);
    setPortraitNote(null);
    try {
      // ComfyUI 没启动时不立刻判死：任务留在这条等待队列里，能自动拉起就自动拉起，
      // 就绪后继续提交；一键补齐的后续角色会复用同一次启动结果。
      const ready = await ensureComfyReady(runtime.comfyUrl, {
        timeoutMs: 10 * 60 * 1000,
        onState: (state) => {
          if (state.waiting) setPortraitNote({ row: i, ok: true, text: state.message });
        },
      });
      if (!ready) throw new Error("ComfyUI 启动超时，本次定妆未提交；启动后可再点一次。");
      setPortraitNote(null);
      const out = await generatePortraitBaseImage(
        {
          name, repoId, outputDir, template: tpl, templateName: tpl.name, preset: portraitDraft,
          promptProfile, comfyUrl: runtime.comfyUrl, chat: runtime.chat,
          presetDir: runtime.presetDir, presetName: runtime.presetName, userName: runtime.userName,
        },
        {
          genPrompt: genPortraitPrompt, listLoras, submit: submitWorkflow, poll: pollWorkflowResult, save: saveLocal,
          // 底图产物同样要进资产库（2026-11-08 用户口径）：与自动插画入库同一个端点与嵌入档，
          // image_url 用 local-view 根相对地址（后端据此生缩略图/视觉索引）。
          index: async (input) => {
            await indexGeneration(repoId || "home", {
              prompt: input.prompt,
              image_url: localViewUrl(input.imagePath),
              template_name: input.templateName,
              lora_names: input.loraNames,
            }, runtime.embed);
          },
        },
      );
      // 面板关掉也要能收回结果：先把「新底图已写入该角色行」的 preset 交给宿主暂存；
      // 面板还开着时宿主会忽略它，继续走下面的即时落盘/候选逻辑。
      const stagedRows = rowsRef.current.map((r, idx) => (
        idx === i ? { ...r, baseImage: out.path, baseCandidates: undefined } : r
      ));
      onPortraitReady?.(name, buildPreset(stagedRows), out.path);
      const indexedNote = out.indexed ? "产物已入资产库。" : "资产库入库失败（图仍已落盘并写入底图）。";
      const existing = row.baseImage;
      if (existing && existing !== out.path) {
        // 已有底图 ⇒ 两张候选并列，等用户删到一张才定稿：此刻不覆盖 baseImage、不落预设，
        // 否则「没选就固化」等于把新图静默顶掉旧图（用户要的是先看再删）。
        const nextRows = rowsRef.current.map((r, idx) => (
          idx === i ? { ...r, baseCandidates: [existing, out.path] } : r
        ));
        commitRows(nextRows);
        setPortraitNote({
          row: i, ok: true,
          text: `已生成第二张候选底图（外貌来源：${portraitAppearanceOriginText(out.appearanceOrigin)}；`
            + `动作：${out.pose || "默认"}）。${indexedNote}`
            + "点图可看大图，两张候选删到只剩一张才能保存。",
        });
      } else {
        const nextRows = rowsRef.current.map((r, idx) => (
          idx === i ? { ...r, baseImage: out.path, baseCandidates: undefined } : r
        ));
        commitRows(nextRows);
        // 定妆是分钟级产物，成功后立刻落预设：不依赖用户再点一次「保存」。
        // 即使图片分区当前被收起（buildPreset 会不带 characterLoras），这条绑定也要保住。
        persistPortrait(nextRows, name, out.path);
        setPortraitNote({
          row: i, ok: true,
          text: `底图已生成，写入 ${PORTRAIT_BASE_IMAGE_SUBDIR}/（外貌来源：${portraitAppearanceOriginText(out.appearanceOrigin)}；`
            + `动作：${out.pose || "默认"}，再点一次换一个动作）；${indexedNote}`
            + (onPersist ? "已自动写入本作品预设。" : "点「保存」才会写进本作品预设。"),
        });
      }
    } catch (error) {
      setPortraitNote({
        row: i, ok: false,
        text: error instanceof Error ? error.message : "定妆失败，底图未写入。",
      });
    } finally {
      setPortraitBusy(-1);
    }
  };

  // 删图：多张候选里删到一张＝用户已做出选择，立即定为底图并落预设（关闭面板也不丢这次决定）；
  // 删除单张底图则只改内存，仍由「保存」决定是否写回预设。
  const removeBase = (i: number, path: string) => {
    const row = rowsRef.current[i];
    if (!row) return;
    if (!row.baseCandidates?.length) {
      setRow(i, { baseImage: undefined });
      return;
    }
    const remaining = row.baseCandidates.filter((item) => item !== path);
    if (remaining.length >= 2) {
      setRow(i, { baseCandidates: remaining });
      return;
    }
    const kept = remaining[0];
    const nextRows = rowsRef.current.map((r, idx) => (
      idx === i ? { ...r, baseImage: kept, baseCandidates: undefined } : r
    ));
    commitRows(nextRows);
    const name = row.name.trim();
    if (kept && name) persistPortrait(nextRows, name, kept);
    setPortraitNote(kept ? { row: i, ok: true, text: "已保留这张底图并写入本作品预设。" } : null);
  };

  // 第 94 刀：首尾帧视频＝参考生成（图片1/2 首尾帧，图片3、4、5… 角色参考图）。
  // 参考图来源就是上面每行的「底图」；保存前先把缺图与模板能力这两类风险摆出来。
  // 2026-11-12：按能力槽解析实际模板（首尾帧槽 → 图生视频槽 → legacy）。
  const effectiveVideoTemplateId = (firstlast ? firstlastTemplateId : image2videoTemplateId)
    || videoTemplateId;
  const videoTpl = templates.find((item) => item.id === effectiveVideoTemplateId);
  const videoHasRefSlot = !!videoTpl?.exposed.some((field) => {
    const binding = workflowFieldBinding(field);
    return binding === SEMANTIC_REFERENCE_IMAGE || binding === SEMANTIC_REFERENCE_IMAGE_FILES
      || /^reference_image_\d+$/.test(binding);
  });
  const missingPortraitRows = rows
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => !!row.name.trim() && !row.baseImage);
  const runMissingPortraits = async () => {
    if (portraitBatch || portraitBusy >= 0 || portraitBlocked) return;
    setPortraitBatch(true);
    try {
      // 串行：定妆链共用 ComfyUI 队列，并发只会互相挤占，失败时也分不清是谁的产物。
      for (const { index } of missingPortraitRows) {
        await runPortrait(index);
      }
    } finally {
      setPortraitBatch(false);
    }
  };

  return (
    <>
    <div className="modal-mask">
      <div className="modal" style={{ width: 560, maxWidth: "94vw", maxHeight: "86vh", overflowY: "auto" }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 10 }}>
          <h3 style={{ margin: 0 }}>多元数据插入</h3>
          <button
            type="button"
            className="btn"
            title="提示词设置：控制给 LLM 看的提示词"
            aria-label="提示词设置"
            onClick={() => setPromptSettingsOpen(true)}
          >
            <Settings size={15} />
          </button>
        </div>
        <p style={{ color: "#666", marginTop: 0, fontSize: 13 }}>
          勾选要随剧情自动生成的内容类型（可多选）。保存后会自动开启「剧情自动生成」，剧情高潮点按勾选类型分别生成对应内容。
        </p>
        <div className="lora-mode-switch" role="group" aria-label="生成内容类型" style={{ marginBottom: 14 }}>
          {([
            ["image", "生成图片"], ["video", "生成视频"], ["audio", "生成音频"],
          ] as const).map(([key, label]) => {
            const on = key === "image" ? enableImage : key === "video" ? enableVideo : enableAudio;
            const toggle = key === "image" ? () => setEnableImage(!enableImage)
              : key === "video" ? () => setEnableVideo(!enableVideo)
              : () => setEnableAudio(!enableAudio);
            return (
              <button
                key={key}
                type="button"
                className={`btn${on ? " primary" : ""}`}
                aria-pressed={on}
                onClick={toggle}
              >{label}</button>
            );
          })}
        </div>
        {!anyEnabled && (
          <p style={{ color: "#c0392b", fontSize: 12, marginTop: -8 }}>至少勾选一项生成内容。</p>
        )}

        {enableImage && (<>
        <hr style={{ border: "none", borderTop: "1px solid #eee", margin: "16px 0" }} />
        <strong style={{ fontSize: 14, display: "block", marginBottom: 10 }}>生成图片</strong>
        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>角色外貌来源</span>
          <select value={appearanceSource}
            onChange={(event) => {
              const next = event.target.value as "worldbook" | "character_card";
              setAppearanceSource(next);
              // 角色卡模式不读预设里的底图绑定（也没有底图行可维护）⇒ 图生图不可用。
              if (next === "character_card") setImageMode("text2img");
            }}
            style={{ width: "100%" }}>
            <option value="worldbook">条目模式</option>
            <option value="character_card">角色卡模式</option>
          </select>
        </label>
        <p className="bind-hint" style={{ marginTop: -8, marginBottom: 12 }}>
          {appearanceSource === "worldbook"
            ? "从当前小仓库世界书中命中的角色视觉条目读取外貌。"
            : "从本作品绑定角色卡的描述读取外貌，适合纯机制世界书。"}
        </p>
        <div style={{ marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>画面人称</span>
          <div className="lora-mode-switch" role="group" aria-label="画面人称">
            {([
              ["", "自动"], ["first_person", "第一人称"], ["third_person", "第三人称"],
            ] as const).map(([value, label]) => (
              <button
                key={value || "auto"}
                type="button"
                className={`btn${viewpoint === value ? " primary" : ""}`}
                aria-pressed={viewpoint === value}
                onClick={() => setViewpoint(value)}
              >{label}</button>
            ))}
          </div>
          <p className="bind-hint" style={{ margin: "5px 0 0" }}>
            {viewpoint === "" && "自动：按判据锚点段判人称；判不出时按第一人称处理。"}
            {viewpoint === "first_person" && "第一人称：叙事者「我」不占画面（身体局部只在原文写明时才入画）。"}
            {viewpoint === "third_person" && "第三人称：主人公算一个主体，按世界书条目取主人公性别入画。"}
          </p>
        </div>
        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>批量生图数目</span>
          <input
            type="number"
            min={1}
            step={1}
            value={batchImageCount}
            onChange={(event) => {
              const parsed = Number(event.target.value);
              setBatchImageCount(
                Number.isFinite(parsed) && parsed >= 1 ? Math.floor(parsed) : 1,
              );
            }}
            style={{ width: "100%" }}
          />
          <p className="bind-hint" style={{ margin: "5px 0 0" }}>
            高潮点／首尾帧两种模式都生效，最小 1、无上限。达到或超过高潮点数 ⇒ 全部出图；
            少于 ⇒ 按判据打分从高到低取前几处，各插到自己的正文位置。
            高潮点：每张各配一支动作延伸视频。首尾帧：每张与前一张接成一段「前图为首帧、
            本图为尾帧」的衔接视频，落在本张图正下方。
          </p>
        </label>
        <div style={{ marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>生图方式</span>
          <div className="lora-mode-switch" role="group" aria-label="生图方式">
            <button
              type="button"
              className={`btn${imageMode === "text2img" ? " primary" : ""}`}
              aria-pressed={imageMode === "text2img"}
              onClick={() => setImageMode("text2img")}
              title="只凭提示词与 LoRA 出图；已绑定的角色/风格底图不参与"
            >文生图</button>
            <button
              type="button"
              className={`btn${imageMode === "img2img" ? " primary" : ""}`}
              aria-pressed={imageMode === "img2img"}
              disabled={appearanceSource === "character_card"}
              title={appearanceSource === "character_card"
                ? "角色卡模式不读取底图绑定；切到条目模式才能图生图"
                : "以角色底图（在场角色都无则风格底图）为参考出图"}
              onClick={() => setImageMode("img2img")}
            >图生图</button>
          </div>
          <p className="bind-hint" style={{ margin: "5px 0 0" }}>
            {imageMode === "text2img"
              ? "只凭提示词与 LoRA 出图：已绑定的角色/风格底图不参与（云端模型走文生图接口）。"
              : "以角色底图（在场角色都无则风格底图）为参考：ComfyUI 注入模板「角色底图」槽位，云端模型走图生图接口。"}
            {appearanceSource === "character_card" && " 角色卡模式不读取底图绑定，图生图不可用。"}
          </p>
          {imageMode === "img2img" && !hasAnyBaseImage && (
            <p style={{ color: "#c98a1a", fontSize: 12, margin: "5px 0 0" }}>
              已选图生图，但还没有任何角色底图或风格底图：出图仍会退化成文生图。请先在下方选择底图或一键定妆。
            </p>
          )}
        </div>
        <div style={{ marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>LoRA 模式</span>
          <div className="lora-mode-switch" role="group" aria-label="LoRA 模式">
            {([
              ["none", "无 LoRA"], ["single", "单 LoRA"], ["multi", "多 LoRA"],
            ] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={`btn${loraMode === value ? " primary" : ""}`}
                aria-pressed={loraMode === value}
                onClick={() => setLoraMode(value)}
              >{label}</button>
            ))}
          </div>
          <p className="bind-hint" style={{ margin: "5px 0 0" }}>
            {loraMode === "none" && "只使用角色底图，不加载角色或风格 LoRA。"}
            {loraMode === "single" && "串联高潮画面中已绑定的角色 LoRA；均未命中时才回退风格 LoRA。"}
            {loraMode === "multi" && "固定加载默认风格 LoRA，并叠加全部在场角色 LoRA。"}
          </p>
        </div>
        <div style={{ marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>生图时点</span>
          <div
            className="lora-mode-switch"
            role="group"
            aria-label="生图时点"
            style={{ gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}
          >
            <button
              type="button"
              className={`btn${firstlast ? " primary" : ""}`}
              aria-pressed={firstlast}
              onClick={() => setFirstlast(true)}
              title="图片按剧情全局关键帧生成；视频为全程剧情影片，含本段全部对白"
            >全局模式</button>
            <button
              type="button"
              className={`btn${!firstlast ? " primary" : ""}`}
              aria-pressed={!firstlast}
              onClick={() => setFirstlast(false)}
              title="单张高潮点定格图；视频为高潮点动作代入，高潮定格对白已说完、不带台词"
            >高潮点模式</button>
          </div>
          <p className="bind-hint" style={{ margin: "5px 0 0" }}>
            {firstlast
              ? "全局模式：按剧情分镜拆成多支视频，硬切/运镜分别生成后按顺序拼接成片。"
              : "高潮点模式：高潮点单图定格，视频模式自动为「高潮点动作代入」（不带台词）。"}
          </p>
          {firstlast && (
            <label className="bind-hint" style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6 }}>
              <input
                type="checkbox"
                checked={shotSequence}
                onChange={(event) => setShotSequence(event.target.checked)}
              />
              分镜序列（多镜头分别出片后拼接；关闭=旧整楼首尾帧长片，稳定性回退）
            </label>
          )}
        </div>
        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>生图提示词模式</span>
          <select
            value={promptProfile}
            onChange={(event) => setPromptProfile(normalizePromptProfile(event.target.value))}
            style={{ width: "100%" }}
          >
            <optgroup label="内置默认">
              {PROMPT_PROFILE_OPTIONS.map((option) => (
                <option key={option.id} value={option.id}>{option.label}</option>
              ))}
            </optgroup>
            {promptTemplateItems.filter((t) => !t.kind.startsWith("video_")).length > 0 && (
              <optgroup label="我的模版（提示词模版工作台）">
                {promptTemplateItems.filter((t) => !t.kind.startsWith("video_")).map((t) => (
                  <option key={t.id} value={`custom:${t.id}`}>★ {t.name || "未命名模版"}</option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
        <p className="bind-hint" style={{ marginTop: -8, marginBottom: 12 }}>
          内置四种输出结构（Anima 两行 / 自然语言 / Niji 四段 / MOSS caption）；「我的模版」在提示词模版工作台里克隆内置后自定义。模版被删时自动回退对应内置结构。
        </p>
        {(promptProfile === "anima_tags" || selectedCustomKind === "anima_tags") && (
          <>
            <label style={{ display: "block", marginBottom: 12 }}>
              <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>固定质量提示词</span>
              <textarea
                value={qualityPrompt}
                onChange={(event) => setQualityPrompt(event.target.value)}
                rows={5}
                style={{ width: "100%", minHeight: 110, resize: "vertical" }}
              />
            </label>
            <label style={{ display: "block", marginBottom: 12 }}>
              <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>固定负面提示词</span>
              <textarea
                value={negativePrompt}
                onChange={(event) => setNegativePrompt(event.target.value)}
                rows={6}
                style={{ width: "100%", minHeight: 130, resize: "vertical" }}
              />
            </label>
          </>
        )}
        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>
            文生图工作流模板（默认）
          </span>
          <select value={templateId} onChange={(e) => setTemplateId(e.target.value)} style={{ width: "100%" }}>
            <option value="">不启用（关闭高潮点自动出图）</option>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </label>
        {templateId && !hasPrompt && (
          <p style={{ color: "#c0392b", fontSize: 12, marginTop: -6 }}>
            该模板未标注「提示词」字段，无法注入剧情提示词。请先到「工作流模板」编辑并标注。
          </p>
        )}
        {templateId && animaFamily && negativePrompt && !hasNegativePrompt && (
          <p style={{ color: "#c98a1a", fontSize: 12, marginTop: -6 }}>
            该模板未标注有效的「负面提示词」字段，固定负面提示词不会参与采样。
          </p>
        )}
        <div style={{ display: "grid", gap: 10, marginBottom: 12 }}>
          <label style={{ display: "block" }}>
            <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>
              图生图模板（建立镜/底图参考，可选）
            </span>
            <select value={img2imgTemplateId}
              onChange={(e) => setImg2imgTemplateId(e.target.value)} style={{ width: "100%" }}>
              <option value="">不配置（逐镜回落文生图）</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
            {img2imgTemplateId && !img2imgReady && (
              <p style={{ color: "#c98a1a", fontSize: 12, marginTop: 2 }}>
                该模板没有 base_image 槽，图生图会退化为文生图；请在模板编辑里标注底图字段。
              </p>
            )}
          </label>
          <label style={{ display: "block" }}>
            <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>
              局部重绘模板（保持门框/背景，可选）
            </span>
            <select value={inpaintTemplateId}
              onChange={(e) => setInpaintTemplateId(e.target.value)} style={{ width: "100%" }}>
              <option value="">不配置</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
            {inpaintTemplateId && !inpaintReady && (
              <p style={{ color: "#c98a1a", fontSize: 12, marginTop: 2 }}>
                该模板缺少 base_image + mask 槽，局部重绘无法保证保留原图。
              </p>
            )}
          </label>
          <label style={{ display: "block" }}>
            <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>
              参考生图模板（IPAdapter/参考图，可选）
            </span>
            <select value={referenceTemplateId}
              onChange={(e) => setReferenceTemplateId(e.target.value)} style={{ width: "100%" }}>
              <option value="">不配置</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
            {referenceTemplateId && !referenceReady && (
              <p style={{ color: "#c98a1a", fontSize: 12, marginTop: 2 }}>
                该模板没有 reference_image 槽，参考图不会生效。
              </p>
            )}
          </label>
        </div>
        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>Latent 最长边</span>
          <select
            value={latentLongEdge}
            onChange={(event) => setLatentLongEdge(Number(event.target.value) as 1024 | 2048 | 4096)}
            style={{ width: "100%" }}
          >
            <option value={1024}>1K（1024）</option>
            <option value={2048}>2K（2048）</option>
            <option value={4096}>4K（4096）</option>
          </select>
        </label>
        {templateId && (!hasLatentWidth || !hasLatentHeight) && (
          <p style={{ color: "#c98a1a", fontSize: 12, marginTop: -6 }}>
            该模板未同时标注「Latent 宽度」和「Latent 高度」，所选尺寸不会注入工作流。
          </p>
        )}

        {appearanceSource === "worldbook" && (<>
          <hr style={{ border: "none", borderTop: "1px solid #eee", margin: "16px 0" }} />
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <strong style={{ fontSize: 13 }}>按角色配置（LoRA + 底图）</strong>
            <span style={{ display: "flex", gap: 6 }}>
              {rosterNames.length > 0 && (
                <button
                  className="btn"
                  onClick={addMissingRosterRows}
                  title="把世界书名册里还没配置的角色补进来；不覆盖已填内容"
                >
                  从名册补全
                </button>
              )}
              <button className="btn" onClick={addRow}>+ 添加角色</button>
            </span>
          </div>
        {loraMode !== "none" && templateId && !hasLoraSlot && (
          <p style={{ color: "#c0392b", fontSize: 12, marginTop: -2 }}>该模板未标注 LoRA 字段，角色 LoRA 选了也不生效。</p>
        )}
        {imageMode === "img2img" && templateId && !img2imgReady && (
          <p style={{ color: "#c0392b", fontSize: 12, marginTop: -2 }}>已选图生图，但图生图模板没有「角色底图」槽位：ComfyUI 出图不会用到底图（会按文生图提交）。请在上方配置图生图模板或改选文生图。</p>
        )}
        {loraMode !== "none" && loraLoadState === "loading" && (
          <p style={{ color: "#777", fontSize: 12, marginTop: -2 }}>正在读取 LoRA 目录…</p>
        )}
        {loraMode !== "none" && loraLoadState === "error" && (
          <p style={{ color: "#c98a1a", fontSize: 12, marginTop: -2 }}>
            LoRA 列表读取失败，当前绑定仍会保留：{loraLoadError}
            <button type="button" className="btn" style={{ marginLeft: 8 }}
              onClick={() => setLoraReloadKey((value) => value + 1)}>重试</button>
          </p>
        )}
        {loraMode !== "none" && loraLoadState === "ready" && loras.length === 0 && (
          <p style={{ color: "#c98a1a", fontSize: 12, marginTop: -2 }}>
            在 {modelsDir}/loras 下没扫到模型文件，请确认设置里的 ComfyUI 路径。
          </p>
        )}
        {loraMode !== "none" && loraLoadState === "idle" && (
          <p style={{ color: "#c98a1a", fontSize: 12, marginTop: -2 }}>
            未设置 ComfyUI 路径，无法列出 LoRA。请先到设置里填 ComfyUI 路径或 models 目录。
          </p>
        )}
        {portraitRuntime && namedRow && portraitBlocked && (
          <p style={{ color: "#c98a1a", fontSize: 12, marginTop: -2 }}>{portraitBlocked}</p>
        )}
        {rows.map((r, i) => (
          <div
            key={i}
            data-character-row={r.name.trim()}
            className="media-insert-row"
            style={{ border: "1px solid #eee", borderRadius: 8, padding: 10, marginBottom: 8 }}
          >
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
              <input
                placeholder="角色名（与剧情中一致）" value={r.name}
                onChange={(e) => setRow(i, { name: e.target.value })}
                style={{ flex: 1 }}
              />
              <button className="btn" onClick={() => delRow(i)} title="删除该角色">✕</button>
            </div>
            {loraMode !== "none" && <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
              <select value={r.loraName || ""} onChange={(e) => {
                const loraName = e.target.value;
                setRow(i, {
                  loraName: loraName || undefined,
                  loraWeight: loraName ? suggestedWeightForLora(loras, loraName) : undefined,
                });
              }} style={{ flex: 1 }}>
                <option value="">无角色 LoRA（用风格 LoRA + 底图）</option>
                {r.loraName && !loras.some((lora) => lora.lora_name === r.loraName) && (
                  <option value={r.loraName}>{r.loraName}（当前绑定，目录列表未包含）</option>
                )}
                {loras.map((l) => (
                  <option key={l.lora_name} value={l.lora_name}>
                    {l.lora_name}{loraStatusSuffix(l, true)}
                  </option>
                ))}
              </select>
              {r.loraName && (
                <input
                  type="number" step={0.05} min={0} max={2} value={r.loraWeight ?? 0.8}
                  onChange={(e) => setRow(i, { loraWeight: Number(e.target.value) })}
                  style={{ width: 80 }} title="建议权重已自动填入，可手动调整"
                />
              )}
            </div>}
            <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
              {(r.baseCandidates?.length || 0) >= 2 ? (
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  {r.baseCandidates!.map((path) => (
                    <div key={path} style={{ position: "relative", width: 40, height: 40 }}>
                      <img
                        src={localViewUrl(path)} alt={`${r.name || "角色"}候选底图`}
                        title="点击查看大图"
                        onClick={() => openLightbox(localViewUrl(path))}
                        style={{ width: 40, height: 40, objectFit: "cover", borderRadius: 4, cursor: "zoom-in" }}
                      />
                      <button
                        type="button" aria-label="删掉这张候选底图"
                        title="删掉这张候选底图（剩一张后才能保存）"
                        style={BASE_DELETE_BTN_STYLE}
                        onClick={() => removeBase(i, path)}
                      >×</button>
                    </div>
                  ))}
                </div>
              ) : r.baseImage ? (
                <div style={{ position: "relative", width: 40, height: 40 }}>
                  <img
                    src={localViewUrl(r.baseImage)} alt={`${r.name || "角色"}底图`}
                    title="点击查看大图"
                    onClick={() => openLightbox(localViewUrl(r.baseImage!))}
                    style={{ width: 40, height: 40, objectFit: "cover", borderRadius: 4, cursor: "zoom-in" }}
                  />
                  <button
                    type="button" aria-label="删除这张底图" title="删除这张底图"
                    style={BASE_DELETE_BTN_STYLE}
                    onClick={() => removeBase(i, r.baseImage!)}
                  >×</button>
                </div>
              ) : <span style={{ color: "#999" }}>底图（可选，用于角色一致性）</span>}
              <label className="btn" style={{ cursor: "pointer" }}>
                {uploading === String(i) ? "上传中…" : (r.baseImage ? "更换底图" : "选择底图")}
                <input type="file" accept="image/*" hidden onChange={(e) => { pickBase(i, e.target.files?.[0]); e.target.value = ""; }} />
              </label>
              {portraitRuntime && r.name.trim() && (
                <button
                  type="button" className="btn"
                  disabled={portraitBusy >= 0 || !!portraitBlocked || (r.baseCandidates?.length || 0) >= 2}
                  title={portraitBlocked
                    || ((r.baseCandidates?.length || 0) >= 2
                      ? "先删掉候选底图中的一张，再重新定妆"
                      : (r.baseImage
                        ? "再生成一张候选底图（与现有底图并列，删掉一张才能保存）"
                        : "按本预设绑定的生图工作流＋角色条目外貌生成定妆底图"))}
                  onClick={() => { void runPortrait(i); }}
                >{portraitBusy === i ? "定妆中…" : (r.baseImage ? "重新定妆" : "一键定妆")}</button>
              )}
            </div>
            {(r.baseCandidates?.length || 0) >= 2 && (
              <p style={{ color: "#c0392b", fontSize: 12, marginTop: 4 }}>
                两张候选底图：点图可看大图，删到只剩一张才能保存。
              </p>
            )}
            {portraitNote && portraitNote.row === i && (
              <p style={{ color: portraitNote.ok ? "#2e7d32" : "#c0392b", fontSize: 12, marginTop: 4 }}>
                {portraitNote.text}
              </p>
            )}
          </div>
        ))}
        </>)}

        {loraMode !== "none" && <>
          <hr style={{ border: "none", borderTop: "1px solid #eee", margin: "16px 0" }} />
          <strong style={{ fontSize: 13, display: "block", marginBottom: 8 }}>
            {appearanceSource === "character_card"
              ? "全局风格 LoRA（可选）"
              : "兜底风格 LoRA"}
          </strong>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
          <select value={styleLora} onChange={(e) => {
            const loraName = e.target.value;
            setStyleLora(loraName);
            if (loraName) setStyleLoraWeight(suggestedWeightForLora(loras, loraName));
          }} style={{ flex: 1 }}>
            <option value="">无风格 LoRA</option>
            {styleLora && !loras.some((lora) => lora.lora_name === styleLora) && (
              <option value={styleLora}>{styleLora}（当前绑定，目录列表未包含）</option>
            )}
            {loras.map((l) => (
              <option key={l.lora_name} value={l.lora_name}>
                {l.lora_name}{loraStatusSuffix(l)}
              </option>
            ))}
          </select>
          {styleLora && (
            <input
              type="number" step={0.05} min={0} max={2} value={styleLoraWeight}
              onChange={(e) => setStyleLoraWeight(Number(e.target.value))}
              style={{ width: 80 }} title="建议权重已自动填入，可手动调整"
            />
          )}
          </div>
        </>}
        {appearanceSource === "worldbook" && <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, marginBottom: 8 }}>
          {styleBaseImage
            ? <img
                src={localViewUrl(styleBaseImage)} alt="风格底图"
                title="点击查看大图"
                onClick={() => openLightbox(localViewUrl(styleBaseImage))}
                style={{ width: 40, height: 40, objectFit: "cover", borderRadius: 4, cursor: "zoom-in" }}
              />
            : <span style={{ color: "#999" }}>风格底图（可选）</span>}
          <label className="btn" style={{ cursor: "pointer" }}>
            {uploading === "style" ? "上传中…" : (styleBaseImage ? "更换底图" : "选择底图")}
            <input type="file" accept="image/*" hidden onChange={(e) => { pickStyleBase(e.target.files?.[0]); e.target.value = ""; }} />
          </label>
          {styleBaseImage && <button className="btn" onClick={() => setStyleBaseImage("")}>清除</button>}
        </div>}
        {appearanceSource === "worldbook" && missingBase && (
          <p style={{ color: "#c0392b", fontSize: 12 }}>有角色既无 LoRA 也无底图，且无兜底风格——该角色出图将缺少一致性锚点。</p>
        )}
        </>)}

        {enableVideo && (<>
          <hr style={{ border: "none", borderTop: "1px solid #eee", margin: "16px 0" }} />
          <strong style={{ fontSize: 14, display: "block", marginBottom: 10 }}>生成视频</strong>
          <label style={{ display: "block", marginBottom: 12 }}>
            <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>
              通用视频工作流模板（默认）
            </span>
            <select value={videoTemplateId} onChange={(e) => setVideoTemplateId(e.target.value)} style={{ width: "100%" }}>
              <option value="">不启用（不生成视频）</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </label>
          <div style={{ display: "grid", gap: 10, marginBottom: 12 }}>
            <label style={{ display: "block" }}>
              <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>
                图生视频模板（首帧/角色参考，可选）
              </span>
              <select value={image2videoTemplateId}
                onChange={(e) => setImage2videoTemplateId(e.target.value)} style={{ width: "100%" }}>
                <option value="">不配置（回落通用视频模板）</option>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
            </label>
            <label style={{ display: "block" }}>
              <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>
                首尾帧视频模板（可选）
              </span>
              <select value={firstlastTemplateId}
                onChange={(e) => setFirstlastTemplateId(e.target.value)} style={{ width: "100%" }}>
                <option value="">不配置（回落通用视频模板）</option>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
            </label>
          </div>
          <p className="bind-hint" style={{ marginTop: -8, marginBottom: 12 }}>
            {firstlast
              ? "首尾帧视频按参考生成提交：图片1／2 是首尾帧，图片3、4、5… 是在场角色的参考底图（按角色出场顺序上传）。"
              : "高潮点视频用该角色的底图作首帧，锁定角色一致性。"}
          </p>
          {firstlast && effectiveVideoTemplateId && (
            <div style={{ marginTop: -6, marginBottom: 12 }}>
              {appearanceSource === "character_card" && (
                <p style={{ color: "#c0392b", fontSize: 12, margin: "0 0 4px" }}>
                  角色卡模式不读取角色底图，参考图为空 ⇒ 视频模型只能猜外貌。请切到「世界书/角色条目」模式并配置角色底图。
                </p>
              )}
              {!videoHasRefSlot && (
                <p style={{ color: "#c98a1a", fontSize: 12, margin: "0 0 4px" }}>
                  当前视频模板没有参考图槽（reference_image / reference_image_files），角色参考图无法上传，视频模型只能猜外貌。
                </p>
              )}
              {appearanceSource === "worldbook" && missingPortraitRows.length > 0 && (
                <p style={{ color: "#c98a1a", fontSize: 12, margin: "0 0 4px" }}>
                  缺失底图：{missingPortraitRows.map(({ row }) => row.name.trim()).join("、")}。
                  这些角色没有参考图，视频模型只能猜外貌。
                </p>
              )}
              {appearanceSource === "worldbook" && missingPortraitRows.length > 0 && portraitRuntime && (
                <button
                  type="button" className="btn"
                  disabled={portraitBusy >= 0 || portraitBatch || !!portraitBlocked}
                  title={portraitBlocked || "按各角色条目外貌 + 已绑定的角色/风格 LoRA 逐个生成定妆底图"}
                  onClick={() => { void runMissingPortraits(); }}
                >{portraitBatch ? "补齐中…" : `一键补齐缺失底图（${missingPortraitRows.length}）`}</button>
              )}
              {portraitBlocked && missingPortraitRows.length > 0 && (
                <p style={{ color: "#c98a1a", fontSize: 12, margin: "4px 0 0" }}>{portraitBlocked}</p>
              )}
            </div>
          )}
          <label style={{ display: "block", marginBottom: 12 }}>
            <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>视频提示词模式</span>
            <select
              value={videoPromptTemplateId}
              onChange={(e) => setVideoPromptTemplateId(e.target.value)}
              style={{ width: "100%" }}
            >
              <option value="">内置默认（五段式骨架）</option>
              {videoTemplatesForMode(promptTemplateBuiltins, promptTemplateItems,
                firstlast ? "firstlast" : "climax").map((option) => (
                <option key={option.id} value={option.id}>{option.name}</option>
              ))}
            </select>
          </label>
          <p className="bind-hint" style={{ marginTop: -8, marginBottom: 12 }}>
            定义视频提示词的区块标题/顺序、运镜文案、节奏名与默认时长（输出结构与分镜逻辑不可改）。
            {firstlast ? "全局模式含段桥转场：选「转场短桥段」模版即用于转场段。" : ""}
          </p>
          {videoTemplateId && (
            <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, fontSize: 13 }}>
              <input type="checkbox" checked={smartVideo} onChange={(e) => setSmartVideo(e.target.checked)} />
              智能模态：后端为这段画面编排出视频分镜时，先出图、图的正下方再挂一个视频槽（拿这张成图当首帧）；没编排的画面仍只出图
            </label>
          )}
        </>)}

        {enableAudio && (<>
          <hr style={{ border: "none", borderTop: "1px solid #eee", margin: "16px 0" }} />
          <strong style={{ fontSize: 14, display: "block", marginBottom: 10 }}>生成音频</strong>
          <label style={{ display: "block", marginBottom: 12 }}>
            <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>音频工作流模板（IndexTTS 系语音合成）</span>
            <select value={audioTemplateId} onChange={(e) => setAudioTemplateId(e.target.value)} style={{ width: "100%" }}>
              <option value="">不启用（不配音）</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </label>
          {audioTemplateId && !hasVoiceText && (
            <p style={{ color: "#c0392b", fontSize: 12, marginTop: -6 }}>
              该模板未标注「角色台词」字段，无法注入台词。请先到「工作流模板」编辑并标注 voice_text。
            </p>
          )}
          {audioTemplateId && !hasVoiceRef && (
            <p style={{ color: "#c98a1a", fontSize: 12, marginTop: -6 }}>
              该模板未标注「参考音轨」字段，音色克隆不会生效。请补充标注 voice_reference。
            </p>
          )}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <strong style={{ fontSize: 13 }}>按角色配置（参考音轨）</strong>
            <button className="btn" onClick={addVoiceRow}>+ 添加角色</button>
          </div>
          <p className="bind-hint" style={{ marginTop: -4, marginBottom: 8 }}>
            每个作品专属、每个角色准备一条参考音轨（音色）。台词按角色筛分后逐角色合成，旁白/叙述句不配音。
          </p>
          {voiceRows.map((r, i) => (
            <div key={i} style={{ border: "1px solid #eee", borderRadius: 8, padding: 10, marginBottom: 8 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
                <input
                  placeholder="角色名（与剧情中一致）" value={r.name}
                  onChange={(e) => setVoiceRow(i, { name: e.target.value })}
                  style={{ flex: 1 }}
                />
                <button className="btn" onClick={() => delVoiceRow(i)} title="删除该角色">✕</button>
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
                <span style={{ color: r.voiceRef ? "#333" : "#999", flex: 1, wordBreak: "break-all" }}>
                  {r.voiceRef ? r.voiceRef.split(/[\\/]/).pop() : "参考音轨（未选择）"}
                </span>
                <label className="btn" style={{ cursor: "pointer" }}>
                  {uploadingVoice === String(i) ? "上传中…" : (r.voiceRef ? "更换音轨" : "选择音轨")}
                  <input type="file" accept="audio/*" hidden onChange={(e) => { pickVoice(i, e.target.files?.[0]); e.target.value = ""; }} />
                </label>
                {r.voiceRef && <button className="btn" onClick={() => setVoiceRow(i, { voiceRef: undefined })}>清除</button>}
              </div>
            </div>
          ))}
        </>)}

        {/* 取消/保存吸附在滚动容器底部：滚轮在任何位置都能点到（2026-10-16 用户口径）；
            点遮罩不再关闭（复制粘贴友好），改用 ESC 关闭 */}
        <div
          className="modal-actions"
          style={{
            position: "sticky", bottom: 0, zIndex: 2,
            background: "var(--surface, #fff)",
            borderTop: "1px solid var(--border, rgba(128,128,128,0.25))",
            paddingTop: 8, marginTop: 8,
          }}
        >
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn primary" disabled={saveDisabled} onClick={save}
            title={pendingCandidates ? "有角色存在多张候选底图：删到一张才能保存" : undefined}
          >保存</button>
        </div>
      </div>
    </div>
    {promptSettingsOpen && (
      <IllustrationPromptModal onClose={() => setPromptSettingsOpen(false)} />
    )}
    </>
  );
}
