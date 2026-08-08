import { useEffect, useRef, useState } from "react";
import { Sparkles, Workflow, RotateCcw } from "lucide-react";
import { getTemplateRaw } from "../api/workflows";
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
    try {
      const base = fullWorkflow;
      const values = await Promise.all(nodeIds.map((id) => requestNodeValues(id)));
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
      setBusy(false);
    }
  };

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

  // 用 postMessage 向指定 iframe 要节点参数，等其 node_values 回传
  const requestNodeValues = (nodeId: string) =>
    requestFrameMessage<{ nodeId: string; node: any }>({
      frameWindow: (document.getElementById(`laf-node-${msg.id}-${nodeId}`) as HTMLIFrameElement | null)?.contentWindow,
      comfyUrl, requestType: "request_node", expectedType: "node_values",
      payload: { nodeId }, timeoutMs: 3000,
    }).then((value) => String(value?.nodeId) === String(nodeId) ? value : null);

  return (
    <div className="msg-bot">
      <div className="bot-avatar">
        <Workflow size={18} />
      </div>
      <div className="bot-content" style={{ width: "100%" }}>
        <div style={{ marginBottom: 10 }}>
          <strong>工作流：{wf.templateName}</strong>
          {wf.done && <span style={{ color: "#3a9e5b", fontSize: 12, marginLeft: 8 }}>已确认</span>}
        </div>

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
            {/* 未确认时才渲染节点 iframe；确认后卸载，省 ComfyUI 性能（点「更改」再加载） */}
            {!wf.done ? (
              nodeIds.map((id, i) => (
                <NodeCard
                  key={id}
                  cardId={msg.id}
                  nodeId={id}
                  index={i}
                  workflow={fullWorkflow}
                  comfyUrl={comfyUrl}
                />
              ))
            ) : (
              <p style={{ color: "var(--text-muted)", fontSize: 13, padding: "8px 0" }}>
                已收起 {nodeIds.length} 个节点画布（节省性能）。点「更改」重新打开调参。
              </p>
            )}

            {!wf.done && (
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
                <button className="btn" disabled={busy} onClick={onOrchestrate} title="让 AI 规划这些输入/输出口怎么填">
                  <Sparkles size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
                  AI 编排
                </button>
              </div>
            )}
            {wf.done && (
              <div style={{ marginTop: 6 }}>
                <p style={{ color: "var(--text-muted)", fontSize: 13, marginBottom: 6 }}>
                  参数已确认。下一条输入 <code>/s</code> 启动工作流；也可让 AI 再改参。
                </p>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button className="btn primary" disabled={isBusy} onClick={onRun}>
                    {isBusy ? "运转中…" : "运转工作流"}
                  </button>
                  <button className="btn" onClick={onReopen}>更改</button>
                  <button className="btn" onClick={onOrchestrate} title="让 AI 规划这些输入/输出口怎么填">
                    <Sparkles size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
                    AI 编排
                  </button>
                  {fullWorkflow && (
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
          onConfirm={applySavedLoraData}
          onCancel={keepCurrentLoraData}
        />
      )}
    </div>
  );
}
// 单个节点卡：嵌一个 mini ComfyUI 画布，载入完整工作流但只显示该节点
function NodeCard({
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
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [ratio, setRatio] = useState<number | null>(null); // 节点 宽/高 比
  // 偶发竞态：多个节点 iframe 同源并发载入同一 ComfyUI 单例，keepOnly 后可能被会话恢复覆盖回整图。
  // 修法（对应老办法「不断新建标签页直到出现目标节点，再删掉多余标签」）：过恢复窗口后校验画布，
  // 不对就【软重发 load】—— applyLoad 内部 clear+keepOnly+closeExtraWorkflows 会重载目标节点并清掉
  // 多余标签页，不整帧重挂 ComfyUI（重挂要全量重载整个页面，就是「太慢」的来源）。
  const [remount, setRemount] = useState(0); // 仅手动兜底用：整帧重挂
  const [failed, setFailed] = useState(false); // 软重试用尽仍非单节点 → 显示手动重载
  const retryRef = useRef(0);
  const MAX_RETRY = 6;
  const frameUrl = lockUrl(comfyUrl);

  useEffect(() => {
    retryRef.current = 0;
    let verifyTimer: ReturnType<typeof setTimeout> | null = null;
    const post = (type: string, payload?: unknown) =>
      postToFrame(ref.current?.contentWindow, type, payload, comfyUrl);
    const sendLoad = () => post("load", { workflow, exposedIds: [nodeId] });
    // 过了 ComfyUI 会话恢复窗口再查画布；首验 2.2s 覆盖恢复窗口，软重发后的复验缩到 900ms
    // （此刻已过恢复窗口，keepOnly 更易稳住，不必再等 2.2s，加快收敛）。
    const scheduleVerify = (delay: number) => {
      verifyTimer = setTimeout(() => post("request_graph"), delay);
    };
    const onMsg = (ev: MessageEvent) => {
      if (!isLafMessageFromStrict(ev, ref.current?.contentWindow, comfyUrl)) return;
      const d = ev.data;
      if (d.type === "ready") {
        sendLoad();
      } else if (d.type === "loaded") {
        setLoaded(true);
        scheduleVerify(2200);
      } else if (d.type === "graph") {
        const count = d.payload?.workflow?.nodes?.length ?? 0;
        if (count === 1) {
          setFailed(false);
        } else if (retryRef.current < MAX_RETRY) {
          // 软重载：只重发 load，不重挂 iframe（不重载整个 ComfyUI 页面）→ 快
          retryRef.current += 1;
          sendLoad();
          scheduleVerify(900);
        } else {
          setFailed(true); // 用尽仍不对 → 交给手动重载（整帧重挂兜底）
        }
      } else if (d.type === "node_size") {
        // 用节点真实宽高比设定外框比例，让对话框展示区域=节点本身的形状，
        // 而不是在固定宽屏画布里缩放节点（那样会留黑/裁切看不全）
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
  }, [workflow, nodeId, comfyUrl, remount]);

  const reload = () => { setFailed(false); setLoaded(false); setRemount((a) => a + 1); };

  // aspectRatio 让外框宽高严格随节点真实比例，使展示区=节点本身的形状（不留黑边/不裁切）。
  // 不设 max-height：截断会破坏比例，导致画布留白、与节点对不齐。极高节点就按比例展示。
  const frameStyle: React.CSSProperties = ratio
    ? { aspectRatio: String(ratio) }
    : { height: 220 };

  return (
    <div style={{ marginBottom: 10 }} ref={wrapRef}>
      <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
        <span>节点 {index + 1} · #{nodeId}</span>
        {!loaded && <span>载入中…</span>}
        {failed && (
          <button className="icon-btn" title="画布没锁定到单个节点，点此重新载入" onClick={reload}
            style={{ color: "#c98a1a" }}>
            <RotateCcw size={13} style={{ verticalAlign: "-2px", marginRight: 2 }} />
            节点没对上，重新载入
          </button>
        )}
      </div>
      <div className="lock-canvas" style={frameStyle}>
        <iframe key={remount} id={`laf-node-${cardId}-${nodeId}`} ref={ref} src={frameUrl} title={`节点 ${nodeId}`} className="lock-frame" />
      </div>
    </div>
  );
}
