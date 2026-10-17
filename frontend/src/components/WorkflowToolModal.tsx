// WorkflowToolModal.tsx — 画布「工作流工具」双栏编辑器
//
// /w 选模板后画布创建 workflow-tool 节点，双击进本 modal：
//   左栏：复用对话里的 WorkflowCard 组件（行为与对话流完全一致：
//         节点参数图 / 选择完毕 / AI 编排 / 运转工作流 / 更改）
//   右栏：AI 编排对话框（调 workflowPorts API → 自动填入节点参数）
//
// 运转工作流在本 modal 内走 submitGraph → subscribeProgress → runtime 守望 → 归档。
// 2026-09-15 C4-A 第 4 步：pending 生命周期与轮询归 `WorkflowGenerationRuntime` 独占（与画布/对话同一 threadId 单例），
// 归档复用对话的 `finalizeGeneration`（`onFinalizeWorkflow`），终态经 `canvasRunEffects` 翻成
// 「通知 + 清本地 React 状态 + 派发 laf-canvas-wf-done」——与画布 `runWorkflowTask` 同一张表。
// 产出入库后由对话链派发 `laf-generation-saved`（不再由本组件盲派），让画布/资产网格刷新新增节点。
// 完整闭环不依赖切回对话。

import { useEffect, useRef, useState } from "react";
import { Sparkles, X } from "lucide-react";
import { WorkflowCard } from "./WorkflowCard";
import { getTemplateRaw } from "../api/workflows";
import { workflowPorts, type PortOp, type PortsPlan } from "../api/ai";
import type { GenResult } from "../api/comfyui";
import { threadKey, type PendingGeneration } from "../lib/workflowGenerationRuntime";
import { runCanvasWorkflow } from "../lib/runCanvasWorkflow";
import { activeChatModel, type Settings } from "../stores/settings";
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
  /**
   * 归档回调：与画布共用对话同一条 finalize 链（`useChatSession.finalizeGeneration`）。
   * 2026-09-15 C4-A 第 4 步：本组件原先直调 `api/comfyui.finalizeGeneration`（零判重 + 假「稍后自动入库」）。
   */
  onFinalizeWorkflow: (result: GenResult, pending: PendingGeneration) => Promise<boolean>;
  /** 画布工具卡「选择完毕」入口：打开即自动执行抓取（与对话模式卡片直接抓取对齐） */
  autoConfirm?: boolean;
}

interface ChatMsg {
  role: "user" | "ai";
  text: string;
}

