// WorkflowToolModal.tsx — 画布「工作流工具」双栏编辑器
//
// /w 选模板后画布创建 workflow-tool 节点，双击进本 modal：
//   左栏：复用对话里的 WorkflowCard 组件（行为与对话流完全一致：
//         节点参数图 / 选择完毕 / AI 编排 / 运转工作流 / 更改）
//   右栏：AI 编排对话框（调 workflowPorts API → 自动填入节点参数）
//
// 运转工作流在本 modal 内走 submitGraph → 轮询 → finalizeGeneration
// 入库到当前作品 generation_store，dispatchEvent('laf-generation-saved')
// 让画布自动刷新新增节点。完整闭环不依赖切回对话。

import { useEffect, useRef, useState } from "react";
import { Sparkles, X } from "lucide-react";
import { WorkflowCard } from "./WorkflowCard";
import { workflowPorts, type PortOp, type PortsPlan } from "../api/ai";
import {
  submitGraph, finalizeGeneration, comfyStatus, interruptComfy,
} from "../api/comfyui";
import { subscribeProgress } from "../lib/comfyProgress";
import { pollWorkflowResult, trackCanvasWorkflow, untrackCanvasWorkflow } from "../lib/workflowGenerationRuntime";
import { workflowGenMetadata } from "../lib/regeneration";
import { activeChatModel, resolvedEmbedModel, type Settings } from "../stores/settings";
import type { CanvasNode } from "../lib/canvasRuntime";

interface Props {
  node: CanvasNode;
  /** 当前画布作品 id（用于 finalize 入库） */
  repoId: string;
  settings: Settings;
  onClose: () => void;
  onUpdate: (updates: Partial<CanvasNode>) => void;
  onNotify: (msg: string) => void;
  /** 运转完成入库后回调（上层用它刷新对话消息，使新产出进入对话→画布） */
  onGenerated?: () => void;
  /** 画布工具卡「选择完毕」入口：打开即自动执行抓取（与对话模式卡片直接抓取对齐） */
  autoConfirm?: boolean;
}

interface ChatMsg {
  role: "user" | "ai";
  text: string;
}

