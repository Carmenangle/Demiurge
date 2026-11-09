import {
  VOICE_EMOTION_KEYS,
  type ControlType, type ExposedField, type ParsedField, type ParsedNode,
} from "../api/workflows";

// 文本载体节点 binding 的标题声明规则（与后端 template_store 同口径，改动需双端同步）。
const TEXT_CARRIER_TYPES = ["string", "primitive", "text"];
const TEXT_CARRIER_FIELDS = new Set(["string", "text", "value", "prompt", "positive"]);
const NEG_PROMPT_TITLE_RE = /negative|neg[\s_-]*prompt|负面|负向|反向/i;
const POS_PROMPT_TITLE_RE = /positive|\bpos[\s_-]*prompt\b|正面|正向/i;
// 第 94 刀：参考视频节点的角色参考图槽（图片3、4、5…）。模板编辑器没有手选 binding 的入口，
// 所以可自动识别的声明只有两种：字段名（ref_image_files 单控件）与节点标题（「参考图N」编号）。
// ⚠ 「参考底图」是图生图输入，不匹配「参考图」，不能被这条规则抢走。
const REF_IMAGE_TITLE_RE = /(?:参考图|角色参考|reference[\s_-]*image|\bref[\s_-]*image\b)\s*[_-]?(\d+)?/i;
// 视频生成节点上显式声明输出像素尺寸的节点类型（与后端 template_store._VIDEO_SIZE_NODE_TYPES 同口径）。
// 2026-11-01：H3 首帧链改用 MiniMaxH3ImageToVideo（ref2va 无帧锁定入口），双端都要认。
const VIDEO_SIZE_NODE_TYPES = ["minimaxh3referencetovideo", "minimaxh3imagetovideo"];

export function inferWorkflowFieldControl(field: ParsedField): ControlType {
  const name = field.name.toLowerCase();
  if (name.includes("image") || name.includes("mask")) return "image";
  if (name === "seed" || name === "noise_seed") return "seed";
  if (typeof field.value === "boolean") return "boolean";
  if (typeof field.value === "number") return "number";
  if (name.includes("text") || name.includes("prompt")) return "textarea";
  return "text";
}

export function inferWorkflowFieldBinding(node: ParsedNode, field: ParsedField): string {
  const name = field.name.toLowerCase();
  const type = node.class_type.toLowerCase();
  const title = node.title.toLowerCase();
  // 标准单LoRA加载器：lora_name / strength_model / strength_clip（保持精确匹配）
  if (name === "lora_name" && type.includes("lora")) return "lora_name";
  // 多LoRA加载器：lora_name_2 / lora_1 / lora_2_name（PowerLoRA / MultiLoRA / Efficiency Nodes）
  if (/^lora(_name_\d+|_\d+|_\d+_name)$/.test(name) && type.includes("lora")) return "lora_name";

  if ((name === "strength_model" || name === "strength_clip") && type.includes("lora")) {
    return "lora_weight";
  }
  // 多LoRA加载器权重：strength_1 / strength_model_1 / lora_wt_2 / strength_clip_3
  if (/^(strength(_model|_clip)?_\d+|lora_wt_\d+)$/.test(name) && type.includes("lora")) {
    return "lora_weight";
  }
  if (type === "emptylatentimage" && name === "width") return "latent_width";
  if (type === "emptylatentimage" && name === "height") return "latent_height";
  if (VIDEO_SIZE_NODE_TYPES.includes(type) && name === "width") return "video_width";
  if (VIDEO_SIZE_NODE_TYPES.includes(type) && name === "height") return "video_height";
  // 第 94 刀：参考视频节点的角色参考图槽（先于 base_image，否则 LoadImage 永远只会被标成底图）
  if ((name === "ref_image_files" || name === "reference_image_files") && type.includes("ref")) {
    return "reference_image_files";
  }
  const numberedRef = /^ref_image_(\d+)$/.exec(name);
  if (numberedRef && type.includes("ref")) return `reference_image_${numberedRef[1]}`;
  if (name === "ref_image" || name === "reference_image") return "reference_image";
  if (type.includes("loadimage") && (name === "image" || name === "images")) {
    const refTitle = REF_IMAGE_TITLE_RE.exec(title);
    if (refTitle) return refTitle[1] ? `reference_image_${refTitle[1]}` : "reference_image";
    return "base_image";
  }
  if (type.includes("cliptextencode") && name === "text") {
    return /negative|负面|负向/.test(title) ? "negative_prompt" : "prompt";
  }
  // 文本载体节点（原生 String / PrimitiveString / 自定义 Text）：字段名与节点类型都判不出
  // 语义，只有节点标题能声明它装的是什么。Anima AIO 全能流没有 CLIPTextEncode——提示词
  // 在 title="POS Prompt" 的 String 节点里，只按节点类型判会漏掉它。
  if (TEXT_CARRIER_TYPES.some((mark) => type.includes(mark))
      && TEXT_CARRIER_FIELDS.has(name)) {
    if (NEG_PROMPT_TITLE_RE.test(title)) return "negative_prompt";
    if (POS_PROMPT_TITLE_RE.test(title)) return "prompt";
  }
  // 音频（IndexTTS 系）：LoadAudio 参考音轨 / IndexTTS 台词与情感向量。
  // 情感字段名大小写归一（节点 widget 名 Happy/Angry…，与语义绑定 voice_emotion_<key> 解耦）。
  if (type.includes("loadaudio") && name === "audio") return "voice_reference";
  if (type.includes("indextts")) {
    if (name === "text") return "voice_text";
    if ((VOICE_EMOTION_KEYS as readonly string[]).includes(name)) return `voice_emotion_${name}`;
  }
  return "";
}

export function exposeWorkflowNodeFields(node: ParsedNode): ExposedField[] {
  return node.fields
    .filter((field) => !field.linked)
    .map((field) => ({
      node_id: node.id,
      field: field.name,
      label: field.name,
      control: inferWorkflowFieldControl(field),
      semantic: field.name,
      binding: inferWorkflowFieldBinding(node, field) || undefined,
      default: field.value,
    }));
}

export function replaceWorkflowNodeExposure(
  current: readonly ExposedField[],
  node: ParsedNode,
): ExposedField[] {
  return [
    ...current.filter((field) => field.node_id !== node.id),
    ...exposeWorkflowNodeFields(node),
  ];
}
