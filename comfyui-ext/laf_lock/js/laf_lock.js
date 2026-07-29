// Local AI Frontend - ComfyUI 只读锁定扩展
// 仅在 URL 带 ?laf_lock=1 时生效，避免影响正常使用 ComfyUI。
// 能力：载入指定工作流 → 只保留暴露节点 → 锁定移动/连线/增删，但保留 widget 交互、缩放、拖视角。
import { app } from "../../scripts/app.js";

const params = new URLSearchParams(location.search);
const LOCK = params.get("laf_lock") === "1";
// full 模式：AI 搭工作流页右侧画布用。保留 ComfyUI 全部原生功能（工具栏/增删节点/连线），
// 只额外挂 postMessage 协议供父页面写入整图(load)/读回画布(request_graph/request_api_prompt)。
// 与 LOCK 完全隔离：两个 URL 参数、两个扩展分支，锁定模式零改动，不影响生图链路。
const FULL = params.get("laf_full") === "1";

// 向父窗口发消息
function toParent(type, payload) {
  if (window.parent && window.parent !== window) {
    window.parent.postMessage({ source: "laf_lock", type, payload }, "*");
  }
}

// 序列化当前图为 ComfyUI 工作流 JSON（含用户改过的 widget 值）
function serialize() {
  try {
    return app.graph.serialize();
  } catch (e) {
    return null;
  }
}

// 收集指定节点的输入口结构（供 AI 判断各口放什么）：
// 每个口给出 name/type、是否已连线、连线来源节点类型；外加可填 widget（名/类型/当前值）。
// includeNeighbors=true 时：额外收进每个连线输入的【直接上游源节点】(neighbor:"upstream")
// 和每个输出口下游连到的【直接下游节点】(neighbor:"downstream")，使 AI 能改到
// 选中节点左侧接线所依赖的上游 widget（如 KSampler 的 latent 尺寸在上游 EmptyLatentImage、
// 正负条件在上游 CLIPTextEncode）。
function collectNodeSchema(nodeIds, includeNeighbors) {
  const ids = (nodeIds && nodeIds.length ? nodeIds : []).map(String);
  const out = [];
  const seen = new Set();
  const targets = ids.length
    ? ids.map((id) => app.graph.getNodeById(Number(id))).filter(Boolean)
    : (app.graph._nodes || []);
  const neighborNodes = [];  // {node, rel} 待补充的上/下游邻居
  for (const n of targets) {
    seen.add(String(n.id));
    const inputs = [];
    for (const inp of n.inputs || []) {
      let srcType = "";
      let srcId = "";
      if (inp.link != null && app.graph.links && app.graph.links[inp.link]) {
        const link = app.graph.links[inp.link];
        const src = app.graph.getNodeById(link.origin_id);
        srcType = src ? (src.type || src.comfyClass || "") : "";
        srcId = src ? String(src.id) : "";
        // 记直接上游源节点：AI 改「左侧接线」内容（latent 尺寸/提示词等）实为改上游 widget
        if (includeNeighbors && src) neighborNodes.push({ node: src, rel: "upstream" });
      }
      inputs.push({
        name: inp.name || "",
        type: typeof inp.type === "string" ? inp.type : String(inp.type || ""),
        connected: inp.link != null,
        source_type: srcType,
        source_node_id: srcId,   // 上游源节点 id，AI 改左侧接线内容时对该 id 出 set_widget
      });
    }
    const widgets = [];
    for (const w of n.widgets || []) {
      widgets.push({
        name: w.name || "",
        type: w.type || "",
        value: typeof w.value === "string" || typeof w.value === "number" || typeof w.value === "boolean"
          ? w.value : "",
      });
    }
    // 输出口（右侧接线）：name/type + 下游连接的 {node_id, input_name}，供 AI 判断
    // 替换该输出口需重接哪些下游口（输出替换 = 新建源节点顶替这些下游连线）。
    const outputs = [];
    for (const out_ of n.outputs || []) {
      const targets = [];
      for (const lid of out_.links || []) {
        const link = app.graph.links && app.graph.links[lid];
        if (!link) continue;
        const dst = app.graph.getNodeById(link.target_id);
        const dstInput = dst && dst.inputs && dst.inputs[link.target_slot];
        targets.push({
          node_id: String(link.target_id),
          node_type: dst ? (dst.type || dst.comfyClass || "") : "",
          input_name: dstInput ? (dstInput.name || "") : "",
        });
        if (includeNeighbors && dst) neighborNodes.push({ node: dst, rel: "downstream" });
      }
      outputs.push({
        name: out_.name || "",
        type: typeof out_.type === "string" ? out_.type : String(out_.type || ""),
        targets,
      });
    }
    out.push({
      id: String(n.id),
      type: n.type || n.comfyClass || "",
      title: n.title || "",
      mode: n.mode || 0,   // 0=正常 2=静音 4=绕过；后端据此跳过被绕过节点（bypass 的 LoRA 不注触发词）
      inputs,
      widgets,
      outputs,
    });
  }
  // 第二遍：补进直接上/下游邻居节点（只出自身 inputs/widgets/outputs，不再向外递归一层）。
  // 带 neighbor 标记供 AI 区分：AI 改选中节点的左侧接线内容时对 upstream 邻居出 set_widget。
  if (includeNeighbors) {
    for (const { node: nb, rel } of neighborNodes) {
      if (seen.has(String(nb.id))) continue;
      seen.add(String(nb.id));
      const inputs = (nb.inputs || []).map((inp) => ({
        name: inp.name || "",
        type: typeof inp.type === "string" ? inp.type : String(inp.type || ""),
        connected: inp.link != null,
      }));
      const widgets = (nb.widgets || []).map((w) => ({
        name: w.name || "",
        type: w.type || "",
        value: typeof w.value === "string" || typeof w.value === "number" || typeof w.value === "boolean"
          ? w.value : "",
      }));
      const outputs = (nb.outputs || []).map((o) => ({
        name: o.name || "",
        type: typeof o.type === "string" ? o.type : String(o.type || ""),
      }));
      out.push({
        id: String(nb.id),
        type: nb.type || nb.comfyClass || "",
        title: nb.title || "",
        mode: nb.mode || 0,
        neighbor: rel,   // "upstream" | "downstream"
        inputs,
        widgets,
        outputs,
      });
    }
  }
  return out;
}

// 在画布新建一个 LoadImage 节点并设其 image，返回节点（失败返回 null）
function makeLoadImage(imageName) {
  try {
    const node = LiteGraph.createNode("LoadImage");
    if (!node) return null;
    app.graph.add(node);
    // LoadImage 第一个 widget 即 image 文件名（combo），直接赋值上传得到的文件名
    const w = (node.widgets || []).find((x) => x.name === "image") || (node.widgets || [])[0];
    if (w) {
      // 把上传的文件名补进候选，避免 combo 校验把它丢掉
      if (Array.isArray(w.options?.values) && !w.options.values.includes(imageName)) {
        w.options.values.push(imageName);
      }
      w.value = imageName;
    }
    return node;
  } catch (e) {
    return null;
  }
}

