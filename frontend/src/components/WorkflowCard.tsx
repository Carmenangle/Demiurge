import { useEffect, useRef, useState } from "react";
import { Sparkles, Workflow } from "lucide-react";
import { getTemplateRaw } from "../api/workflows";
import { comfyStatus } from "../api/comfyui";
import type { ChatMessage } from "../types/chat";
import { fmtOpResults } from "../lib/opResults";
import { lockUrl, postToFrame, isLafMessageFromStrict } from "../lib/lafLock";
import {
  canonicalWorkflowDraft, mergeRequestedNodes, preservesWorkflowTopology,
} from "../lib/workflowDraft";
import { listLoras } from "../api/loras";
import { ConfirmModal } from "./Modal";
import {
  buildWorkflowLoraProposal, workflowLoraNames, type WorkflowLoraProposal,
} from "../lib/workflowLoraData";
import { captureWorkflowApiPrompt, requestFrameMessage } from "../lib/workflowCapture";

// 工作流卡：选中模板后把所选节点逐个嵌入锁定的真实 ComfyUI 画布调参，
// 「选择完毕」经 ComfyUI 原生 graphToPrompt 抓取合法 API prompt；「AI 编排」触发上层规划。
export function WorkflowCard({
  msg,
  comfyUrl,
  chatModel,
  onDraft,
  onDone,
  onReopen,
  onRun,
  onNotify,
  onOrchestrate,
  isBusy = false,
  uploading = false,
  autoConfirm = false,
  plain = false,
}: {
  msg: ChatMessage;
  comfyUrl: string;
  chatModel: { baseUrl: string; apiKey: string; modelName: string };
  onDraft: (draftGraph: unknown) => void;
  onDone: (draftGraph: unknown, capturedGraph: unknown) => void;
  onReopen: () => void;
  onRun: () => void;
  onNotify: (text: string) => void;
  onOrchestrate: () => void;
  isBusy?: boolean;
  uploading?: boolean;  // 工作流上传/提交阶段（点击后 → submitGraph 返回前）
  /** 画布工具卡「选择完毕」入口：模板载入完成后自动执行一次 handleDone（与对话模式卡片直接抓取对齐） */
  autoConfirm?: boolean;
  /** 纯节点模式（编辑器左栏用）：去掉对话消息形态（头像/标题/引导说明/AI编排按钮），只留节点+操作 */
  plain?: boolean;
}) {
  const wf = msg.workflow!;
  const [fullWorkflow, setFullWorkflow] = useState<any>(null); // 完整原始工作流
  const [nodeIds, setNodeIds] = useState<string[]>([]);        // 选中节点（按顺序）
  const [loadErr, setLoadErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingLora, setPendingLora] = useState<{
    draft: any;
    graph: any;
    proposal: WorkflowLoraProposal;
  } | null>(null);

  // 取模板原始工作流 + 已选节点顺序
  useEffect(() => {
    getTemplateRaw(wf.templateId)
      .then((r) => {
        setFullWorkflow(canonicalWorkflowDraft(r.workflow, wf.draftGraph));
        setNodeIds(r.exposed_ids || []);
      })
      .catch((e) => setLoadErr((e as Error).message));
  }, [wf.templateId, wf.draftGraph]);

  // 「选择完毕」：逐节点抓取最新参数 → 合并进完整工作流 → 用 ComfyUI 自带 graphToPrompt
  // 生成 API prompt（与原生"运行"一致，避免自写转换器出错）→ 存为 capturedGraph
  const handleDone = async (ops?: any[]) => {
    if (!fullWorkflow) return;
    setBusy(true);
    // 抓取总超时兜底：requestFrameMessage(3s)/captureWorkflowApiPrompt(30s) 各有内建超时，
    // 但 listLoras() 等 apiGet 无超时——后端接口挂起或 iframe 异常会永久「抓取参数中」。
    // 60s 强制报错并释放 busy，绝不无限等待。
    const guard = setTimeout(() => {
      setBusy(false);
      onNotify("抓取参数超时（60 秒）。请确认 ComfyUI 正在运行、后端未卡死后重试。");
    }, 60000);
    try {
      const base = fullWorkflow;
      // 早期机制：逐节点从各自独立 iframe（laf-node-*）request_node 取最新参数。
      const values = await Promise.all(nodeIds.map((nid) => {
        const frame = (document.getElementById(`laf-node-${msg.id}-${nid}`) as HTMLIFrameElement | null)?.contentWindow;
        return requestFrameMessage<{ nodeId: string; node: any }>({
          frameWindow: frame, comfyUrl,
          requestType: "request_node", expectedType: "node_values",
          payload: { nodeId: nid }, timeoutMs: 3000,
        }).then((v) => v && String(v.nodeId) === String(nid) ? v : null);
      }));
      const merged = mergeRequestedNodes(base, values) as any;
      setFullWorkflow(merged);
      onDraft(merged);

      // 把最新完整 UI 草稿交给 ComfyUI 原生转换；失败也不会回滚上面的 draft。
      // 自写转换器无法还原自定义 JS 节点的 widget 映射（如 D站画廊的 selection_data），
      // 一旦回退会提交错误 prompt → 出图链断裂。所以原生转换失败就报错让用户重试，绝不静默回退。
      // ops 非空时：在全图 iframe 载入后先执行 AI 的输入口操作（含新建 LoadImage/连线），再抓取。
      const { prompt: apiPrompt, workflow: capturedDraft, opResults } = await captureWorkflowApiPrompt({
        workflow: merged, comfyUrl, ops,
      });
      if (!preservesWorkflowTopology(merged, capturedDraft)) {
        onNotify("捕获到的是单节点编辑画布，已阻止其覆盖完整工作流。请重新点「选择完毕」。");
        return;
      }
      const finalDraft = capturedDraft;
      setFullWorkflow(finalDraft);
      onDraft(finalDraft);
      if (!apiPrompt) {
        onNotify("用 ComfyUI 原生转换工作流超时/失败，请重试「选择完毕」（首次需等 ComfyUI 在后台载入完成）。");
        return; // 不存、不标记完成，避免提交错误的手写转换结果
      }
      if (ops && ops.length) {
        const okN = (opResults || []).filter((r: any) => r.ok).length;
        onNotify(`AI 已写入 ${okN}/${ops.length} 个输入口：\n${fmtOpResults(opResults || [])}\n参数已确认，直接输入 /s 出图。`);
      }
      const selectedLoras = workflowLoraNames(apiPrompt);
      if (selectedLoras.length > 0) {
        try {
          const loraItems = (await listLoras()).items;
          const proposal = buildWorkflowLoraProposal(apiPrompt, loraItems);
          if (proposal) {
            setPendingLora({ draft: finalDraft, graph: apiPrompt, proposal });
            return;
          }
          onNotify(`检测到 LoRA：${selectedLoras.join("、")}，但没有对应的 LoRA 数据保存记录，已保留当前参数。`);
        } catch (error) {
          onNotify(`检测到 LoRA，但读取 LoRA 数据保存失败，已保留当前参数：${(error as Error).message}`);
        }
      }
      onDone(finalDraft, apiPrompt);
    } catch (e) {
      onNotify(`抓取参数失败：${(e as Error).message}`);
    } finally {
      clearTimeout(guard);
      setBusy(false);
    }
  };

  // autoConfirm（画布工具卡「选择完毕」）：模板载入完成后自动执行一次 handleDone，防重复触发。
  const handleDoneRef = useRef(handleDone);
  handleDoneRef.current = handleDone;
  const autoFiredRef = useRef(false);
  useEffect(() => {
    if (!autoConfirm || !fullWorkflow || wf.done || busy) return;
    if (autoFiredRef.current) return;
    autoFiredRef.current = true;
    // 等一拍让 WorkflowCard 自身 state 稳定（nodeIds 等），再触发抓取
    const timer = setTimeout(() => handleDoneRef.current(), 200);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoConfirm, fullWorkflow]);

  const keepCurrentLoraData = () => {
    const pending = pendingLora;
    setPendingLora(null);
    if (pending) onDone(pending.draft, pending.graph);
  };

  const applySavedLoraData = async () => {
    const pending = pendingLora;
    if (!pending) return;
    if (pending.proposal.ops.length === 0) {
      setPendingLora(null);
      onDone(pending.draft, pending.graph);
      return;
    }
    setBusy(true);
    try {
      const captured = await captureWorkflowApiPrompt({
        workflow: pending.draft, comfyUrl, ops: pending.proposal.ops,
      });
      if (!captured.prompt) {
        onNotify("LoRA 数据写入工作流失败，请重试；当前参数尚未覆盖。");
        return;
      }
      if (!preservesWorkflowTopology(pending.draft, captured.workflow)) {
        onNotify("LoRA 写入返回了残缺画布，已保留完整工作流，请重试。");
        return;
      }
      const finalDraft = captured.workflow || pending.draft;
      setFullWorkflow(finalDraft);
      onDraft(finalDraft);
      setPendingLora(null);
      onNotify(
        `已应用 LoRA 数据：覆盖 ${pending.proposal.weightChanges} 个权重，`
        + `更新 ${pending.proposal.promptChanges} 个正向提示词输入。`,
      );
      onDone(finalDraft, captured.prompt);
    } catch (error) {
      onNotify(`LoRA 数据写入失败：${(error as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  // 监听 App 层「应用计划后自动选择完毕」请求：仅响应针对本卡的事件。
  // detail.ops 为 AI 计划的输入口操作，在全图隐藏 iframe 载入后执行，再 graphToPrompt 抓参。
  useEffect(() => {
    const onFinish = (e: Event) => {
      const detail = (e as CustomEvent).detail as { cardId: string; ops?: any[] };
      if (detail?.cardId === msg.id && !wf.done && !busy) handleDone(detail.ops);
    };
    window.addEventListener("laf-finish-card", onFinish);
    return () => window.removeEventListener("laf-finish-card", onFinish);
  }, [msg.id, wf.done, busy, fullWorkflow, nodeIds]);

  return (
    <div className={plain ? "workflow-plain" : "msg-bot"}>
      {!plain && (
        <div className="bot-avatar">
          <Workflow size={18} />
        </div>
      )}
      <div className="bot-content" style={{ width: "100%" }}>
        {!plain && (
          <div style={{ marginBottom: 10 }}>
            <strong>工作流：{wf.templateName}</strong>
            {wf.done && <span style={{ color: "#3a9e5b", fontSize: 12, marginLeft: 8 }}>已确认</span>}
          </div>
        )}

        {loadErr ? (
          <p style={{ color: "#d9534f", fontSize: 13 }}>
            载入失败：{loadErr}（需先在「ComfyUI 节点面板」启动 ComfyUI）
          </p>
        ) : !fullWorkflow ? (
          <p style={{ color: "var(--text-muted)", fontSize: 13 }}>正在载入节点…</p>
        ) : nodeIds.length === 0 ? (
          <p style={{ color: "#c98a1a", fontSize: 13 }}>
            该模板没有选择任何节点。请回模板编辑页用「ComfyUI 界面模式」长按选择节点后保存。
          </p>
        ) : (
          <>
            {/* 未确认时才渲染节点；确认后卸载，省 ComfyUI 性能（点「更改」再加载）。
                早期机制：每节点一个独立 ComfyUI iframe（keepOnly 单节点 + node_size 自适应），
                纵向一口气展示全部选中节点；在各节点画布手动调好参数后点「选择完毕」。 */}
            {!wf.done ? (
              <div>
                {nodeIds.map((nid, i) => (
                  <NodeCard
                    key={nid}
                    cardId={msg.id}
                    nodeId={nid}
                    index={i}
                    workflow={fullWorkflow}
                    comfyUrl={comfyUrl}
                  />
                ))}
              </div>
            ) : (
              <p style={{ color: "var(--text-muted)", fontSize: 13, padding: "8px 0" }}>
                已收起 {nodeIds.length} 个节点画布（节省性能）。点「更改」重新打开调参。
              </p>
            )}

            {/* 纯节点模式（plain）：不显示引导说明文字与 AI 编排按钮（编辑器右栏即 AI 编排） */}
            {!wf.done && !plain && (
              <p style={{ color: "var(--text-muted)", fontSize: 12, margin: "8px 0 0" }}>
                手动在画布调好后点「选择完毕」；或点「AI 编排」让 AI 读取这些节点的输入口、
                按你的需求列出填充计划（提示词写入提示词口、图片放进对应图像口），确认后写入画布。
              </p>
            )}
            {!wf.done && (
              <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
                <button className="btn primary" disabled={busy} onClick={() => handleDone()}>
                  {busy ? "抓取参数中…" : "选择完毕"}
                </button>
                {!plain && (
                  <button className="btn" disabled={busy} onClick={onOrchestrate} title="让 AI 规划这些输入/输出口怎么填">
                    <Sparkles size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
                    AI 编排
                  </button>
                )}
              </div>
            )}
            {wf.done && (
              <div style={{ marginTop: 6 }}>
                <p style={{ color: "var(--text-muted)", fontSize: 13, marginBottom: 6 }}>
                  参数已确认。{plain ? "点「运转工作流」提交，或「更改」重新调参。" : "下一条输入 <code>/s</code> 启动工作流；也可让 AI 再改参。"}
                </p>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button className="btn primary" disabled={uploading} onClick={onRun}>
                    {uploading ? "上传中…" : isBusy ? "加入队列运转" : "运转工作流"}
                  </button>
                  <button className="btn" onClick={onReopen}>更改</button>
                  {!plain && (
                    <button className="btn" onClick={onOrchestrate} title="让 AI 规划这些输入/输出口怎么填">
                      <Sparkles size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
                      AI 编排
                    </button>
                  )}
                  {!plain && fullWorkflow && (
                    <button className="btn" title="复制当前工作流到 AI 搭工作流页并新建会话；不会自动改回当前对话卡片"
                      onClick={() => {
                        try { localStorage.setItem("laf_pending_build_graph", JSON.stringify(fullWorkflow)); } catch { /* 太大则忽略 */ }
                        window.location.hash = "#/ai-build";
                      }}>
                      复制到 AI 搭工作流页
                    </button>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>
      {pendingLora && (
        <ConfirmModal
          title="应用 LoRA 数据保存内容"
          message={`检测到已保存数据的 LoRA：${pendingLora.proposal.loraNames.join("、")}。使用后将覆盖 ${pendingLora.proposal.weightChanges} 个权重；${pendingLora.proposal.promptChanges > 0 ? `更新 ${pendingLora.proposal.promptChanges} 个正向 CLIP 文本，把触发词放在第一行、去重后的质量提示词放在第二行最前方` : "未定位到正向 CLIP 文本，因此不会修改提示词"}。`}
          confirmText="使用保存数据"
          cancelText="保留当前参数"
          busy={busy}
          closeOnBackdrop={false}
          portal
          overlayClassName="workflow-lora-modal-mask"
          onConfirm={applySavedLoraData}
          onCancel={keepCurrentLoraData}
        />
      )}
    </div>
  );
}
// NodeCard：每节点一个独立 ComfyUI iframe（keepOnly 单节点 + node_size 自适应）——
// 工作流卡节点展示的唯一机制（用户早期设计）。对话卡片与画布工具卡共用。
export function NodeCard({
  cardId,
  nodeId,
  index,
  workflow,
  comfyUrl,
}: {
  cardId: string;
  nodeId: string;
  index: number;
  workflow: unknown;
  comfyUrl: string;
}) {
  const ref = useRef<HTMLIFrameElement | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [ratio, setRatio] = useState<number | null>(null);
  const [comfyReady, setComfyReady] = useState(false);
  const [remount, setRemount] = useState(0);
  const retryRef = useRef(0);
  const MAX_RETRY = 6;
  const loadedRef = useRef(false);
  const frameUrl = lockUrl(comfyUrl);

  useEffect(() => {
    if (comfyReady) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      if (cancelled) return;
      try {
        const st = await comfyStatus(comfyUrl);
        if (st?.running) { if (!cancelled) setComfyReady(true); return; }
      } catch { /* 未响应 */ }
      if (!cancelled) timer = setTimeout(poll, 5000);
    };
    poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [comfyUrl, comfyReady]);

  useEffect(() => {
    if (!comfyReady) return;
    retryRef.current = 0;
    loadedRef.current = false;
    let verifyTimer: ReturnType<typeof setTimeout> | null = null;
    const post = (type: string, payload?: unknown) =>
      postToFrame(ref.current?.contentWindow, type, payload, comfyUrl);
    const sendLoad = () => post("load", { workflow, exposedIds: [nodeId] });
    const scheduleVerify = (delay: number) => {
      verifyTimer = setTimeout(() => post("request_graph"), delay);
    };
    const onMsg = (ev: MessageEvent) => {
      if (!isLafMessageFromStrict(ev, ref.current?.contentWindow, comfyUrl)) return;
      const d = ev.data;
      if (d.type === "ready") {
        sendLoad();
      } else if (d.type === "loaded") {
        loadedRef.current = true;
        setLoaded(true);
        scheduleVerify(2200);
      } else if (d.type === "graph") {
        const count = d.payload?.workflow?.nodes?.length ?? 0;
        if (count === 1) {
          // 单节点校验通过
        } else if (retryRef.current < MAX_RETRY) {
          retryRef.current += 1;
          sendLoad();
          scheduleVerify(900);
        } else {
          setRemount((r) => r + 1);
        }
      } else if (d.type === "node_size") {
        const w = d.payload.w || 200;
        const h = d.payload.h || 120;
        setRatio(w / h);
      }
    };
    window.addEventListener("message", onMsg);
    return () => {
      if (verifyTimer) clearTimeout(verifyTimer);
      window.removeEventListener("message", onMsg);
    };
  }, [workflow, nodeId, comfyUrl, remount, comfyReady]);

  const frameStyle: React.CSSProperties = ratio
    ? { aspectRatio: String(ratio) }
    : { height: 220 };

  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
        <span>节点 {index + 1} · #{nodeId}</span>
        {!comfyReady && <span>ComfyUI 未启动，等待中…</span>}
        {comfyReady && !loaded && <span>载入中…</span>}
      </div>
      {comfyReady && (
        <div className="lock-canvas" style={frameStyle}>
          <iframe
            key={remount}
            id={`laf-node-${cardId}-${nodeId}`}
            ref={ref}
            src={frameUrl}
            title={`节点 ${nodeId}`}
            className="lock-frame"
            onLoad={() => {
              if (!loadedRef.current) {
                setTimeout(() => {
                  if (!loadedRef.current) {
                    postToFrame(ref.current?.contentWindow, "ping_ready", undefined, comfyUrl);
                  }
                }, 200);
              }
            }}
          />
        </div>
      )}
    </div>
  );
}
