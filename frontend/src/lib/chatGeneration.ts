// 生成流程的纯判定/整形逻辑（无 React、无 I/O）：从 useChatSession 闭包里抽出，
// 让「图像门 / 快照瘦身 / 文本打分」这些真会咬人的分支可被单测。
// 依赖注入原则：涉及落盘的部分（persist）由调用方传入函数，本模块只管遍历与决策。
import type { ChatMessage, RegenerationSnapshot } from "../types/chat";
import { regenerationPrompt } from "./regeneration";
import type { Template } from "../api/workflows";
import type { RichContent } from "../components/RichInput";
export { prependLoraTriggers } from "./imagePromptProfiles";

interface LoraTriggerRecord {
  lora_name: string;
  triggers: string[];
  missing: boolean;
  suggested_prompt?: string;
}

const QUALITY_PROMPT_PATTERN = /(?:\b(?:best|high|amazing|masterpiece|masterwork|quality|aesthetic|absurdres|newest|score_\d+|contrast|detail(?:ed|s)?|resolution|anatomy|shading|focus|blurr?y|depth of field|rim light|lighting|chiaroscuro|coloring|sketch|style|realistic|material|texture|silk|satin|velvet|chiffon|lace|leather|metallic|glossy|matte|translucent|grain(?:y)?|[248]k|hd|uhd)\b|画质|高质量|杰作|光影|景深|虚化|画风|风格|细节|分辨率|色彩|上色|阴影|锐利|清晰|材质|纹理|丝绸|真丝|缎面|丝绒|雪纺|蕾丝|皮革|金属|哑光|高光|通透)/i;
const SCENE_CONTROL_PATTERN = /(?:\b(?:close-up|wide angle|camera|shot|composition|perspective|portrait|full body|upper body|cowboy shot|dutch angle|low angle|high angle|from above|from below)\b|构图|镜头|特写|近景|中景|远景|全身|半身|俯拍|仰拍)/i;
const CONTENT_PROMPT_PATTERN = /(?:\b(?:\d*(?:girl|boy)|woman|man|female|male|hair|eyes?|dress|skirt|shirt|pants|underwear|bra|panties|nude|naked|breasts?|nipples?|pussy|penis|standing|sitting|kneeling|lying|running|walking|holding|touching|kissing|sex|arms?|hands?|sword|forest|room|street|bedroom)\b|女孩|男孩|女人|男人|头发|眼睛|裙|衬衫|裤|内衣|裸体|乳房|乳头|阴部|阴茎|站立|坐着|跪|躺|奔跑|行走|手持|触摸|亲吻|性交|手臂|手部|剑|森林|房间|街道|卧室)/i;
const MEDIA_STYLE_PATTERN = /(?:\b(?:photo-?real(?:istic)?|realistic(?:\s+(?:skin|face|photo(?:graphy)?))?|live[ -]?action|anime|donghua|cartoon)\b|真人|写实|照片|摄影|二次元|动漫|卡通)/i;

export function splitPromptTags(value: string): string[] {
  const tags: string[] = [];
  let current = "";
  let depth = 0;
  for (const char of value.replace(/\r\n?/g, "\n")) {
    if (char === "(") depth += 1;
    else if (char === ")" && depth > 0) depth -= 1;
    if ((char === "," || char === ";" || char === "\n") && depth === 0) {
      if (current.trim()) tags.push(current.trim());
      current = "";
    } else {
      current += char;
    }
  }
  if (current.trim()) tags.push(current.trim());
  return tags;
}

function isArtistSignature(tag: string): boolean {
  const value = tag.trim().replace(/^\(+|\)+$/g, "");
  return /^@\[[^\]]+\]$/i.test(value)
    || /^(?:artist\s*:|by\s+)/i.test(value)
    || /^[a-z][\w]*(?:_\([^)]*\))?\s*:\s*\d+(?:\.\d+)?$/i.test(value);
}

function isQualityPromptTag(tag: string): boolean {
  if (SCENE_CONTROL_PATTERN.test(tag)) return false;
  if (MEDIA_STYLE_PATTERN.test(tag)) return false;
  if (isArtistSignature(tag)) return true;
  if (CONTENT_PROMPT_PATTERN.test(tag)) return false;
  const words = tag.trim().split(/\s+/).filter(Boolean);
  if (words.length > 8 || /[!?。！？]|[.!?](?:\s|$)/.test(tag)) return false;
  return QUALITY_PROMPT_PATTERN.test(tag);
}

