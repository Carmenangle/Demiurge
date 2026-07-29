import { getTemplateRaw, type Template } from "../api/workflows";
import { workflowPorts, type PortOp } from "../api/ai";
import { uploadImage } from "../api/comfyui";
import type { RichContent } from "../components/RichInput";
import type { ChatMessage } from "../types/chat";
import { fmtOpResults } from "./opResults";
import { isLafMessageFromStrict, postToFrame } from "./lafLock";
import { walkModelChain } from "./modelChain";

type Chat = { baseUrl: string; apiKey: string; modelName: string };

// 常见 ComfyUI 节点的 widget 顺序 → 真实 widget 名（UI 格式 widgets_values 无名，据此对齐）。
// 仅用于未确认卡从 raw.workflow 解析；apply 按名匹配，故名字须与 ComfyUI 一致。
const WIDGET_NAMES: Record<string, string[]> = {
  EmptyLatentImage: ["width", "height", "batch_size"],
  EmptySD3LatentImage: ["width", "height", "batch_size"],
  CLIPTextEncode: ["text"],
  KSampler: ["seed", "control_after_generate", "steps", "cfg", "sampler_name", "scheduler", "denoise"],
  KSamplerAdvanced: ["add_noise", "noise_seed", "control_after_generate", "steps", "cfg",
    "sampler_name", "scheduler", "start_at_step", "end_at_step", "return_with_leftover_noise"],
  CheckpointLoaderSimple: ["ckpt_name"],
  UNETLoader: ["unet_name", "weight_dtype"],
  VAELoader: ["vae_name"],
  CLIPLoader: ["clip_name", "type", "device"],
  LoraLoader: ["lora_name", "strength_model", "strength_clip"],
  // 只吃 MODEL 不吃 CLIP 的 LoRA 加载器，UNET 工作流里很常见（用户的 Krea2 流就是这个）。
  // 漏了它的话 lora_name 会退化成 widget_0，改模型时按名匹配就找不到。
  LoraLoaderModelOnly: ["lora_name", "strength_model"],
  UnetLoaderGGUF: ["unet_name"],
  CLIPLoaderGGUF: ["clip_name", "type"],
  SaveImage: ["filename_prefix"],
  LatentUpscale: ["upscale_method", "width", "height", "crop"],
};

export interface OrchestrationDeps {
  messages: ChatMessage[];
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  templates: Template[];
  chat: Chat;
  comfyuiUrl: string;
  imageStyle?: string;  // 用户选的提示词风格 ""/sd/gpt/banana，透传给 workflowPorts
  styleTemplate?: string;  // 选中的自定义风格存档内容（非空时优先）
  repoId?: string;      // 小仓库 id，后端据此取该仓库的当前色彩约束
  pushBot: (text: string) => void;
  runFreeText: (t: string, content?: RichContent, skipUserMsg?: boolean, userMsgId?: string) => void;
}

