import {
  VOICE_EMOTION_KEYS,
  type ControlType, type ExposedField, type ParsedField, type ParsedNode,
} from "../api/workflows";

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
  if (type.includes("loadimage") && (name === "image" || name === "images")) return "base_image";
  if (type.includes("cliptextencode") && name === "text") {
    return /negative|负面|负向/.test(title) ? "negative_prompt" : "prompt";
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