function uniquePromptTags(tags: readonly string[]): string[] {
  const seen = new Set<string>();
  return tags.filter((tag) => {
    const key = tag.trim().toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function qualityPromptTagsFromSuggestion(
  suggestion: string, _triggers: readonly string[],
): string[] {
  return uniquePromptTags(splitPromptTags(suggestion).filter(
    (tag) => isQualityPromptTag(tag),
  ));
}

export interface PromptHistoryItem {
  role: "user" | "assistant";
  content: string;
}

/**
 * 异步瘦身只能回写它开始处理的同一版消息。
 * 期间若流事件或用户发送产生了新数组，旧结果不得覆盖新消息。
 */
export function acceptSlimmedMessages(
  current: readonly ChatMessage[], original: readonly ChatMessage[], slimmed: ChatMessage[],
): ChatMessage[] {
  return current === original ? slimmed : current as ChatMessage[];
}

export function canCommitSnapshot(
  current: readonly ChatMessage[], original: readonly ChatMessage[],
  activeThreadId: string, targetThreadId: string,
): boolean {
  return current === original && activeThreadId === targetThreadId;
}

export function promptHistory(msgs: readonly ChatMessage[]): PromptHistoryItem[] {
  return msgs.flatMap((message) => {
    const text = (message.text || "").trim() || (message.parts || [])
      .filter((part) => part.type === "text" && part.text?.trim())
      .map((part) => part.text!.trim())
      .join("\n");
    return text ? [{ role: message.role, content: text }] : [];
  });
}

export function userMessagePlainText(msg: ChatMessage): string {
  if (!msg.parts?.length) return msg.text || "";
  return msg.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text || "")
    .join("");
}

export function userMessageRichContent(msg: ChatMessage): RichContent {
  const text = userMessagePlainText(msg);
  const images = (msg.parts || [])
    .filter((part) => part.type === "image" && part.url)
    .map((part) => part.url!);
  if (msg.image && !images.includes(msg.image)) images.push(msg.image);
  const maskedPart = (msg.parts || []).find(
    (part) => part.type === "masked-image" && part.image && part.mask && part.url,
  );
  const maskedImage = maskedPart ? {
    image: maskedPart.image!,
    mask: maskedPart.mask!,
    preview: maskedPart.url!,
  } : undefined;
  const inputParts: RichContent["parts"] = [];
  for (const part of msg.parts || []) {
    if (part.type === "text") inputParts.push({ type: "text", text: part.text || "" });
    if (part.type === "image" && part.url) inputParts.push({ type: "image", url: part.url });
    if (part.type === "masked-image" && part.url && part.image && part.mask) {
      inputParts.push({
        type: "masked-image", url: part.url, image: part.image, mask: part.mask,
      });
    }
  }
  return {
    text,
    images,
    parts: inputParts.length ? inputParts : [
      ...images.map((url) => ({ type: "image" as const, url })),
      ...(text ? [{ type: "text" as const, text }] : []),
    ],
    ...(maskedImage ? { maskedImage } : {}),
  };
}

export function prepareConversationRegeneration(
  messages: readonly ChatMessage[], messageId: string,
): { history: ChatMessage[]; retained: ChatMessage[]; content: RichContent } | null {
  const index = messages.findIndex((message) => message.id === messageId);
  if (index < 0 || messages[index].role !== "user") return null;
  return {
    history: messages.slice(0, index),
    retained: messages.slice(0, index + 1),
    content: userMessageRichContent(messages[index]),
  };
}

export function triggersForSelectedLora(
  items: readonly LoraTriggerRecord[], selectedLoraName: string,
): string[] {
  if (!selectedLoraName) return [];
  const selected = items.find(
    (item) => item.lora_name === selectedLoraName && !item.missing,
  );
  return selected ? [...selected.triggers] : [];
}

export function promptAdditionsForSelectedLora(
  items: readonly LoraTriggerRecord[], selectedLoraName: string,
): string[] {
  if (!selectedLoraName) return [];
  const selected = items.find(
    (item) => item.lora_name === selectedLoraName && !item.missing,
  );
  if (!selected) return [];
  const suggestion = selected.suggested_prompt?.trim() || "";
  if (!suggestion) return [...selected.triggers];
  const qualityTags = qualityPromptTagsFromSuggestion(suggestion, selected.triggers);
  return uniquePromptTags([...selected.triggers, ...qualityTags]);
}

export function resolveLoraPromptMetadata(
  items: readonly LoraTriggerRecord[], selectedLoraName: string,
): { found: boolean; additions: string[] } {
  if (!selectedLoraName) return { found: false, additions: [] };
  const found = items.some(
    (item) => item.lora_name === selectedLoraName && !item.missing,
  );
  return {
    found,
    additions: found ? promptAdditionsForSelectedLora(items, selectedLoraName) : [],
  };
}

export function resolveGenerationPrompt(
  pendingPrompt: string | undefined,
  regeneration: Pick<RegenerationSnapshot, "kind" | "prompt"> | undefined,
  resultText: string,
): string {
  return pendingPrompt?.trim()
    || regenerationPrompt(regeneration as RegenerationSnapshot | undefined).trim()
    || resultText;
}

// ===== 图像门（image gate）=====
// 判断一个工作流模板是否声明了图像输入口，以及抓取到的画布里该输入口是否已填图。
// 用于 /s 出图前拦截「图生图工作流但没给图」。

// 模板是否定义了图像输入口
export function needsImageInput(tpl: Template): boolean {
  return !!tpl.image_node_id || (tpl.exposed || []).some((f) => f.control === "image");
}

// 值是否算「已填」：非 null/undefined/空串、非空数组
function nonEmpty(v: unknown): boolean {
  return v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && v.length === 0);
}

// capturedGraph 里图像输入节点是否已有图值。
// 同时兼容两种图结构：litegraph { nodes:[{id,widgets_values}] } 与 API { id:{inputs} }。
// 拿不准（两种结构都不匹配）就放行，不误拦。
export function hasImageProvided(graph: unknown, tpl: Template): boolean {
  const g = graph as any;
  const ids = new Set<string>();
  if (tpl.image_node_id) ids.add(String(tpl.image_node_id));
  for (const f of tpl.exposed || []) if (f.control === "image") ids.add(String(f.node_id));
  if (ids.size === 0) return true;
  if (g && Array.isArray(g.nodes)) {
    for (const n of g.nodes) {
      if (!ids.has(String(n.id))) continue;
      const wv = n.widgets_values;
      if (Array.isArray(wv) ? wv.some(nonEmpty) : nonEmpty(wv)) return true;
    }
    return false;
  }
  if (g && typeof g === "object") {
    for (const id of ids) {
      const node = g[id];
      const inp = node?.inputs;
      if (inp && Object.values(inp).some(nonEmpty)) return true;
    }
    return false;
  }
  return true; // 拿不准就放行
}

// ===== 文本打分：从生成结果的多段文本里挑最优 =====
// 过滤掉「有效字符占比过低」的噪声段（如纯符号/乱码），再按长度取最长的一段。
// 有效字符 = 字母数字 + 中文。占比阈值 0.3。
export function pickBestText(texts: readonly string[] | undefined): string {
  const cleaned = (texts || [])
    .map((t) => t.trim())
    .filter((t) => t.length > 0)
    .filter((t) => (t.replace(/[^\w一-龥]/g, "").length / t.length) > 0.3);
  return cleaned.sort((a, b) => b.length - a.length)[0] || "";
}

// ===== 快照瘦身：把消息流里的 data:URI 大图落盘转小地址 =====
// 遍历与决策是纯的；实际落盘由调用方注入 persist（data:URI → 小地址，失败返回原值）。
export async function slimSnapshot(
  msgs: readonly ChatMessage[],
  persist: (src: string) => Promise<string>,
): Promise<ChatMessage[]> {
  const out: ChatMessage[] = [];
  for (const m of msgs) {
    let nm = m;
    // 1) 用户消息 parts 里的上传图 → 落盘转小地址
    if (nm.parts?.some((p) => p.type === "image" && p.url?.startsWith("data:"))) {
      const parts = await Promise.all(
        nm.parts.map(async (p) =>
          p.type === "image" && p.url ? { ...p, url: await persist(p.url) } : p,
        ),
      );
      nm = { ...nm, parts };
    }
    if (nm.parts?.some((part) => part.type === "masked-image")) {
      const parts = await Promise.all(nm.parts.map(async (part) => {
        if (part.type !== "masked-image") return part;
        const [url, image, mask] = await Promise.all([
          part.url ? persist(part.url) : Promise.resolve(part.url),
          part.image ? persist(part.image) : Promise.resolve(part.image),
          part.mask ? persist(part.mask) : Promise.resolve(part.mask),
        ]);
        return { ...part, url, image, mask };
      }));
      nm = { ...nm, parts };
    }
    // 2) portsPlan.images
    if (nm.portsPlan?.images?.length) {
      const pp = nm.portsPlan;
      if (pp.status === "applied" || pp.status === "ignored") {
        nm = { ...nm, portsPlan: { ...pp, images: [] } };  // 已执行：副本无用，清空
      } else {
        const imgs = await Promise.all(pp.images.map((s) => persist(s)));  // 待执行：落盘保留
        nm = { ...nm, portsPlan: { ...pp, images: imgs } };
      }
    }
    if (nm.regeneration?.kind === "ai-image" && nm.regeneration.images.length) {
      const images = await Promise.all(nm.regeneration.images.map((src) => persist(src)));
      nm = { ...nm, regeneration: { ...nm.regeneration, images } };
    }
    if (nm.regeneration?.kind === "ai-image" && nm.regeneration.imageMask) {
      const [image, mask] = await Promise.all([
        persist(nm.regeneration.imageMask.image),
        persist(nm.regeneration.imageMask.mask),
      ]);
      nm = { ...nm, regeneration: { ...nm.regeneration, imageMask: { image, mask } } };
    }
    out.push(nm);
  }
  return out;
}
