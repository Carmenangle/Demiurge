// 顺着 MODEL 类连线往上走，找出「改模型」到底该改哪个 widget。
//
// 为什么需要它：采样器的 model 是**连线**不是 widget，模型名在上游加载器的 widget 上。
// 而原先送给 AI 的 schema 只带一跳上游，于是像
//     KSamplerAdvanced → LoraLoaderModelOnly → UNETLoader
// 这种链，AI 只看得见 LoRA 加载器，用户说「换模型」它只能往 lora_name 写 —— 改错对象。
//
// 只顺 MODEL 系连线走，不做全图遍历：整图可能上百节点，全带上去会把 schema 撑爆。

// 承载 MODEL 的输入口名。不同节点叫法不一，但都是「模型从这儿进来」。
const MODEL_INPUTS = new Set(["model", "unet", "model_1", "base_model"]);

// class_type → 模型名 widget。判定优先按类型精确匹配，匹配不到再按 widget 名兜底
// （自定义加载器五花八门，但 widget 名基本都带 _name）。
export const LOADER_WIDGET: Record<string, string> = {
  CheckpointLoaderSimple: "ckpt_name",
  CheckpointLoader: "ckpt_name",
  UNETLoader: "unet_name",
  UnetLoaderGGUF: "unet_name",
  LoraLoader: "lora_name",
  LoraLoaderModelOnly: "lora_name",
};

// 兜底识别：widget 名像模型名，且值像模型文件
const NAME_LIKE = /^(ckpt|unet|lora|model|diffusion|gguf)_name$/i;
const FILE_LIKE = /\.(safetensors|ckpt|gguf|sft|pt|pth|bin)$/i;

export interface ChainNode {
  id: string;
  type: string;
  hops: number;             // 距采样器几跳
  widget: string;           // 模型名 widget；空=这节点只是直通，没模型可改
  value: string;            // 该 widget 当前值
}

interface Node {
  id?: string | number;
  type?: string;
  class_type?: string;
  inputs?: { name?: string; source_node_id?: string; link?: number }[];
  widgets?: { name?: string; value?: unknown }[];
}

function nodeType(n: Node): string {
  return String(n.type || n.class_type || "");
}

function widgetValue(n: Node, name: string): string {
  for (const w of n.widgets || []) {
    if (w && w.name === name) return typeof w.value === "string" ? w.value : "";
  }
  return "";
}

// 找出这个节点的模型名 widget（没有则返回空串）
export function modelWidgetOf(n: Node): string {
  // 类型已知就直接认，值为空也认（新建的加载器可能还没选文件）
  const known = LOADER_WIDGET[nodeType(n)];
  if (known) return known;
  for (const w of n.widgets || []) {
    const nm = String(w?.name || "");
    if (NAME_LIKE.test(nm)) {
      const v = w?.value;
      if (typeof v === "string" && (FILE_LIKE.test(v) || v === "")) return nm;
    }
  }
  return "";
}

// 顺 model 输入一路往上，返回整条链（从最靠近采样器的一跳开始）。
// maxHops 防环与防病态深链；带 seen 集合彻底防环。
export function walkModelChain(
  nodes: Node[], samplerId: string, maxHops = 12,
): ChainNode[] {
  const byId = new Map<string, Node>();
  for (const n of nodes) if (n?.id != null) byId.set(String(n.id), n);

  const nextUp = (n: Node): string => {
    for (const inp of n.inputs || []) {
      if (inp?.name && MODEL_INPUTS.has(inp.name) && inp.source_node_id) {
        return String(inp.source_node_id);
      }
    }
    return "";
  };

  const start = byId.get(String(samplerId));
  if (!start) return [];
  const out: ChainNode[] = [];
  const seen = new Set<string>([String(samplerId)]);
  let cur = nextUp(start);
  let hops = 1;
  while (cur && hops <= maxHops && !seen.has(cur)) {
    seen.add(cur);
    const n = byId.get(cur);
    if (!n) break;
    const widget = modelWidgetOf(n);
    out.push({
      id: cur, type: nodeType(n), hops, widget,
      value: widget ? widgetValue(n, widget) : "",
    });
    cur = nextUp(n);
    hops += 1;
  }
  return out;
}

// 链上所有能改模型的节点（跳过 ModelSampling/CFGNorm 这类直通节点）
export function loadersInChain(chain: ChainNode[]): ChainNode[] {
  return chain.filter((c) => !!c.widget);
}

// 采样器节点 id（可能多个）
export function samplerIds(nodes: Node[]): string[] {
  return nodes
    .filter((n) => /sampler/i.test(nodeType(n)))
    .map((n) => String(n.id));
}