// 新建一个输出 STRING 的文本源节点并设其文本，返回 {node, outIdx}（失败返回 null）。
// 不同环境的纯文本节点类名不一，按常见名依次尝试；都没有则返回 null（让上层提示人工处理）。
function makeTextNode(text) {
  const CANDS = ["PrimitiveString", "String", "Text", "CR Text", "ttN text",
                 "StringConstant", "PrimitiveNode"];
  for (const cls of CANDS) {
    let node;
    try { node = LiteGraph.createNode(cls); } catch (e) { node = null; }
    if (!node) continue;
    app.graph.add(node);
    // 找一个字符串型 widget 写入文本（widget 名常见 value/text/string）
    const w = (node.widgets || []).find((x) => /^(value|text|string)$/i.test(x.name || ""))
      || (node.widgets || [])[0];
    if (w) w.value = text;
    // 找 STRING 输出口
    let outIdx = (node.outputs || []).findIndex(
      (o) => String(o.type || "").toUpperCase() === "STRING");
    if (outIdx < 0) outIdx = (node.outputs || []).length ? 0 : -1;
    if (outIdx < 0) { app.graph.remove(node); continue; }
    return { node, outIdx };
  }
  return null;
}

// 按 AI 计划逐条改画布。每条 op：
//   {node_id, input, output, action, value, image_name, kind}
//   action: "set_widget"      → 把目标节点的 widget(input 名) 设为 value（文本/数值）
//           "set_image"       → 给目标节点的 input 图像口接入图（新建 LoadImage 设 image_name，
//                                顶替该口原有连线）；若 input 是 widget 而非连线口则直接设 widget
//           "replace_output"  → 替换目标节点 output 输出口：新建源节点(图→LoadImage / 文本→文本节点)，
//                                把该输出口原本连到的所有下游输入口改接到新源，使提供内容流入下游
function applyOps(ops) {
  const results = [];
  for (const op of ops) {
    const r = { node_id: String(op.node_id), input: op.input || "", action: op.action, ok: false, msg: "" };
    const n = app.graph.getNodeById(Number(op.node_id));
    if (!n) { r.msg = "节点不存在"; results.push(r); continue; }
    try {
      if (op.action === "set_widget") {
        const w = (n.widgets || []).find((x) => x.name === op.input);
        if (!w) { r.msg = "未找到该 widget"; results.push(r); continue; }
        w.value = op.value;
        r.ok = true;
      } else if (op.action === "set_image") {
        // 先看 input 是不是连线口
        const slotIdx = (n.inputs || []).findIndex((i) => i.name === op.input);
        if (slotIdx >= 0) {
          const loader = makeLoadImage(op.image_name);
          if (!loader) { r.msg = "新建 LoadImage 失败"; results.push(r); continue; }
          // LoadImage 的 IMAGE 输出（找名为 IMAGE 的输出，否则第 0 个）
          let outIdx = (loader.outputs || []).findIndex((o) => (o.name || "").toUpperCase() === "IMAGE");
          if (outIdx < 0) outIdx = 0;
          loader.connect(outIdx, n, slotIdx); // connect 会自动顶掉该输入口原有连线
          r.ok = true;
        } else {
          // 不是连线口，退化为 widget 赋值（如 LoadImage 自身的 image combo widget）
          const w = (n.widgets || []).find((x) => x.name === op.input);
          if (w) {
            // image 是 combo widget：上传的新文件名必须先补进候选，否则 combo 校验/
            // graphToPrompt 会把不在候选里的值丢弃、回退到原图 → 最终仍用原图
            if (Array.isArray(w.options?.values) && !w.options.values.includes(op.image_name)) {
              w.options.values.push(op.image_name);
            }
            w.value = op.image_name;
            r.ok = true;
          } else r.msg = "该口既非图像连线口也非 widget";
        }
      } else if (op.action === "replace_output") {
        // 替换输出口：新建源节点，把该输出口原本连到的每个下游输入口改接到新源，
        // 顶替原连线，使你提供的图/文本随工作流流入下游。
        r.input = op.output || op.input || "";
        const outIdx = (n.outputs || []).findIndex((o) => o.name === r.input);
        if (outIdx < 0) { r.msg = "未找到该输出口"; results.push(r); continue; }
        const outPort = n.outputs[outIdx];
        // 收集下游连接 [{node, slot}]（在改动前快照，避免边改边遍历）
        const downstream = [];
        for (const lid of (outPort.links || []).slice()) {
          const link = app.graph.links && app.graph.links[lid];
          if (!link) continue;
          const dst = app.graph.getNodeById(link.target_id);
          if (dst) downstream.push({ node: dst, slot: link.target_slot });
        }
        if (downstream.length === 0) { r.msg = "该输出口未连接任何下游，无需替换"; results.push(r); continue; }
        // 按类型建源节点
        const isImage = (op.kind === "image") || !!op.image_name;
        let srcNode, srcOut;
        if (isImage) {
          const loader = makeLoadImage(op.image_name);
          if (!loader) { r.msg = "新建 LoadImage 失败"; results.push(r); continue; }
          srcNode = loader;
          srcOut = (loader.outputs || []).findIndex((o) => (o.name || "").toUpperCase() === "IMAGE");
          if (srcOut < 0) srcOut = 0;
        } else {
          const made = makeTextNode(op.value != null ? String(op.value) : "");
          if (!made) { r.msg = "环境无可用文本节点，请人工处理"; results.push(r); continue; }
          srcNode = made.node;
          srcOut = made.outIdx;
        }
        // 把新源接到每个下游输入口（connect 自动顶掉原连线）
        let okN = 0;
        for (const d of downstream) {
          try { if (srcNode.connect(srcOut, d.node, d.slot)) okN++; } catch (e) { /* skip */ }
        }
        r.ok = okN > 0;
        if (!r.ok) r.msg = "下游重接失败";
        else if (okN < downstream.length) r.msg = `已重接 ${okN}/${downstream.length} 个下游`;
      } else {
        r.msg = "未知 action";
      }
    } catch (e) {
      r.msg = String((e && e.message) || e);
    }
    results.push(r);
  }
  return results;
}