export function WorkflowToolModal({ node: nodeProp, repoId, settings, onClose, onUpdate, onNotify, onGenerated, onFinalizeWorkflow, autoConfirm = false }: Props) {
  // P17（2026-09-15）：runtime 的线程键统一走 `threadKey`。本组件的 `repoId` prop 由画布透传
  // （`ChatView` 传 `repo?.id || ""`），首页会是 `""`，而约定键是 `"home"` ⇒ 否则弹窗在首页运转的任务
  // 会落在 `getRuntime("")` 这个孤立实例上（与画布/对话的判重域分裂，同一任务可被归档两次）。
  // `repoId` 仍原样传给 `workflowPorts` 等 API（「空」即「无仓库」）；仅线程键与 `owner` 归一。
  const threadId = threadKey(repoId);
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
  // 模板已选节点（exposed_ids）：确认时随 onUpdate 写回画布，供封面徽标/节点预览用
  // （修复：模板确认后封面徽标一直显示 0 —— 此前确认回写不含 wfExposedIds）
  const [exposedIds, setExposedIds] = useState<string[]>([]);
  useEffect(() => {
    const tid = node.templateId || "";
    if (!tid) { setExposedIds([]); return; }
    let alive = true;
    getTemplateRaw(tid).then((r) => { if (alive) setExposedIds(r.exposed_ids || []); }).catch(() => {});
    return () => { alive = false; };
  }, [node.templateId]);

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

  // ===== 运转工作流（编排链归 lib/runCanvasWorkflow；本组件只给 input + sink）=====
  // runTaskId：画布「生成中」占位节点的任务 id。提交后画布创建占位节点并订阅实时进度，
  // 运转完毕（入库/失败）占位节点被生成内容节点替换/清除。
  // capturedOverride：弹窗「运转工作流」点击同拍刚抓到的图（node prop 可能尚未回流）。
  //
  // 2026-09-15 C4-A 第 4 步 + **P10**：原先本组件自持 `pollWorkflowResult` + `finally` 兜底，
  // 并直调 `api/comfyui.finalizeGeneration`（零判重、乱派 `laf-generation-saved`、入库失败还谎报
  // 「稍后自动入库」）；而这条编排链又与画布 `runWorkflowTask` **逐句重复**（两个属主）。
  // 现在 pending/轮询归 `getRuntime(threadId)` 独占，**链本身**归 `lib/runCanvasWorkflow.ts`，
  // 归档复用对话链，终态翻成事件用画布同一张表。
  // ⚠ 本组件有一条「先 `onClose()` 再 `handleRun()`」的路径（二级弹窗「运转工作流」）——弹窗卸载后
  //   observer 仍在闭包里继续跑，所以收工**不能依赖组件存活**：卸载后本地 setState 是 no-op，
  //   但派发事件 / onNotify / onGenerated 必须照常（跨组件契约，漏一条占位就永久残留）。
  const handleRun = (runTaskId?: string, capturedOverride?: unknown): void => {
    const dispatchDone = (): void => {
      if (!runTaskId) return;
      try { window.dispatchEvent(new CustomEvent("laf-canvas-wf-done", { detail: { taskId: runTaskId } })); } catch { /* ignore */ }
    };
    if (wfRunningRef.current) {
      // 已有一个运转在跑：不重复提交。但调用方**已经**派发过 `laf-canvas-wf-run`（画布占位与
      // 对话框进度条的条目已建），必须回一个 done，否则该条目永久残留（画布自己看不出来）。
      // 注：这条守卫是弹窗**专有**（画布没有），与下面四个开关同属刻意差异。
      dispatchDone();
      return;
    }
    runCanvasWorkflow({
      captured: capturedOverride ?? node.wfCaptured,
      comfyuiUrl: settings.comfyuiUrl,
      templateName: node.templateName,
      wfDraft: node.wfDraft,
      threadId,
      outputDir: settings.outputDir,
      runId: runTaskId,
      finalizeWorkflow: onFinalizeWorkflow,
      // 占位节点显示名：弹窗两栏都能查（API 抓取图 → UI 草稿图），故传自己的实现
      nodeLabel: wfNodeLabel,
      // ── 以下四个是**两条路径的刻意差异**（源码锁 `runCanvasWorkflow.contract.test.ts` 钉住），
      //    与画布那侧取值相反。「对齐」任意一个都是行为回归。──────────────────────
      // 提交前预检 ComfyUI 是否已启动（未启动 → 专有文案早退）
      precheckComfyStatus: true,
      // 只判 captured；地址为空时由 comfyStatus 失败来报「ComfyUI 未启动」
      requireComfyUrl: false,
      // 订阅后补发一次「进度未知」：弹窗进度条先落到「未知」
      emitInitialProgress: true,
      missingCapturedText: "还没抓到画布内容，请先在左栏点「选择完毕」。",
    }, {
      // 弹窗提示走 onNotify（info 样式，与改前一致）；kind 不使用
      notify: (text) => onNotify(text),
      dispatchDone,
      // 回读对话快照：产出已入库，画布据此投影出生成内容节点
      reloadFromSnapshot: () => { onGenerated?.(); },
      // 同步置忙：`wfRunningRef` 若只靠 useEffect 同步，同一 tick 内的第二次点击仍会穿过守卫 ⇒
      // 两次 `getRuntime().start` 是两个不同 promptId 的 pending，后端幂等键也救不了（会真落两份）。
      beginBusy: () => {
        wfRunningRef.current = true;
        setWfRunning(true);
        setWfProgress(null);
        setWfNode(undefined);
      },
      // 清本地 React 状态（弹窗内进度条/按钮态）：**所有**终态都要清，含 timed_out
      // （任务转 ComfyUI 后台继续跑，但编辑器不该再显示「运转中」；改前 finally 也是无条件清）。
      teardown: () => {
        wfRunningRef.current = false;
        setWfRunning(false);
        setWfProgress(null);
        setWfNode(undefined);
      },
      onProgressTick: (progress, node) => { setWfProgress(progress); setWfNode(node); },
      onNodeTick: (node) => setWfNode(node),
    });
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
    <div className="modal-mask">
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
        <div className="modal-mask" style={{ zIndex: 10001 }}>
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
                  onUpdate({ wfDraft: d, wfCaptured: g, wfConfirmed: true, wfExposedIds: exposedIds });
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
                  onUpdate({ wfDraft: d, wfCaptured: g, wfConfirmed: false, wfExposedIds: exposedIds });
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