export function WorkflowToolModal({ node: nodeProp, repoId, settings, onClose, onUpdate, onNotify, onGenerated, autoConfirm = false }: Props) {
  // nodeProp 是打开弹窗时的快照；onUpdate 写回画布是异步的（setRfNodes→投影→prop 回流）。
  // 若弹窗一直读旧快照，「更改」后左栏仍显示旧状态（退出重进才生效）。用活状态 + prop 同步。
  const [node, setNode] = useState(nodeProp);
  useEffect(() => { setNode(nodeProp); }, [nodeProp]);
  const [chatMsgs, setChatMsgs] = useState<ChatMsg[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [wfRunning, setWfRunning] = useState(false);
  const wfRunningRef = useRef(false);
  const [wfProgress, setWfProgress] = useState<number | null>(null);   // 弹窗内实时进度（0-100）
  const [wfNode, setWfNode] = useState<string | undefined>(undefined); // 当前执行节点 id
  const [refreshKey, setRefreshKey] = useState(0);  // 强制重渲 WorkflowCard（onDone 触发）
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // 选择完毕 → LoRA 弹窗后 → 第二级弹窗：运转/更改/关闭
  const [postConfirm, setPostConfirm] = useState(false);
  const postConfirmDraftRef = useRef<unknown>(null);
  const postConfirmGraphRef = useRef<unknown>(null);

  useEffect(() => { wfRunningRef.current = wfRunning; }, [wfRunning]);

  // 节点 id → 显示名（对齐对话模式 nodeLabel：class_type (#id)）
  // captured 是 API 格式（{id:{class_type}}）；wfDraft 是 UI 格式（{nodes:[{id,type}]}）——两者都要能查
  const wfNodeLabel = (id: string): string => {
    try {
      const cap = node.wfCaptured as Record<string, { class_type?: string }> | null;
      const c = cap?.[id]?.class_type;
      if (c) return `${c} (#${id})`;
      const draft = node.wfDraft as { nodes?: Array<{ id?: unknown; type?: string }> } | null;
      const dn = (draft?.nodes || []).find((n) => String(n.id) === String(id));
      if (dn?.type) return `${dn.type} (#${id})`;
      return `节点 #${id}`;
    } catch { return `节点 #${id}`; }
  };

  // ===== 构造 WorkflowCard 需要的伪 message（与对话里的 msg 接口对齐） =====
  const pseudoMsg = {
    id: `wftool-pseudo-${node.id}`,
    role: "assistant" as const,
    text: `工作流工具卡：${node.templateName || "未命名模板"}（双击画布上的工具卡打开编辑器）`,
    workflow: {
      templateId: node.templateId || "",
      templateName: node.templateName || "未命名模板",
      draftGraph: node.wfDraft,
      capturedGraph: node.wfCaptured,
      done: !!node.wfConfirmed,
      capturedLora: undefined,
    },
  };

  // ===== 运转工作流（复刻 useChatSession.runWorkflow 核心闭环） =====
  // runTaskId：画布「生成中」占位节点的任务 id。提交后画布创建占位节点并订阅实时进度，
  // 运转完毕（入库/失败）占位节点被生成内容节点替换/清除。
  // capturedOverride：弹窗「运转工作流」点击同拍刚抓到的图（node prop 可能尚未回流）。
  const handleRun = async (runTaskId?: string, capturedOverride?: unknown) => {
    if (wfRunningRef.current) return;
    const captured = capturedOverride ?? node.wfCaptured;
    if (!captured) {
      onNotify("还没抓到画布内容，请先在左栏点「选择完毕」。");
      if (runTaskId) {
        try { window.dispatchEvent(new CustomEvent("laf-canvas-wf-done", { detail: { taskId: runTaskId } })); } catch { /* ignore */ }
      }
      return;
    }
    setWfRunning(true);
    setWfProgress(null);
    setWfNode(undefined);
    let stopProg: (() => void) | null = null;
    let runPromptId = "";      // 提交成功的 prompt_id：后台活动标记用（finally 里移除）
    let keepActivity = false;  // still_running 时保留后台活动标记（任务仍在 ComfyUI 后台跑）
    try {
      const st = await comfyStatus(settings.comfyuiUrl);
      if (!st.running) {
        onNotify("ComfyUI 未启动。请先启动 ComfyUI，或在「设置」填写路径由工具自动拉起。");
        if (runTaskId) {
          try { window.dispatchEvent(new CustomEvent("laf-canvas-wf-done", { detail: { taskId: runTaskId } })); } catch { /* ignore */ }
        }
        return;
      }
      const r = await submitGraph(captured, settings.comfyuiUrl);
      runPromptId = r.prompt_id || "";
      onNotify(`已提交到 ComfyUI（prompt_id: ${runPromptId}，${r.node_count} 个节点），正在运转工作流…`);
      // 记入后台活动（laf_pending_gen_<repoId>）：SupportWidget 面板显示「出图中」；
      // 本弹窗自持轮询，runtime 只做标记，结束（finally）时移除。
      trackCanvasWorkflow(repoId, runPromptId, settings.comfyuiUrl, node.templateName || "工作流生成");
      // 把 graph 带给画布占位节点：节点名才能显示 class_type（如 KSamplerAdvanced (#15)）而非裸 id
      if (runTaskId) {
        try {
          window.dispatchEvent(new CustomEvent("laf-canvas-wf-run-graph", {
            detail: { runId: runTaskId, graph: captured },
          }));
        } catch { /* ignore */ }
      }
      // 占位节点显示真实提示词/节点名用（对齐 runWorkflowTask 路径）
      if (runTaskId) {
        try { window.dispatchEvent(new CustomEvent("laf-canvas-wf-run-graph", { detail: { runId: runTaskId, graph: captured } })); } catch { /* ignore */ }
      }
      // 实时进度：直连 ComfyUI /ws，同步驱动弹窗内进度条 + 画布「生成中」节点 + 对话框进度条
      stopProg = subscribeProgress(settings.comfyuiUrl || "", r.prompt_id || "", {
        onProgress: (pct, p) => {
          setWfProgress(pct);
          setWfNode(p.node);
          if (runTaskId) {
            window.dispatchEvent(new CustomEvent("laf-canvas-wf-progress", {
              detail: { taskId: runTaskId, promptId: r.prompt_id || "", progress: pct, node: p.node || "", nodeLabel: p.node ? wfNodeLabel(p.node) : "", templateName: node.templateName || "" },
            }));
          }
        },
        onNode: (nid) => {
          setWfNode(nid);
          if (runTaskId) {
            window.dispatchEvent(new CustomEvent("laf-canvas-wf-progress", {
              detail: { taskId: runTaskId, promptId: r.prompt_id || "", node: nid, nodeLabel: wfNodeLabel(nid), templateName: node.templateName || "" },
            }));
          }
        },
      });
      if (runTaskId) {
        window.dispatchEvent(new CustomEvent("laf-canvas-wf-progress", {
          detail: { taskId: runTaskId, promptId: r.prompt_id || "", progress: null, node: "", templateName: node.templateName || "" },
        }));
      }
      // 轮询拿图：复用 pollSchedule 合同（图片 20 分钟 / 视频 60 分钟），不再硬编码 240 秒。
      const outcome = await pollWorkflowResult(r.prompt_id || "", settings.comfyuiUrl || "", "image");
      if (outcome.kind === "stalled") {
        // 队列卡死：清理坏死任务（清队列+中断）后按失败处理（2026-08-29 用户需求）。
        await interruptComfy(settings.comfyuiUrl || "", r.prompt_id || "").catch(() => undefined);
        onNotify(`运转失败：${outcome.error}`);
        if (runTaskId) {
          try { window.dispatchEvent(new CustomEvent("laf-canvas-wf-done", { detail: { taskId: runTaskId } })); } catch { /* 忽略 */ }
        }
        return;
      }
      if (outcome.kind === "failed") {
        onNotify(`运转失败：${outcome.error}`);
        if (runTaskId) {
          try { window.dispatchEvent(new CustomEvent("laf-canvas-wf-done", { detail: { taskId: runTaskId } })); } catch { /* ignore */ }
        }
        return;
      }
      if (outcome.kind === "still_running") {
        // ComfyUI 仍在后台运转：不擅自删除「生成中」节点，保留占位并告知用户。
        keepActivity = true; // 任务还在跑，后台活动标记保留
        onNotify("运转仍在进行中（已超过预期时长）。画布会保留「生成中」节点，请稍后在 ComfyUI 控制台查看或重新运转。");
        return; // 不派发 done，保留占位节点
      }
      const result = outcome.result;
      if ((!result.images || result.images.length === 0)
          && (!result.videos || result.videos.length === 0)
          && (!result.audios || result.audios.length === 0)) {
        onNotify("运转完成但未拿到图片/视频/音频（可能输出节点未正确配置），请到 ComfyUI 控制台查看。");
        if (runTaskId) {
          try { window.dispatchEvent(new CustomEvent("laf-canvas-wf-done", { detail: { taskId: runTaskId } })); } catch { /* ignore */ }
        }
        return;
      }
      // 入库到 generation_store → 派发 laf-generation-saved 让画布自动刷新
      // 元数据与对话模式同源（workflowGenMetadata）：真实提示词 + 主模型 + LoRA，
      // 供画布详情面板「提示词/模型/LoRA」展示。captured 提取为空时回退 wfDraft（UI 格式）。
      const meta = workflowGenMetadata(node.templateName || "", captured, node.wfDraft);
      const embed = resolvedEmbedModel(settings);
      const chat = activeChatModel(settings);
      try {
        await finalizeGeneration({
          threadId: repoId,
          repoId,
          promptId: r.prompt_id || "",
          prompt: meta.prompt || node.templateName || "工作流生成",
          images: result.images,
          videos: result.videos || [],
          audios: result.audios || [],
          outputDir: settings.outputDir,
          comfyuiUrl: settings.comfyuiUrl,
          embed: {
            baseUrl: embed.baseUrl || chat.baseUrl,
            apiKey: embed.apiKey || chat.apiKey,
            modelName: embed.modelName,
          },
          chat: {
            baseUrl: chat.baseUrl,
            apiKey: chat.apiKey,
            modelName: chat.modelName,
          },
          regeneration: undefined,
          target: undefined,
          templateName: meta.templateName,
          modelName: meta.modelName,
          loraNames: meta.loraNames,
        });
        onNotify(`运转完成，${result.images.length} 张图${result.videos.length ? `、${result.videos.length} 个视频` : ""}${result.audios.length ? `、${result.audios.length} 个音频` : ""}已入库。`);
      } catch (e) {
        // 入库失败但图已生成——提示并派发事件让画布刷新（generation_store 由 laf-runtime 后台轮询也会补上）
        onNotify(`运转完成（入库失败：${(e as Error).message}），稍后会自动入库。`);
      }
      try { window.dispatchEvent(new CustomEvent("laf-generation-saved")); } catch { /* ignore */ }
      onGenerated?.();
      if (runTaskId) {
        try { window.dispatchEvent(new CustomEvent("laf-canvas-wf-done", { detail: { taskId: runTaskId } })); } catch { /* ignore */ }
      }
    } catch (e) {
      onNotify(`运转失败：${(e as Error).message}`);
      if (runTaskId) {
        try { window.dispatchEvent(new CustomEvent("laf-canvas-wf-done", { detail: { taskId: runTaskId } })); } catch { /* ignore */ }
      }
    } finally {
      if (!keepActivity && runPromptId) untrackCanvasWorkflow(repoId, runPromptId); // 结束移除后台活动标记
      setWfRunning(false);
      setWfProgress(null);
      setWfNode(undefined);
      stopProg?.();
    }
  };

  // ===== 从 ComfyUI 工作流 JSON 提取输入口结构（供 workflowPorts API 用） =====
  const extractNodeSchemas = (graph: unknown): unknown[] => {
    const g = graph as Record<string, unknown> | undefined;
    const nodes = (g?.nodes ?? g?.["nodes"]) as Array<Record<string, unknown>> | undefined;
    if (!nodes || !Array.isArray(nodes)) return [];
    return nodes.map((n) => ({
      id: n.id ?? n["id"] ?? "",
      type: n.type ?? n["type"] ?? "",
      widgets: n.widgets_values ?? n["widgets_values"] ?? [],
    }));
  };

  // ===== 把 AI 编排返回的 ops 写到 draft graph 的 widgets_values =====
  const applyOpsToDraft = (draft: unknown, ops: PortOp[]): unknown => {
    if (!draft) return draft;
    const g = JSON.parse(JSON.stringify(draft)) as Record<string, unknown>;
    const nodes = (g.nodes ?? g["nodes"]) as Array<Record<string, unknown>> | undefined;
    if (!nodes || !Array.isArray(nodes)) return draft;
    for (const op of ops) {
      const target = nodes.find((n) => (n.id ?? n["id"]) === op.node_id);
      if (!target) continue;
      const widgets = (target.widgets_values ?? target["widgets_values"]) as unknown[];
      if (!widgets || !Array.isArray(widgets)) continue;
      if (op.action === "set_widget") {
        // input 可能是 widget 名或数字索引
        const idx = typeof op.input === "string" && /^\d+$/.test(op.input)
          ? parseInt(op.input, 10) : -1;
        if (idx >= 0 && idx < widgets.length) {
          widgets[idx] = op.value;
        }
      }
      // set_image / replace_output 需要更深层的 iframe 交互，本版先只做 set_widget
    }
    return g;
  };

  // ===== AI 编排：调 workflowPorts API → 展示计划 → 自动写入 draft graph =====
  const handleOrchestrateChat = async () => {
    const text = chatInput.trim();
    if (!text || !node.wfDraft) return;
    setChatBusy(true);
    setChatInput("");
    setChatMsgs((prev) => [...prev, { role: "user", text }]);
    try {
      const chat = activeChatModel(settings);
      const schemas = extractNodeSchemas(node.wfDraft);
      const plan: PortsPlan = await workflowPorts(
        text, 0, schemas, chat.modelName, chat, false, "", "", repoId,
      );
      if (plan.is_orchestration === false) {
        setChatMsgs((prev) => [...prev, {
          role: "ai",
          text: plan.summary || "AI 判断这不是编排需求，请直接描述你想要的画面效果。",
        }]);
        return;
      }
      // 展示 AI 计划
      const opsList = plan.ops.map((op, i) =>
        `  ${i + 1}. 节点 ${op.node_id}：${op.action} ${op.input ?? ""} → ${op.value ?? ""}（${op.reason ?? ""}）`
      ).join("\n");
      setChatMsgs((prev) => [...prev, {
        role: "ai",
        text: `${plan.summary}\n\n操作清单：\n${opsList}`,
      }]);
      // 自动写入 draft graph
      if (plan.ops.length > 0) {
        const updated = applyOpsToDraft(node.wfDraft, plan.ops);
        onUpdate({ wfDraft: updated });
        onNotify(`AI 编排完成，已自动填入 ${plan.ops.length} 个参数。`);
      }
    } catch (e) {
      setChatMsgs((prev) => [...prev, {
        role: "ai",
        text: `编排失败：${(e as Error).message}。请重试或手动调参。`,
      }]);
    } finally {
      setChatBusy(false);
      setTimeout(() => scrollRef.current?.scrollTo({ top: 1e6, behavior: "smooth" }), 30);
    }
  };

  return (
    <div className="modal-mask" onClick={onClose}>
      <div
        className="modal"
        style={{
          width: "92vw", maxWidth: 1200, height: "86vh", display: "flex",
          flexDirection: "column", padding: 0,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 标题栏 */}
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "12px 16px", borderBottom: "1px solid var(--border)",
        }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 14 }}>
              🛠️ 工作流工具 · {node.templateName || "未命名"}
            </h3>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
              左栏：参数选择图与运转；右栏：AI 编排对话
            </div>
          </div>
          <button className="icon-btn" style={{ background: "transparent", color: "var(--text)" }} onClick={onClose}>
            <X size={18} />
          </button>
        </div>

        {/* 双栏 */}
        <div style={{ flex: 1, display: "flex", gap: 0, overflow: "hidden" }}>
          {/* 左栏：复用 WorkflowCard（key 强制重渲以响应 wfDraft/wfCaptured/wfConfirmed 变化）。
              plain：只放实际节点与操作，去掉对话消息形态（头像/标题/引导/AI编排按钮——右栏即 AI 编排）。
              宽度固定 2/3 语义（minWidth 520 保证节点参数可读）。 */}
          <div style={{
            flex: "2 1 0", minWidth: 520, display: "flex", flexDirection: "column",
            borderRight: "1px solid var(--border)",
          }}>
            <div style={{ flex: 1, overflowY: "auto", padding: 12 }}>
            {/* 运转工作流实时进度条（弹窗内显示，不用依赖被 modal-mask 盖住的对话框进度条）。
                语义对齐对话模式：当前节点的采样步进度（每节点 0→100，切换回零），不是整体任务百分比。 */}
            {wfRunning && (
              <div className="wf-progress-wrap" style={{ maxWidth: "none", width: "100%", marginBottom: 8 }}>
                <div className="wf-progress" title={wfProgress != null ? `当前节点采样步进度 ${wfProgress}%（节点切换时回零重走）` : "工作流运转中（排队/初始化）"}>
                  <div className="wf-progress-bar" style={{ width: `${wfProgress ?? 0}%` }} />
                  <span className="wf-progress-txt">{wfProgress != null ? `${wfProgress}%` : "运转中…"}</span>
                </div>
                <span className="wf-progress-node" title="当前执行节点，若长时间不变可能卡住">
                  {wfNode ? `${wfNodeLabel(wfNode)} · ` : ""}{node.templateName || "工作流"}
                </span>
              </div>
            )}
            <WorkflowCard
              key={`${node.id}:${node.wfDraft ? "d" : "-"}:${node.wfCaptured ? "c" : "-"}:${node.wfConfirmed ? "ok" : "-"}:${refreshKey}`}
              msg={pseudoMsg}
              comfyUrl={settings.comfyuiUrl || ""}
              chatModel={{
                baseUrl: activeChatModel(settings).baseUrl,
                apiKey: activeChatModel(settings).apiKey,
                modelName: activeChatModel(settings).modelName,
              }}
              isBusy={wfRunning}
              uploading={wfRunning}
              autoConfirm={autoConfirm}
              plain
              onDraft={(d) => onUpdate({ wfDraft: d })}
              onDone={(d, g) => {
                // 选择完毕 → LoRA 弹窗确认后 → 二级弹窗：运转/更改/关闭
                postConfirmDraftRef.current = d;
                postConfirmGraphRef.current = g;
                setPostConfirm(true);
              }}
              onReopen={() => onUpdate({ wfConfirmed: false })}
              onRun={() => {
                // 左栏「运转工作流」：同样创建画布「生成中」节点 + 对话框进度条。
                // captured 用最新一次「选择完毕」抓取结果（node prop 在弹窗内不会回流）
                const runId = `wfrun-${crypto.randomUUID().slice(0, 8)}`;
                window.dispatchEvent(new CustomEvent("laf-canvas-wf-run", {
                  detail: { runId, toolNodeId: node.id, templateName: node.templateName || "工作流" },
                }));
                handleRun(runId, postConfirmGraphRef.current ?? node.wfCaptured);
              }}
              onNotify={onNotify}
              onOrchestrate={() => {
                // 「AI 编排」：把焦点给到右栏输入框，用户补充需求后即可发送
                setChatMsgs((prev) => [...prev, {
                  role: "ai",
                  text: "已切换到 AI 编排模式。请在下方补充需求（例如「金发红裙的赛博朋克少女，户外夜景」），我会给出输入口的参数建议。",
                }]);
                setTimeout(() => {
                  const el = document.getElementById("wftool-chat-input");
                  if (el) (el as HTMLTextAreaElement).focus();
                }, 50);
              }}
            />
            </div>
            {/* 「选择完毕」按钮已内置在左栏 WorkflowCard 节点列表下方，不再重复渲染底部按钮 */}
          </div>

          {/* 右栏：AI 编排对话框 */}
          <div style={{
            flex: "1 1 0", minWidth: 280, display: "flex", flexDirection: "column",
            padding: 12, background: "rgba(255,255,255,0.02)",
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
              <Sparkles size={14} color="var(--primary, #3b82f6)" />
              <span style={{ fontSize: 12, fontWeight: 500 }}>AI 编排</span>
            </div>
            {/* 消息流 */}
            <div ref={scrollRef} style={{
              flex: 1, overflowY: "auto", background: "rgba(0,0,0,0.18)", borderRadius: 8,
              padding: 10, marginBottom: 8, minHeight: 200,
            }}>
              {chatMsgs.length === 0 ? (
                <div style={{ color: "var(--text-muted)", fontSize: 12, textAlign: "center", padding: 24, lineHeight: 1.6 }}>
                  告诉 AI 你想画什么。<br />例：「金发红裙的赛博朋克少女，户外夜景」
                </div>
              ) : chatMsgs.map((m, i) => (
                <div key={i} style={{
                  marginBottom: 10, padding: 10,
                  background: m.role === "user" ? "rgba(59,130,246,0.18)" : "rgba(255,255,255,0.04)",
                  borderRadius: 8,
                }}>
                  <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>
                    {m.role === "user" ? "你" : "AI"}
                  </div>
                  <div style={{ fontSize: 13, whiteSpace: "pre-wrap", wordBreak: "break-word", lineHeight: 1.5 }}>
                    {m.text}
                  </div>
                </div>
              ))}
            </div>
            {/* 输入框 + 发送 */}
            <textarea
              id="wftool-chat-input"
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  handleOrchestrateChat();
                }
              }}
              placeholder="Ctrl/⌘+Enter 发送"
              rows={3}
              style={{
                padding: 10, borderRadius: 6, resize: "none",
                background: "var(--input-bg, rgba(0,0,0,0.2))",
                color: "var(--text)", border: "1px solid var(--border)",
                fontSize: 13, fontFamily: "inherit",
              }}
            />
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button
                className="btn primary"
                onClick={handleOrchestrateChat}
                disabled={chatBusy || !chatInput.trim()}
                style={{ flex: 1 }}
              >
                <Sparkles size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />
                {chatBusy ? "AI 编排中…" : "AI 编排"}
              </button>
            </div>
          </div>
        </div>
      </div>
      {/* 选择完毕 → LoRA 确认后 → 第二级弹窗：运转/更改/关闭 */}
      {postConfirm && (
        <div className="modal-mask" onClick={() => setPostConfirm(false)} style={{ zIndex: 10001 }}>
          <div className="modal" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
            <h3>参数已确认</h3>
            <p style={{ fontSize: 13, color: "var(--text-muted)", margin: "8px 0" }}>
              工作流节点参数已锁定。是否运转工作流？
            </p>
            <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
              <button
                className="btn primary"
                style={{ flex: 1 }}
                onClick={() => {
                  const d = postConfirmDraftRef.current;
                  const g = postConfirmGraphRef.current;
                  setPostConfirm(false);
                  onUpdate({ wfDraft: d, wfCaptured: g, wfConfirmed: true });
                  // 运转工作流 → 跳转画布页面：画布创建「生成中」节点，运转完毕替换为生成内容节点；
                  // 对话框下方同步显示进度条。编辑器收工，任务在画布后台继续。
                  const runId = `wfrun-${crypto.randomUUID().slice(0, 8)}`;
                  // 画布创建「生成中」占位节点（运转完毕被生成内容节点替换）
                  window.dispatchEvent(new CustomEvent("laf-canvas-wf-run", {
                    detail: { runId, toolNodeId: node.id, templateName: node.templateName || "工作流" },
                  }));
                  onClose();
                  handleRun(runId, g);
                }}
              >
                运转工作流
              </button>
              <button
                className="btn"
                style={{ flex: 1 }}
                onClick={() => {
                  const d = postConfirmDraftRef.current;
                  const g = postConfirmGraphRef.current;
                  setPostConfirm(false);
                  // 「更改」→ 留在本节点页面、回到未选择状态（保留已填参数，恢复节点预览可继续调参）
                  onUpdate({ wfDraft: d, wfCaptured: g, wfConfirmed: false });
                }}
              >
                更改
              </button>
              <button
                className="btn"
                onClick={() => {
                  setPostConfirm(false);
                  if (autoConfirm) onClose();
                }}
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}