// 只保留 exposedIds 指定的节点，其余删除；清空连线（看不到连线情况）
function keepOnly(exposedIds) {
  if (!exposedIds || exposedIds.length === 0) return;
  const keep = new Set(exposedIds.map(String));
  const nodes = [...app.graph._nodes];
  for (const n of nodes) {
    if (!keep.has(String(n.id))) {
      app.graph.remove(n);
    }
  }
  // 清空所有连线（保留节点自身的输入输出槽作为"连线桩"，但不画线）
  if (app.graph.links) {
    for (const id of Object.keys(app.graph.links)) {
      delete app.graph.links[id];
    }
  }
  for (const n of app.graph._nodes) {
    if (n.inputs) for (const i of n.inputs) i.link = null;
    if (n.outputs) for (const o of n.outputs) o.links = null;
  }
  // 按 exposedIds 的顺序把保留的节点竖向排成一列（提取成单独节点列表的样子）
  const GAP = 40;
  let y = 0;
  for (const id of exposedIds.map(String)) {
    const n = app.graph.getNodeById(Number(id));
    if (!n) continue;
    n.pos = [0, y];
    const h = (n.size && n.size[1]) ? n.size[1] : 100;
    y += h + GAP;
  }
  // 删除所有组（彩色框）——它们不是节点，残留会让画面杂乱
  try {
    if (Array.isArray(app.graph._groups)) {
      for (const g of [...app.graph._groups]) {
        if (app.graph.remove) app.graph.remove(g);
      }
    }
    app.graph._groups = [];
  } catch (e) {}
  app.graph.setDirtyCanvas(true, true);
}

// PLACEHOLDER_LOCK

// 关闭多余的工作流标签页：每次 loadGraphData 都会新建一个 "Unsaved Workflow" 临时标签，
// watchdog 反复重载会让它们堆叠。这里在载入后只保留当前活动标签，关掉其余。
// 通过 Vue app(#vue-app) 拿到 Pinia 实例，再取 workspace store 的 workflow 子状态。
function getPinia() {
  try {
    const app2 = document.getElementById("vue-app")?.__vue_app__;
    if (!app2) return null;
    // 优先 globalProperties.$pinia
    const gp = app2.config?.globalProperties;
    if (gp?.$pinia?._s) return gp.$pinia;
    // 兜底：遍历 provides 找含 store map(_s) 的对象 = pinia 实例
    const provides = app2._context?.provides || {};
    for (const k of Reflect.ownKeys(provides)) {
      const v = provides[k];
      if (v && v._s instanceof Map) return v;
    }
  } catch (e) {}
  return null;
}

function getWorkflowStore() {
  const pinia = getPinia();
  if (!pinia) return null;
  try {
    const ws = pinia._s.get("workspace");
    const wf = ws?.workflow;
    if (wf && Array.isArray(wf.openWorkflows)) return wf;
    // 部分版本 workflow 是独立 store
    const direct = pinia._s.get("workflow");
    if (direct && Array.isArray(direct.openWorkflows)) return direct;
  } catch (e) {}
  return null;
}

async function closeExtraWorkflows() {
  const store = getWorkflowStore();
  if (!store) return; // 拿不到 store 就跳过
  try {
    const open = store.openWorkflows || [];
    const active = store.activeWorkflow;
    // store 层的 closeWorkflow 只收 1 个参数，直接移除标签、不弹未保存确认框
    for (const wf of [...open]) {
      if (wf === active) continue;
      try { await store.closeWorkflow(wf); } catch (e) {}
    }
  } catch (e) {}
}


// 锁定画布交互：禁止移动节点、连线、增删；保留 widget 点击、滚轮缩放、拖视角
function lockCanvas() {
  const c = app.canvas;
  if (!c) return;
  c.allow_dragnodes = false;        // 不能移动节点
  c.allow_reconnect_links = false;  // 不能改连线
  c.allow_searchbox = false;        // 双击不弹搜索建节点
  c.allow_interaction = true;       // 保留 widget 交互
  c.read_only = false;              // read_only 会连 widget 一起锁，故不开
  c.connections_width = c.connections_width || 3;
  // 屏蔽右键增删菜单
  c.getCanvasMenuOptions = () => [];
  c.getNodeMenuOptions = () => [];
  c.getExtraMenuOptions = () => [];
  // 屏蔽双击节点/画布弹出的搜索建节点
  c.processNodeDblClick = () => {};
  c.showSearchBox = () => {};
  c.showConnectionMenu = () => {};
  // 任何已发起的连线都立即丢弃（双保险）
  if ("connecting_links" in c) c.connecting_links = null;
  c.connecting_node = null;
  // 屏蔽 Delete / Backspace 删节点
  const origKey = c.processKey ? c.processKey.bind(c) : null;
  c.processKey = function (e) {
    if (e.type === "keydown" && (e.key === "Delete" || e.key === "Backspace")) {
      return; // 吞掉删除键
    }
    if (origKey) return origKey(e);
  };
}

// 隐藏 ComfyUI 自带的菜单栏/侧边栏/工作流标签页/任务队列/底部控件，让画布只剩节点
function hideChrome() {
  const css = `
    /* 画布容器提为全屏覆盖层，盖住一切 chrome（含 Crystools 监视器、Manager、任务队列、
       底部运行栏等浮层）。容器内的 D站画廊 DOM widget 会一起置顶，正常显示。 */
    .graph-canvas-container {
      position: fixed !important; inset: 0 !important;
      width: 100vw !important; height: 100vh !important;
      z-index: 2147483000 !important; margin: 0 !important;
    }
    .litegraph { inset: 0 !important; }
    /* 明确隐藏已知 chrome（稳定 id/class，最高优先级，防 z-index 极高的浮层）。
       #comfyui-body-top 是顶栏菜单/工作流标签页/Manager 的 Teleport 目标，隐藏它=隐藏整个顶栏。 */
    #comfyui-body-top, #comfyui-body-bottom, #comfyui-body-left, #comfyui-body-right,
    .comfyui-body-top, .comfyui-body-bottom, .comfyui-body-left, .comfyui-body-right,
    .comfy-menu, .side-tool-bar-container, .side-tool-bar-end,
    #crystools-root, .crystools-root, .crystools-monitors-root, .crystools-monitors-container,
    .comfyui-menu, .actionbar, .p-toolbar, .graph-canvas-menu,
    .comfyui-queue-button, .queue-button-group, .p-toast, .p-overlay {
      display: none !important;
      visibility: hidden !important;
      pointer-events: none !important;
    }
    /* widget 弹出层必须盖在画布覆盖层之上。combo widget（lora_name/ckpt_name 等）的下拉
       是 litegraph 的 ContextMenu，挂在 body 上、自身 z-index 只有 9999，而画布容器是
       2147483000 —— 不提上来就会藏到画布底下，表现为「点了没反应、选不了模型」。
       这里用 CSS 兜第一帧，MutationObserver 里的 promotePopup 兜后续动态挂载。 */
    .litecontextmenu, .litegraph.litecontextmenu, .litemenubar-panel,
    .p-autocomplete-overlay, .graphdialog, .comfy-modal {
      z-index: 2147483400 !important;
      visibility: visible !important;
      pointer-events: auto !important;
    }
    /* 不在这里写 display —— litegraph 自己会用内联样式开关菜单显隐，
       CSS 里 !important 写死会把「关闭菜单」也一起破坏。
       误隐藏的恢复交给 JS 的 promotePopup（它只清掉我们自己加的内联 none）。 */
    /* 长按选择进度环 */
    #laf-ring { position: fixed; width: 56px; height: 56px; margin: -28px 0 0 -28px;
      pointer-events: none; z-index: 2147483600; }
    #laf-ring circle { fill: none; stroke-width: 5; }
    #laf-ring .bg { stroke: rgba(255,255,255,.25); }
    #laf-ring .fg { stroke: #3b82f6; stroke-linecap: round;
      transform: rotate(-90deg); transform-origin: 28px 28px;
      transition: stroke-dashoffset linear; }
  `;
  const el = document.createElement("style");
  el.textContent = css;
  document.head.appendChild(el);
  isolateCanvas();
  // ComfyUI 的面板（任务队列、资源监视器、顶栏等）是异步挂载的，
  // 用 MutationObserver 持续把"画布祖先链以外的兄弟元素"隐藏，
  // 完全不依赖会变化的哈希 class 名。
  try {
    const obs = new MutationObserver(() => isolateCanvas());
    obs.observe(document.body, { childList: true, subtree: true });
  } catch (e) {}
}