// 工作流输入口编排：读节点结构 → AI 出计划 → 写画布/capturedGraph。
// 从 ChatView 抽出，通过 deps 拿到消息列表、模板、对话模型与回退对话能力。
// 对外只暴露 findWorkflowCardByName / planWorkflowOps / applyWorkflowOps / ignoreWorkflowOps。
export function useWorkflowOrchestration(deps: OrchestrationDeps) {
  const { messages, setMessages, templates, chat, comfyuiUrl, imageStyle, styleTemplate, repoId, pushBot, runFreeText } = deps;

  // 按 /a 后的文本找工作流卡：文本以某卡的模板名开头即匹配（其后为编排需求）。
  // 返回卡与命中的模板名，供剥离出需求部分。文本为空则取最近一张有编排能力的卡（matchedName=""）。
  const findWorkflowCardByName = (rest: string): { card: ChatMessage; matchedName: string } | null => {
    const orchable = (m: ChatMessage) => {
      if (!m.workflow) return false;
      const tpl = templates.find((t) => t.id === m.workflow!.templateId);
      if (!tpl) return false;
      return (tpl.input_node_ids?.length || 0) > 0 || (tpl.output_node_ids?.length || 0) > 0
        || !!tpl.prompt_node_id || !!tpl.image_node_id;
    };
    // 名字对上了但模板没配编排节点 → 记下来，供上层给出准确提示（而非误报"没找到"）。
    let nameMatchedButNotConfigured = false;
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (!m.workflow) continue;
      const tn = m.workflow.templateName || "";
      const nameHit = !rest || (tn && rest.startsWith(tn));
      if (!orchable(m)) {
        if (rest && nameHit) nameMatchedButNotConfigured = true;
        continue;
      }
      if (!rest) return { card: m, matchedName: "" };            // 空 → 最近可编排卡
      if (nameHit) return { card: m, matchedName: tn };          // 以模板名开头
    }
    return nameMatchedButNotConfigured ? { card: null as any, matchedName: "__unconfigured__" } : null;
  };

  // 向某工作流卡的某节点 iframe 发消息并等指定类型回复
  const askNodeFrame = <T,>(cardId: string, nodeId: string, send: object, expectType: string, ms = 4000) =>
    new Promise<T | null>((resolve) => {
      const frame = document.getElementById(`laf-node-${cardId}-${nodeId}`) as HTMLIFrameElement | null;
      if (!frame?.contentWindow) return resolve(null);
      let done = false;
      const onMsg = (ev: MessageEvent) => {
        if (!isLafMessageFromStrict(ev, frame.contentWindow, comfyuiUrl, expectType)) return;
        done = true;
        window.removeEventListener("message", onMsg);
        resolve(ev.data.payload as T);
      };
      window.addEventListener("message", onMsg);
      postToFrame(frame.contentWindow, (send as any).type, (send as any).payload, comfyuiUrl);
      setTimeout(() => { if (!done) { window.removeEventListener("message", onMsg); resolve(null); } }, ms);
    });

  // dataURI/URL → File，供上传到 ComfyUI input
  const srcToFile = async (src: string, name: string): Promise<File> => {
    const resp = await fetch(src);
    const blob = await resp.blob();
    const ext = (blob.type.split("/")[1] || "png").split("+")[0];
    return new File([blob], `${name}.${ext}`, { type: blob.type || "image/png" });
  };

  // 规划工作流输入口填充：收集该卡节点结构 → AI 出计划 → push 计划消息（待确认）。
  // force=false 时先让 AI 判断意图，非编排（普通问答/修饰词/翻译）自动回退到 AI 对话。
  const planWorkflowOps = async (card: ChatMessage, text: string, content: RichContent, force: boolean) => {
    const images = content.images || [];
    // 立即 push 用户气泡（意图判定是异步 AI 调用，之前不 push 会让回车后界面无反应、
    // 直到判定返回才和回复一起冒出）。后续所有分支都复用这个 id，绝不重复 push。
    const userMsgId = crypto.randomUUID();
    setMessages((m) => [...m, { id: userMsgId, role: "user", text, parts: content.parts }]);
    const fallbackToChat = () => runFreeText(text, content, true, userMsgId);  // 用户气泡已 push
    let raw;
    try { raw = await getTemplateRaw(card.workflow!.templateId); }
    catch { return fallbackToChat(); }  // 读不到模板 → 退回对话，别卡住
    const nodeIds: string[] = raw.exposed_ids || [];
    if (nodeIds.length === 0) {
      // 没配替换节点：不参与编排（防乱改），直接走对话
      return fallbackToChat();
    }
    // 读节点结构：已确认卡从 capturedGraph（完整 API 图）；未确认卡从模板完整工作流 raw.workflow。
    // 关键：不能从画布单节点 iframe 读——那些 iframe 用 exposedIds 载入后 keepOnly 会删除其他节点
    // 并把所有连线置空，导致 latent/条件等被误判「未连接」、上游节点丢失。raw.workflow 含全图全连线。
    let schemas: unknown[] = [];
    if (card.workflow!.done && card.workflow!.capturedGraph) {
      schemas = schemaFromCapturedGraph(card.workflow!.capturedGraph, nodeIds);
    } else {
      schemas = schemaFromRawWorkflow(raw.workflow, nodeIds);
    }
    if (schemas.length === 0) {
      // 读不到结构：force 时提示重试；否则退回对话（不打扰）。用户气泡已 push，只补 bot。
      if (force) {
        pushBot("没能读到节点结构（画布可能还在载入）。请稍等画布载入完成后再点「AI 编排」。");
        return;
      }
      return fallbackToChat();
    }
    const modelName = guessModelName(raw.workflow);
    let plan;
    try {
      plan = await workflowPorts(text, images.length, schemas, modelName, chat, force, imageStyle || "", styleTemplate || "", repoId || "");
    } catch (e) {
      if (force) {
        pushBot(`规划失败：${(e as Error).message}`);
        return;
      }
      return fallbackToChat();  // 判定阶段出错 → 退回对话
    }
    // AI 判定这句不是编排意图 → 转普通对话（新手无需记指令）
    if (plan.is_orchestration === false && !force) {
      return fallbackToChat();
    }
    // 是编排：用户气泡已 push，这里只追加计划卡（待确认）
    setMessages((m) => [
      ...m,
      {
        id: crypto.randomUUID(),
        role: "assistant",
        text: "",
        portsPlan: {
          cardId: card.id,
          summary: plan.summary || "",
          ops: plan.ops || [],
          images,
          status: "pending",
        },
      },
    ]);
  };

  // 从 capturedGraph(API格式 {id:{class_type,inputs}}) 提取选中节点的输入结构，供 AI 改参。
  // 连线输入的 [srcId,slot] → 记 source_node_id，并把该上游源节点也纳入（neighbor:"upstream"），
  // 使 AI 能改选中节点左侧接线的内容（如 latent 尺寸改上游 EmptyLatentImage 的 width/height）。
  const schemaFromCapturedGraph = (graph: any, nodeIds: string[]): unknown[] => {
    const out: unknown[] = [];
    const seen = new Set<string>();
    const upstreamIds = new Set<string>();
    const nodeSchema = (id: string, node: any, neighbor?: string) => {
      const inputs: any[] = [];
      const widgets: any[] = [];
      for (const [name, v] of Object.entries(node.inputs || {})) {
        if (Array.isArray(v)) {
          const srcId = String(v[0]);
          inputs.push({ name, type: "", connected: true, source_type: "", source_node_id: srcId });
          if (!neighbor) upstreamIds.add(srcId);   // 只跟随选中节点一层上游，邻居不再外扩
        } else {
          widgets.push({ name, type: typeof v, value: v });
        }
      }
      return { id: String(id), type: node.class_type || "", title: "", ...(neighbor ? { neighbor } : {}), inputs, widgets };
    };
    for (const id of nodeIds) {
      const node = graph?.[id];
      if (!node) continue;
      seen.add(String(id));
      out.push(nodeSchema(String(id), node));
    }
    // 补进直接上游源节点（去重，跳过已在选中集里的）
    for (const srcId of upstreamIds) {
      if (seen.has(srcId)) continue;
      const node = graph?.[srcId];
      if (!node) continue;
      seen.add(srcId);
      out.push(nodeSchema(srcId, node, "upstream"));
    }
    // 沿 MODEL 连线补齐整条模型链（理由同 schemaFromRawWorkflow 里的说明）
    const pool = Object.entries(graph || {}).map(([id, n]: [string, any]) => ({
      id: String(id),
      type: String(n?.class_type || ""),
      inputs: Object.entries(n?.inputs || {})
        .filter(([, v]) => Array.isArray(v))
        .map(([name, v]: [string, any]) => ({
          name, source_node_id: String(v[0]),
        })),
    }));
    for (const s of out as { id: string; type: string }[]) {
      if (!/sampler/i.test(s.type)) continue;
      for (const link of walkModelChain(pool, s.id)) {
        if (seen.has(link.id)) continue;
        const node = graph?.[link.id];
        if (!node) continue;
        seen.add(link.id);
        out.push(nodeSchema(link.id, node, "model-chain"));
      }
    }
    return out;
  };

  // 从模板完整工作流提取选中节点 + 直接上游的 schema（未确认卡用）。兼容两种格式：
  // API 格式（{id:{class_type,inputs}}）直接复用 schemaFromCapturedGraph；
  // UI 格式（{nodes:[{id,type,inputs:[{name,link}],widgets_values}], links:[[id,srcId,srcSlot,dstId,dstSlot,type]]}）
  // 在此解析：连线从 links 反查上游源节点 id，widget 值从 widgets_values 按序取（够 AI 判断即可）。
  const schemaFromRawWorkflow = (workflow: any, nodeIds: string[]): unknown[] => {
    if (!workflow || typeof workflow !== "object") return [];
    // API 格式：顶层无 nodes 数组，值含 class_type
    if (!Array.isArray(workflow.nodes)) {
      return schemaFromCapturedGraph(workflow, nodeIds);
    }
    // UI 格式
    const byId = new Map<string, any>();
    for (const n of workflow.nodes) if (n && n.id != null) byId.set(String(n.id), n);
    // link_id -> 上游源节点 id
    const linkSrc = new Map<number, string>();
    for (const l of workflow.links || []) {
      if (Array.isArray(l) && l.length >= 5) linkSrc.set(l[0], String(l[1]));
    }
    const out: unknown[] = [];
    const seen = new Set<string>();
    const upstreamIds = new Set<string>();
    const nodeSchema = (n: any, neighbor?: string) => {
      const inputs: any[] = [];
      for (const inp of n.inputs || []) {
        const connected = inp.link != null;
        const srcId = connected ? (linkSrc.get(inp.link) || "") : "";
        inputs.push({
          name: inp.name || "",
          type: typeof inp.type === "string" ? inp.type : String(inp.type || ""),
          connected, source_type: "", source_node_id: srcId,
        });
        if (!neighbor && srcId) upstreamIds.add(srcId);
      }
      // UI 格式 widgets_values 是无名数组，按节点类型映射为真实 widget 名（apply 时按名匹配）。
      // 覆盖常见文生图节点；未知类型回退 widget_i（仍能读连线，不影响「未连接」判断修复）。
      const wvals = Array.isArray(n.widgets_values) ? n.widgets_values : [];
      const names = WIDGET_NAMES[String(n.type || n.comfyClass || "")];
      const widgets = wvals
        .map((v: unknown, i: number) => ({
          name: names?.[i] || `widget_${i}`,
          type: typeof v, value: v,
        }))
        .filter((w: { type: string }) => ["string", "number", "boolean"].includes(w.type));
      return { id: String(n.id), type: n.type || n.comfyClass || "",
        title: n.title || "", ...(neighbor ? { neighbor } : {}), inputs, widgets };
    };
    for (const id of nodeIds) {
      const n = byId.get(String(id));
      if (!n) continue;
      seen.add(String(id));
      out.push(nodeSchema(n));
    }
    for (const srcId of upstreamIds) {
      if (seen.has(srcId)) continue;
      const n = byId.get(srcId);
      if (!n) continue;
      seen.add(srcId);
      out.push(nodeSchema(n, "upstream"));
    }
    // 再沿 MODEL 连线把整条模型链补齐。只带一跳的话，
    // KSampler → LoraLoaderModelOnly → UNETLoader 这种链里 AI 看不到 UNETLoader，
    // 用户说「换模型」它只能往 lora_name 写。只顺 MODEL 走，不做全图遍历（整图可能上百节点）。
    const pool = [...byId.values()].map((n: any) => ({
      id: String(n.id),
      type: String(n.type || n.comfyClass || ""),
      inputs: (n.inputs || []).map((inp: any) => ({
        name: inp?.name || "",
        source_node_id: inp?.link != null ? (linkSrc.get(inp.link) || "") : "",
      })),
    }));
    for (const s of out as { id: string; type: string }[]) {
      if (!/sampler/i.test(s.type)) continue;
      for (const link of walkModelChain(pool, s.id)) {
        if (seen.has(link.id)) continue;
        const raw = byId.get(link.id);
        if (!raw) continue;
        seen.add(link.id);
        out.push(nodeSchema(raw, "model-chain"));
      }
    }
    return out;
  };

  // 从工作流 JSON 里猜 checkpoint/模型名（用于提示词风格）
  const guessModelName = (wf: any): string => {
    const nodes = wf?.nodes;
    if (!Array.isArray(nodes)) return "";
    for (const n of nodes) {
      const t = String(n.type || "");
      if (/Checkpoint|UNETLoader|Loader/i.test(t) && Array.isArray(n.widgets_values)) {
        const s = n.widgets_values.find((v: unknown) => typeof v === "string" && /\.(safetensors|ckpt|gguf|sft)$/i.test(v));
        if (s) return s;
      }
    }
    return "";
  };

  // 应用一条编排计划：未确认卡→写画布后自动「选择完毕」；已确认卡→直接改 capturedGraph
  const applyWorkflowOps = async (planMsgId: string) => {
    const planMsg = messages.find((m) => m.id === planMsgId);
    const plan = planMsg?.portsPlan;
    if (!plan) return;
    const card = messages.find((m) => m.id === plan.cardId);
    if (!card?.workflow) { pushBot("目标工作流卡不存在。"); return; }

    if (card.workflow.done) {
      await applyOpsToCaptured(planMsgId, plan, card);
    } else {
      await applyOpsToCanvas(planMsgId, plan, card);
    }
  };

  // 未确认卡：上传图 → 构造 ops → 触发本卡「选择完毕」（在全图隐藏 iframe 里 apply_ops 后抓参，
  // 这样 AI 新建的 LoadImage/连线能被原生 graphToPrompt 正确纳入），抓完自动卸载画布省显存。
  const applyOpsToCanvas = async (
    planMsgId: string,
    plan: NonNullable<ChatMessage["portsPlan"]>,
    card: ChatMessage,
  ) => {
    // 先上传需要用图的 op（set_image，或 kind=image 的 replace_output），换成 ComfyUI 文件名
    const uploaded: Record<number, string> = {};
    const needsImage = (op: PortOp) =>
      op.action === "set_image" || (op.action === "replace_output" && op.kind === "image");
    for (const op of plan.ops) {
      if (needsImage(op) && op.image_index && !uploaded[op.image_index]) {
        const src = plan.images[op.image_index - 1];
        if (!src) continue;
        try {
          const file = await srcToFile(src, `laf_${Date.now()}_${op.image_index}`);
          const up = await uploadImage(file, comfyuiUrl);
          uploaded[op.image_index] = up.name;
        } catch (e) {
          pushBot(`图${op.image_index} 上传失败：${(e as Error).message}`);
        }
      }
    }
    // 构造扁平 ops（已带 value / image_name），交给本卡在全图 iframe 执行
    const ops = plan.ops.map((op) => {
      const o: any = { node_id: String(op.node_id), input: op.input, action: op.action };
      if (op.action === "set_widget") o.value = op.value;
      if (op.action === "set_image") o.image_name = uploaded[op.image_index || 0] || "";
      if (op.action === "replace_output") {
        o.output = op.output;
        o.kind = op.kind;
        if (op.kind === "image") o.image_name = uploaded[op.image_index || 0] || "";
        else o.value = op.value;
      }
      return o;
    });
    markPlanApplied(planMsgId);
    pushBot("正在写入画布并自动「选择完毕」（抓取参数、关闭画布省显存）…");
    // 触发本卡 handleDone(ops)：全图 iframe 载入 → apply_ops → graphToPrompt → 卸载画布
    window.dispatchEvent(new CustomEvent("laf-finish-card", { detail: { cardId: card.id, ops } }));
  };

  // 已确认卡：画布已关，直接改 capturedGraph(API格式)。仅支持改 widget 标量；
  // set_image 在已确认态无法新建节点连线 → 退化为改其上游 LoadImage 的 image，失败则提示重开画布。
  const applyOpsToCaptured = async (
    planMsgId: string,
    plan: NonNullable<ChatMessage["portsPlan"]>,
    card: ChatMessage,
  ) => {
    const graph: any = JSON.parse(JSON.stringify(card.workflow!.capturedGraph));
    const results: any[] = [];
    const uploaded: Record<number, string> = {};
    for (const op of plan.ops) {
      const r: any = { node_id: String(op.node_id), input: op.input, ok: false, msg: "" };
      const node = graph[String(op.node_id)];
      if (!node) { r.msg = "节点不存在于已确认工作流"; results.push(r); continue; }
      if (op.action === "set_widget") {
        node.inputs[op.input] = op.value;
        r.ok = true;
      } else if (op.action === "set_image") {
        // 上传图
        let name = uploaded[op.image_index || 0];
        if (!name && op.image_index) {
          const src = plan.images[op.image_index - 1];
          if (src) {
            try {
              const file = await srcToFile(src, `laf_${Date.now()}_${op.image_index}`);
              name = (await uploadImage(file, comfyuiUrl)).name;
              uploaded[op.image_index] = name;
            } catch (e) { r.msg = `图上传失败：${(e as Error).message}`; results.push(r); continue; }
          }
        }
        const cur = node.inputs[op.input];
        if (Array.isArray(cur) && graph[cur[0]]?.class_type === "LoadImage") {
          // 该口连着 LoadImage → 改它的 image 文件名
          graph[cur[0]].inputs.image = name;
          r.ok = true;
        } else {
          r.msg = "该口未连 LoadImage，已确认态无法新建节点，请点「更改」重开画布处理";
        }
      } else if (op.action === "replace_output") {
        // 输出口替换需新建源节点并重接下游连线，已确认态(画布已关)做不了 → 提示重开画布
        r.input = op.output || op.input;
        r.msg = "输出口替换需在画布执行，请点「更改」重开画布后再让我处理";
      } else {
        r.msg = "未知动作";
      }
      results.push(r);
    }
    // 写回 capturedGraph
    setMessages((ms) =>
      ms.map((m) =>
        m.id === card.id && m.workflow
          ? { ...m, workflow: { ...m.workflow, capturedGraph: graph } }
          : m,
      ),
    );
    markPlanApplied(planMsgId);
    const okN = results.filter((r) => r.ok).length;
    pushBot(`已更新参数 ${okN}/${results.length} 项（已确认工作流）：\n${fmtOpResults(results)}\n直接输入 /s 提交，改动会和未修改节点一起出图。`);
  };

  const markPlanApplied = (planMsgId: string) =>
    setMessages((ms) =>
      ms.map((m) =>
        m.id === planMsgId && m.portsPlan
          ? { ...m, portsPlan: { ...m.portsPlan, status: "applied" } }
          : m,
      ),
    );

  const ignoreWorkflowOps = (planMsgId: string) =>
    setMessages((ms) =>
      ms.map((m) =>
        m.id === planMsgId && m.portsPlan
          ? { ...m, portsPlan: { ...m.portsPlan, status: "ignored" } }
          : m,
      ),
    );

  // 执行前编辑某条 op 的文本值（如把 AI 加工的中文提示词改成 Anima 英文结构）。
  // 只改 message state 里的 ops.value；apply 读的就是它，天然按编辑后的值执行。
  const editWorkflowOp = (planMsgId: string, opIndex: number, newValue: string) =>
    setMessages((ms) =>
      ms.map((m) => {
        if (m.id !== planMsgId || !m.portsPlan) return m;
        const ops = m.portsPlan.ops.map((op, i) => (i === opIndex ? { ...op, value: newValue } : op));
        return { ...m, portsPlan: { ...m.portsPlan, ops } };
      }),
    );

  return { findWorkflowCardByName, planWorkflowOps, applyWorkflowOps, ignoreWorkflowOps, editWorkflowOp };
}