// 结构化隔离：保留画布容器整棵子树（含 D站画廊等自定义节点的 DOM widget 覆盖层），
// 隐藏 ComfyUI 的 chrome（顶栏/标签页/侧边栏/底栏/任务队列/资源监视器等）。
// 关键：ComfyUI 的 chrome 组件都是 #graph-canvas-container 的【同级兄弟】（都在 #vue-app 下），
// 所以直接隐藏画布容器的所有兄弟即可，不靠猜 class。用 important 防止 Vue 重渲染覆盖。
// 弹出层白名单：这些是「节点自身要用」的浮层，不是 chrome，隐藏了会让 widget 没法操作。
// 典型受害者：combo widget（lora_name/ckpt_name 等）的下拉是 litegraph 的 ContextMenu，
// 挂在 document.body 上 —— 既会被 isolateCanvas 当成兄弟隐藏，
// 又因自身 z-index 只有 9999（画布容器是 2147483000）而藏到画布底下。两个都得治。
const POPUP_SELECTOR = [
  ".litecontextmenu",          // litegraph combo/右键菜单（模型下拉就是它）
  ".litegraph.litecontextmenu",
  ".litemenubar-panel",
  ".p-autocomplete-overlay",   // 新前端的自动完成下拉
  ".p-tooltip",
  ".graphdialog",              // litegraph 数值/文本输入弹窗（双击 widget 改值）
  ".comfy-modal",
].join(",");

function isPopupLayer(el) {
  try { return !!(el && el.matches && el.matches(POPUP_SELECTOR)); }
  catch (e) { return false; }
}

// 把弹出层提到画布容器之上，否则它在画布覆盖层底下，看不见也点不到
const POPUP_Z = "2147483400";
function promotePopup(el) {
  try {
    // 只在需要时写：MutationObserver 每次 DOM 变动都会走到这里，无条件写会白白触发样式重算
    if (el.style.getPropertyValue("z-index") !== POPUP_Z) {
      el.style.setProperty("z-index", POPUP_Z, "important");
    }
    // 之前若被 isolateCanvas 误隐藏过，这里恢复（只清我们自己加的内联 none）
    if (el.style.getPropertyValue("display") === "none") {
      el.style.removeProperty("display");
    }
  } catch (e) {}
}

// 全文档扫一遍弹出层并提权。比在 hideEl 里对每个兄弟做 querySelector 便宜得多
// （那会在每次 DOM 变动 × 每个兄弟上重复遍历子树），也能覆盖「弹出层挂在已隐藏容器里」
// 这种 hideEl 白名单救不回来的情况。
function promoteAllPopups() {
  let list;
  try { list = document.querySelectorAll(POPUP_SELECTOR); }
  catch (e) { return; }
  // 不往祖先链上放开：实测 litegraph 的 ContextMenu 是 append 到 document.body
  // （见前端 api chunk 里的 `c.body.append(i)`），即画布容器的同级兄弟，
  // hideEl 的白名单已经能保住它。盲目放开祖先反而会把本该隐藏的 chrome 露出来。
  for (const el of list) promotePopup(el);
}

function hideEl(el) {
  if (!el || el.tagName === "STYLE" || el.tagName === "SCRIPT") return;
  if (el.id === "laf-ring") return;
  // widget 的下拉/输入弹窗不能隐藏，否则模型选不了
  if (isPopupLayer(el)) { promotePopup(el); return; }
  if (el.style.getPropertyValue("display") !== "none") {
    el.style.setProperty("display", "none", "important");
  }
}

// 直接对“真实解析到的”画布容器元素套全屏覆盖样式（不靠猜 id/class 选择器，
// 这是之前 CSS 始终不生效的根因：容器是 #graph-canvas-container(id)，而 CSS 写的是 .类）。
function promoteCanvas(container) {
  if (!container) return;
  const s = container.style;
  s.setProperty("position", "fixed", "important");
  s.setProperty("inset", "0", "important");
  s.setProperty("left", "0", "important");
  s.setProperty("top", "0", "important");
  s.setProperty("width", "100vw", "important");
  s.setProperty("height", "100vh", "important");
  s.setProperty("margin", "0", "important");
  s.setProperty("z-index", "2147483000", "important");
}

function isolateCanvas() {
  const canvas =
    document.getElementById("graph-canvas") ||
    document.querySelector("canvas.litegraph") ||
    document.querySelector("canvas");
  if (!canvas) return;
  const container =
    canvas.closest(".graph-canvas-container") ||
    document.getElementById("graph-canvas-container") ||
    canvas.parentElement ||
    canvas;
  promoteCanvas(container);
  // 沿祖先链：每一层都隐藏"不含画布"的兄弟（chrome 都在这些兄弟里）。
  // 含画布的分支保留，使画布容器整棵子树（含 DOM widget）完整显示。
  let node = container;
  while (node && node.parentElement && node !== document.body) {
    const parent = node.parentElement;
    for (const sib of Array.from(parent.children)) {
      if (sib === node) continue;
      if (sib.contains(canvas)) continue; // 别误伤画布所在分支
      hideEl(sib);
    }
    node = parent;
  }
  // 隔离完再把 widget 弹出层放开并提到画布之上（顺序要在后面，否则又被上面这轮隐藏掉）
  promoteAllPopups();
}

// ===== 长按选择整个节点 =====
const PRESS_MS = 650;       // 长按时长
const MOVE_TOL = 6;         // 超过该位移视为拖视角，取消选择
const selectedIds = new Set();
let ring = null, pressTimer = null, pressNode = null, pressStart = null;
let selectEnabled = false;  // 仅编辑页（全量选节点模式）启用长按选择；对话页关闭

function makeRing() {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.id = "laf-ring";
  svg.setAttribute("viewBox", "0 0 56 56");
  const r = 24, C = 2 * Math.PI * r;
  const bg = document.createElementNS(NS, "circle");
  bg.setAttribute("class", "bg"); bg.setAttribute("cx", "28"); bg.setAttribute("cy", "28"); bg.setAttribute("r", r);
  const fg = document.createElementNS(NS, "circle");
  fg.setAttribute("class", "fg"); fg.setAttribute("cx", "28"); fg.setAttribute("cy", "28"); fg.setAttribute("r", r);
  fg.setAttribute("stroke-dasharray", String(C));
  fg.setAttribute("stroke-dashoffset", String(C));
  svg.appendChild(bg); svg.appendChild(fg);
  svg._fg = fg; svg._C = C;
  document.body.appendChild(svg);
  return svg;
}

function showRing(x, y) {
  if (!ring) ring = makeRing();
  ring.style.left = x + "px";
  ring.style.top = y + "px";
  ring.style.display = "block";
  const fg = ring._fg, C = ring._C;
  fg.style.transition = "none";
  fg.setAttribute("stroke-dashoffset", String(C));
  // 强制重排后再启动动画 → 进度环平滑填满
  void ring.getBoundingClientRect();
  fg.style.transition = `stroke-dashoffset ${PRESS_MS}ms linear`;
  fg.setAttribute("stroke-dashoffset", "0");
}

function hideRing() {
  if (ring) ring.style.display = "none";
}

function highlight(node, on) {
  if (on) {
    if (node.__laf_orig === undefined) node.__laf_orig = { c: node.color, b: node.bgcolor };
    node.color = "#1d4ed8";
    node.bgcolor = "#1e3a8a";
  } else if (node.__laf_orig) {
    node.color = node.__laf_orig.c;
    node.bgcolor = node.__laf_orig.b;
    delete node.__laf_orig;
  }
  app.graph.setDirtyCanvas(true, true);
}

function cancelPress() {
  if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
  pressNode = null; pressStart = null;
  hideRing();
}

function commitSelect() {
  hideRing();
  const n = pressNode;
  pressTimer = null; pressNode = null; pressStart = null;
  if (!n) return;
  if (selectedIds.has(n.id)) return; // 已选过
  selectedIds.add(n.id);
  highlight(n, true);
  toParent("node_selected", { id: n.id, title: n.title || n.type || String(n.id), type: n.type || "" });
}

// 把客户端坐标换算成画布图坐标
function clientToGraph(clientX, clientY) {
  const c = app.canvas;
  if (!c || !c.canvas) return null;
  const rect = c.canvas.getBoundingClientRect();
  const ds = c.ds || {};
  const scale = ds.scale || 1;
  const off = ds.offset || [0, 0];
  return [(clientX - rect.left) / scale - off[0], (clientY - rect.top) / scale - off[1]];
}

// 命中测试找到节点
function nodeAtClient(clientX, clientY) {
  const g = clientToGraph(clientX, clientY);
  if (!g) return null;
  return app.graph.getNodeOnPos ? app.graph.getNodeOnPos(g[0], g[1], app.graph._nodes) : null;
}

// 命中测试是否点在某个组的标题栏（拖标题会移动整组节点，需禁止）
function onGroupTitle(clientX, clientY) {
  const g = clientToGraph(clientX, clientY);
  if (!g) return false;
  const groups = (app.graph && app.graph._groups) || [];
  const fontSize = 24; // litegraph 组标题默认高度量级
  for (const grp of groups) {
    const b = grp._bounding || grp.bounding;
    if (!b) continue;
    const titleH = (grp.titleHeight || fontSize) + 4;
    // 标题栏 = 组顶部一条；点在组范围内且落在标题高度内即判定为标题拖拽
    if (g[0] >= b[0] && g[0] <= b[0] + b[2] && g[1] >= b[1] && g[1] <= b[1] + titleH) {
      return true;
    }
  }
  return false;
}

// 是否点在某节点的输入/输出槽上（用于阻止拖拽改连线，但放行 widget）
function isOnSlot(node, clientX, clientY) {
  if (!node || !node.getSlotInPosition) return false;
  const g = clientToGraph(clientX, clientY);
  if (!g) return false;
  try {
    return !!node.getSlotInPosition(g[0], g[1]);
  } catch (e) {
    return false;
  }
}

// 全局守卫：在 document 捕获阶段彻底拦截右键/中键与右键菜单（新版 Vue 菜单在 document 上监听，
// 仅在 canvas 上拦截会漏，导致首次右键弹出菜单→黑屏）。只装一次。
let guardsInstalled = false;
function installGlobalGuards() {
  if (guardsInstalled) return;
  guardsInstalled = true;
  const swallow = (e) => { e.preventDefault(); e.stopImmediatePropagation(); };
  document.addEventListener("contextmenu", swallow, true);
  document.addEventListener("auxclick", (e) => { if (e.button !== 0) swallow(e); }, true);
  document.addEventListener("pointerdown", (e) => { if (e.button === 2 || e.button === 1) swallow(e); }, true);
  document.addEventListener("mousedown", (e) => { if (e.button === 2 || e.button === 1) swallow(e); }, true);
  document.addEventListener("pointerup", (e) => { if (e.button === 2 || e.button === 1) swallow(e); }, true);
}

function bindLongPress() {
  installGlobalGuards();
  const el = app.canvas && app.canvas.canvas;
  if (!el || el.__laf_bound) return;
  el.__laf_bound = true;

  // 全部用「捕获阶段」处理：litegraph 自己的 pointerdown 在冒泡阶段会 stopImmediatePropagation，
  // 放冒泡阶段我们的选择逻辑永远收不到事件 → 长按选不中。捕获阶段先于 litegraph 执行。
  el.addEventListener(
    "pointerdown",
    (e) => {
      if (e.button === 2 || e.button === 1) { e.preventDefault(); e.stopImmediatePropagation(); return; }
      if (e.button !== 0) return;
      // 点在组标题栏 → 阻止拖动整组节点（会弄乱工作流）
      if (onGroupTitle(e.clientX, e.clientY)) { e.preventDefault(); e.stopImmediatePropagation(); return; }
      const node = nodeAtClient(e.clientX, e.clientY);
      if (node && isOnSlot(node, e.clientX, e.clientY)) {
        // 点在连线槽上 → 阻止 litegraph 发起连线（widget 不在槽上，照常可点）
        e.preventDefault(); e.stopImmediatePropagation(); return;
      }
      if (!node) return;            // 空白处：放行，litegraph 处理平移
      if (!selectEnabled) return;   // 对话页禁用长按选择（无进度环）
      pressNode = node;
      pressStart = { x: e.clientX, y: e.clientY };
      showRing(e.clientX, e.clientY);
      pressTimer = setTimeout(commitSelect, PRESS_MS);
    },
    true,
  );
  el.addEventListener(
    "pointermove",
    (e) => {
      if (!pressStart) return;
      const dx = e.clientX - pressStart.x, dy = e.clientY - pressStart.y;
      if (Math.hypot(dx, dy) > MOVE_TOL) cancelPress(); // 拖动 = 平移视角，取消选择
    },
    true,
  );
  window.addEventListener("pointerup", cancelPress, true);
  window.addEventListener("pointercancel", cancelPress, true);
}

// ===== 载入工作流 + 守护（防止 ComfyUI 会话恢复覆盖我们指定的工作流）=====
let watchdog = null;
let soloNode = null;       // 单节点模式下当前展示的节点
let resizeHooked = false;  // 是否已挂 resize 重新贴合

async function applyLoad(workflow, exposedIds) {
  try { app.graph.clear(); } catch (e) {}
  selectedIds.clear();
  // 载图：某些扩展(reroute 等)可能在 loadGraphData 内部抛/挂起，加超时兜底，
  // 避免整个 load 处理永不返回（父页面会一直等到 30s 超时）。graphToPrompt 只需图载入即可。
  const loadOnce = async () => {
    try { await app.loadGraphData(workflow, true, false); }
    catch (e) { await app.loadGraphData(workflow); }
  };
  try {
    await Promise.race([
      loadOnce(),
      new Promise((_, rej) => setTimeout(() => rej(new Error("loadGraphData timeout")), 12000)),
    ]);
  } catch (e) {
    console.error("[laf_lock] loadGraphData failed/timeout:", e);
  }
  // 以下均为外观/锁定处理，任一步抛错都不能影响 loaded 回传与后续 graphToPrompt
  try { keepOnly(exposedIds); } catch (e) {}
  // exposedIds 为空=编辑页全量选节点模式→启用长按选择；非空=对话页提取模式→禁用
  selectEnabled = !(exposedIds && exposedIds.length);
  try { lockCanvas(); } catch (e) {}
  try { bindLongPress(); } catch (e) {}
  // 单节点模式：精确铺满该节点，并回传节点宽高比供父页面调整 iframe 尺寸
  if (exposedIds && exposedIds.length === 1) {
    const n = app.graph.getNodeById(Number(exposedIds[0]));
    if (n) {
      soloNode = n;
      // DOM widget（如 D站画廊图片网格）是异步渲染的，节点高度会在渲染后变化。
      // 多次延迟重测并回传真实尺寸，确保父页面 iframe 比例最终对齐节点。
      try { measureAndReport(n); } catch (e) {}
      for (const ms of [150, 400, 800, 1500]) {
        setTimeout(() => { if (soloNode === n) { try { measureAndReport(n); } catch (e) {} } }, ms);
      }
      // iframe 尺寸被父页面按比例调整后会触发 resize，需重新贴合
      if (!resizeHooked) {
        resizeHooked = true;
        window.addEventListener("resize", () => {
          if (soloNode) setTimeout(() => fitNode(soloNode), 50);
        });
      }
    }
  } else {
    soloNode = null;
    // 首次打开时 iframe 刚插入 DOM，画布 getBoundingClientRect 可能还是 0/旧值，
    // 此时 fitAll 会按错误尺寸缩放 → 节点比例崩坏、视图错位（无法拖动）。
    // 立即 fit 一次，再延迟多次重试，等布局稳定后以正确尺寸重新贴合。
    try { fitAll(); } catch (e) {}
    for (const ms of [120, 350, 700, 1200]) {
      setTimeout(() => { if (!soloNode) { try { fitAll(); } catch (e) {} } }, ms);
    }
    // iframe 尺寸变化（父页面布局完成 / 窗口缩放）后重新贴合
    if (!resizeHooked) {
      resizeHooked = true;
      window.addEventListener("resize", () => {
        if (!soloNode) setTimeout(() => { try { fitAll(); } catch (e) {} }, 50);
      });
    }
  }
  // 关掉 loadGraphData 新建的多余 "Unsaved Workflow" 标签，只留当前；再隔离一次 chrome
  try { await closeExtraWorkflows(); } catch (e) {}
  try { isolateCanvas(); } catch (e) {}
}

// 测量单节点真实尺寸（含 DOM widget 撑开后的高度），重新贴合并回传给父页面
function measureAndReport(n) {
  if (!n) return;
  fitNode(n);
  const w = (n.size && n.size[0]) || 200;
  const h = ((n.size && n.size[1]) || 100) + 30; // +标题高度
  toParent("node_size", { id: n.id, w, h });
}

// 把视图精确对准单个节点，使其铺满画布（留少量边距）
function fitNode(n) {
  const c = app.canvas;
  if (!c || !c.ds || !c.canvas) return;
  const pad = 12;
  // 用 CSS 像素（getBoundingClientRect），不要用 canvas.width/height（含 devicePixelRatio
  // 倍率，Windows 125% 缩放下会偏大 → 节点放太大显示不全）
  const rect = c.canvas.getBoundingClientRect();
  const cw = rect.width || c.canvas.clientWidth || c.canvas.width;
  const ch = rect.height || c.canvas.clientHeight || c.canvas.height;
  const nw = (n.size && n.size[0]) || 200;
  const nh = ((n.size && n.size[1]) || 100) + 30; // 含标题
  const nx = n.pos ? n.pos[0] : 0;
  const ny = (n.pos ? n.pos[1] : 0) - 30;          // 标题在 pos 上方
  const scale = Math.min((cw - pad * 2) / nw, (ch - pad * 2) / nh);
  try {
    c.ds.scale = scale;
    // 让节点居中
    c.ds.offset[0] = (cw / scale - nw) / 2 - nx;
    c.ds.offset[1] = (ch / scale - nh) / 2 - ny;
    c.setDirty(true, true);
  } catch (e) {}
}

// 缩放/平移使所有节点恰好可见
function fitAll() {
  const c = app.canvas;
  if (!c) return;
  // 画布尺寸未稳定（首次插入 DOM，rect 还是 0/极小）时不 fit，避免按错误尺寸缩放导致比例崩坏。
  if (c.canvas) {
    const rect = c.canvas.getBoundingClientRect();
    if ((rect.width || 0) < 50 || (rect.height || 0) < 50) return;
  }
  try {
    if (typeof c.fitViewToContent === "function") { c.fitViewToContent(); return; }
    if (c.ds && typeof c.ds.fitToBounds === "function" && app.graph) {
      const b = app.graph.getBounding ? app.graph.getBounding() : null;
      if (b) { c.ds.fitToBounds(b, { animate: false }); return; }
    }
  } catch (e) {}
  c.setDirty(true, true);
}

// 工作流内容签名：节点 id+类型 的有序串，用于判断当前画布是否就是我们指定的工作流。
// 比单纯比节点数更可靠（两个工作流节点数相同也能区分）。
function sigFromWorkflow(workflow, exposedIds) {
  if (!workflow || !Array.isArray(workflow.nodes)) return "";
  let nodes = workflow.nodes;
  if (exposedIds && exposedIds.length) {
    const keep = new Set(exposedIds.map(String));
    nodes = nodes.filter((n) => keep.has(String(n.id)));
  }
  return nodes
    .map((n) => `${n.id}:${n.type || n.class_type || ""}`)
    .sort()
    .join("|");
}

function sigFromGraph() {
  const ns = app.graph && app.graph._nodes ? app.graph._nodes : [];
  return ns
    .map((n) => `${n.id}:${n.type || n.comfyClass || ""}`)
    .sort()
    .join("|");
}

function startWatchdog(workflow, exposedIds) {
  if (watchdog) { clearInterval(watchdog); watchdog = null; }
  const targetSig = sigFromWorkflow(workflow, exposedIds);
  if (!targetSig) return; // 无法生成签名则不守护
  let tries = 0;
  watchdog = setInterval(async () => {
    tries++;
    // 用户正在改 widget（下拉菜单/输入弹窗开着）→ 这一轮不重载。
    // 守护窗口有 8 秒，期间 applyLoad 会 graph.clear() 重载整图，
    // 正在选的模型会被连带丢掉 —— 表现为「选了没生效」。
    if (document.querySelector(POPUP_SELECTOR)) return;
    // 当前画布内容与目标工作流不一致 → 被 ComfyUI 会话恢复覆盖了 → 重新载入
    if (sigFromGraph() !== targetSig) {
      await applyLoad(workflow, exposedIds);
    }
    if (tries >= 16) { clearInterval(watchdog); watchdog = null; } // ~8s 守护窗口后停止
  }, 500);
}

if (LOCK) {
  app.registerExtension({
    name: "LocalAIFrontend.Lock",
    async setup() {
      hideChrome();
      installGlobalGuards();
      // 收父窗口消息
      window.addEventListener("message", async (ev) => {
        const d = ev.data;
        if (!d || d.target !== "laf_lock") return;
        if (d.type === "ping_ready") {
          // 父页面没收到 ready（极少数竞态）→ 主动补回一次
          toParent("ready", {});
          return;
        }
        if (d.type === "load") {
          // payload: { workflow, exposedIds }
          // 固定显示选定工作流，并启动守护防止 ComfyUI 恢复上次会话工作流覆盖
          // 大图载入可能在某扩展里抛异常，必须兜底——无论成败都回 loaded，否则父页面死等超时
          try {
            await applyLoad(d.payload.workflow, d.payload.exposedIds);
          } catch (e) {
            console.error("[laf_lock] applyLoad error:", e);
          }
          try { startWatchdog(d.payload.workflow, d.payload.exposedIds); } catch (e) { /* ignore */ }
          toParent("loaded", { ok: true });
        } else if (d.type === "deselect") {
          // payload: { id } —— 从右侧列表移除某节点选择
          const n = app.graph.getNodeById(Number(d.payload.id));
          if (n) highlight(n, false);
          selectedIds.delete(Number(d.payload.id));
          selectedIds.delete(String(d.payload.id));
        } else if (d.type === "reselect") {
          // payload: { id } —— 重新进入画布时恢复已选节点高亮，并回传真实标题
          const n = app.graph.getNodeById(Number(d.payload.id));
          if (n) {
            selectedIds.add(n.id);
            highlight(n, true);
            toParent("node_title", { id: n.id, title: n.title || n.type || String(n.id) });
          }
        } else if (d.type === "request_graph") {
          toParent("graph", { workflow: serialize() });
        } else if (d.type === "request_api_prompt") {
          // 用 ComfyUI 自带的 graphToPrompt() 生成 API 格式（与原生“运行”完全一致，
          // 正确处理 bypass/reroute/widget 顺序/seed 控件/自定义节点 JS 映射，避免自写转换器出错被 /prompt 拒绝）
          try {
            // 等一拍让自定义节点 JS（如 D站画廊）重建隐藏 widget（selection_data）后再序列化。
            // 注意：隐藏 iframe 在屏幕外，requestAnimationFrame 会被浏览器冻结 → 不能用它等待，
            // 否则永远不返回、api_prompt 永不回传。用 setTimeout（后台 iframe 仍会触发）。
            await new Promise((r) => setTimeout(r, 400));
            const p = await app.graphToPrompt();
            toParent("api_prompt", { output: p.output, workflow: serialize(), ok: true });
          } catch (e) {
            toParent("api_prompt", { ok: false, error: String(e && e.message || e), workflow: serialize() });
          }
        } else if (d.type === "request_node") {
          // payload: { nodeId } —— 单节点卡：回传该节点的最新参数，供父页面合并进完整工作流
          const g = serialize();
          let node = null;
          if (g && Array.isArray(g.nodes)) {
            node = g.nodes.find((n) => String(n.id) === String(d.payload.nodeId)) || null;
          }
          toParent("node_values", { nodeId: d.payload.nodeId, node });
        } else if (d.type === "set_widget") {
          // payload: { nodeId, widgetName, value } —— AI 生成提示词注入
          const n = app.graph.getNodeById(Number(d.payload.nodeId));
          if (n && n.widgets) {
            const w = n.widgets.find((w) => w.name === d.payload.widgetName);
            if (w) {
              w.value = d.payload.value;
              app.graph.setDirtyCanvas(true, true);
            }
          }
          toParent("widget_set", { ok: !!n });
        } else if (d.type === "request_node_schema") {
          // payload: { nodeIds, includeNeighbors } —— 回传每个节点的输入口结构与可填 widget；
          // includeNeighbors 时附带直接上/下游邻居，供 AI 改左侧接线内容（改上游 widget）
          toParent("node_schema", { nodes: collectNodeSchema(d.payload.nodeIds, d.payload.includeNeighbors) });
        } else if (d.type === "apply_ops") {
          // payload: { ops:[{node_id,input,action,value,image_name}] } —— 按 AI 计划改画布
          const results = applyOps(d.payload.ops || []);
          app.graph.setDirtyCanvas(true, true);
          toParent("ops_applied", { results });
        }
      });
      // 锁一次，载图后还会再锁
      lockCanvas();
      toParent("ready", {});
    },
    async nodeCreated() {
      // 任何时刻新建节点后都重新上锁（防止内部逻辑解锁）
      lockCanvas();
    },
  });
}

// 载入工作流：兼容两种格式。
// - UI 格式(有 nodes 数组)：走 app.loadGraphData；
// - API prompt 格式({id:{class_type,inputs}})：AI 搭建/骨架输出的是这种。不同 ComfyUI 版本载入
//   API 格式的入口不一(app.loadApiJson / workflowService.loadApiJson / loadGraphData 自动识别都可能)，
//   逐个尝试；全都没有则用 LiteGraph 手动反建节点+连线，不依赖任何版本特定方法。
function isApiPromptFormat(wf) {
  return wf && typeof wf === "object" && !Array.isArray(wf.nodes)
    && Object.values(wf).some((v) => v && typeof v === "object" && "class_type" in v);
}

// 用 LiteGraph 直接把 API prompt 反建成画布节点（版本无关兜底）。
function buildGraphFromApiPrompt(apiPrompt) {
  const LG = window.LiteGraph;
  if (!LG || typeof LG.createNode !== "function") {
    console.error("[laf] LiteGraph 不可用，无法手动反建 API 图");
    return;
  }
  app.graph.clear();
  const idMap = {};   // apiId -> LGraphNode
  // 先建所有节点并填 widget 值
  for (const [aid, def] of Object.entries(apiPrompt)) {
    if (!def || !def.class_type) continue;
    const node = LG.createNode(def.class_type);
    if (!node) { console.warn("[laf] 未知节点类型:", def.class_type); continue; }
    app.graph.add(node);
    idMap[aid] = node;
    // 铺开一点，避免全叠在原点
    const i = Object.keys(idMap).length;
    node.pos = [80 + (i % 6) * 240, 80 + Math.floor(i / 6) * 200];
    // 填 widget 字面值（非连线的 inputs）
    const inputs = def.inputs || {};
    for (const [name, val] of Object.entries(inputs)) {
      if (Array.isArray(val) && val.length === 2) continue; // 连线，稍后接
      const w = (node.widgets || []).find((w) => w.name === name);
      if (w) { try { w.value = val; } catch (e) {} }
    }
  }
  // 再接线：inputs 里 [上游apiId, 输出序号] → connect
  for (const [aid, def] of Object.entries(apiPrompt)) {
    const node = idMap[aid];
    if (!node) continue;
    const inputs = def.inputs || {};
    for (const [name, val] of Object.entries(inputs)) {
      if (!(Array.isArray(val) && val.length === 2)) continue;
      const up = idMap[String(val[0])];
      if (!up) continue;
      const inSlot = (node.inputs || []).findIndex((s) => s.name === name);
      if (inSlot < 0) continue;
      try { up.connect(Number(val[1]) || 0, node, inSlot); } catch (e) {}
    }
  }
  app.graph.setDirtyCanvas(true, true);
}

function graphNodeCount() {
  try { return (app.graph && app.graph._nodes ? app.graph._nodes.length : 0); } catch (e) { return 0; }
}

async function loadAnyFormat(workflow) {
  if (isApiPromptFormat(workflow)) {
    // 依次尝试原生入口（app.loadApiJson 在多数版本存在）
    try {
      if (typeof app.loadApiJson === "function") {
        await app.loadApiJson(workflow, "ai_workflow.json");
        // 某些版本 loadApiJson 会静默 no-op（不抛错但画布仍空）→ 转手动反建兜底
        if (graphNodeCount() > 0) return;
        console.warn("[laf] loadApiJson 未产出节点，转手动反建");
      }
    } catch (e) { console.warn("[laf] 原生 loadApiJson 失败，转手动反建:", e); }
    // 兜底：LiteGraph 手动反建（版本无关）
    buildGraphFromApiPrompt(workflow);
    return;
  }
  // UI 格式
  try { await app.loadGraphData(workflow, true, false); }
  catch (e) { await app.loadGraphData(workflow); }
}

// full 模式：完整功能 ComfyUI + 父页面双向同步（AI 搭工作流右侧画布）。
// 不 hideChrome、不 lockCanvas、不 installGlobalGuards —— 保留全部原生交互。
if (FULL) {
  // 是否已发生过 load：一旦父页面载入骨架/工作流，就停掉后续的"初始延迟清空"，
  // 否则初始 clear 序列(最长 1.8s)会把刚载入的骨架清掉 —— 表现为"节点先出现又消失"。
  let didLoad = false;
  app.registerExtension({
    name: "LocalAIFrontend.Full",
    async setup() {
      window.addEventListener("message", async (ev) => {
        const d = ev.data;
        if (!d || d.target !== "laf_lock") return;
        if (d.type === "ping_ready") {
          toParent("ready", {});
        } else if (d.type === "load") {
          didLoad = true;  // 之后不再自动清空
          // 载入整图（不裁剪、不锁定）；大图/扩展异常兜底，无论成败都回 loaded 防父页死等
          // 兼容 API prompt 格式（AI/骨架输出），用 loadAnyFormat 分流。
          try {
            app.graph.clear();
            await Promise.race([
              loadAnyFormat(d.payload.workflow),
              new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), 12000)),
            ]);
          } catch (e) {
            console.error("[laf_full] load error:", e);
          }
          try { await closeExtraWorkflows(); } catch (e) {}  // 只留一个工作流标签，别堆叠
          toParent("loaded", { ok: true });
        } else if (d.type === "request_graph") {
          toParent("graph", { workflow: serialize() });
        } else if (d.type === "request_api_prompt") {
          // 与锁定模式一致：用 ComfyUI 自带 graphToPrompt() 生成 API 格式，处理自定义节点映射
          try {
            await new Promise((r) => setTimeout(r, 300));
            const p = await app.graphToPrompt();
            toParent("api_prompt", { output: p.output, workflow: serialize(), ok: true });
          } catch (e) {
            toParent("api_prompt", { ok: false, error: String(e), workflow: serialize() });
          }
        } else if (d.type === "clear_graph") {
          // 父页面请求清空画布（AI 搭工作流从空白起步）
          try { app.graph.clear(); app.graph.setDirtyCanvas(true, true); } catch (e) {}
          try { await closeExtraWorkflows(); } catch (e) {}  // 顺带只留一个标签
          toParent("cleared", { ok: true });
        }
      });
      // 初始清空：ComfyUI 会自动恢复上次会话/默认工作流，AI 搭流须从空白画布起步。
      // 恢复是异步的，单次 clear 会被随后的恢复覆盖 → 多次延迟清，覆盖恢复窗口。
      // 同时关掉恢复出来的多余工作流标签，保证右侧画布只有一个标签页。
      // 关键：一旦已发生 load（用户在这 1.8s 内选了骨架），立刻停手，别清掉刚载入的图。
      for (const ms of [0, 200, 500, 1000, 1800]) {
        setTimeout(() => {
          if (didLoad) return;  // 已载入骨架/工作流，不再自动清空
          try { app.graph.clear(); app.graph.setDirtyCanvas(true, true); } catch (e) {}
          closeExtraWorkflows().catch(() => {});
        }, ms);
      }
      toParent("ready", {});
    },
  });
}

