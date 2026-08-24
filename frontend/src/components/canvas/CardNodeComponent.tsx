// canvas/CardNodeComponent.tsx — 画布自定义节点卡片（React.memo，从 CanvasStageFlow.tsx 拆出）
// 用 React.memo + 自定义比较：只比较影响渲染的字段，忽略回调引用变化，
// 避免 onNodeDrag / pan 时全部节点重渲染。
import { memo, useCallback, useMemo, useRef } from "react";
import { NodeResizer, Handle, Position } from "@xyflow/react";
import { CARD_W, INSPIRATION_META, type CardNodeProps } from "./CanvasTypes";
import { NodeCard } from "../WorkflowCard";
import { AudioPlayer } from "../AudioPlayer";
import { runScripts, Placement } from "../../lib/regexEngine";
import { renderMarkdown } from "../../lib/renderMarkdown";

function audioLinesKey(lines?: Array<{ speaker: string; url: string }>): string {
  return (lines || []).map((l) => `${l.speaker || ""}|${l.url}`).join("~");
}

function areCardPropsEqual(prev: CardNodeProps, next: CardNodeProps): boolean {
  if (prev.selected !== next.selected || prev.id !== next.id) return false;
  const p = prev.data;
  const n = next.data;
  // 数组引用在新 useMemo 产物中必然不同 → 按值比较，避免每次 resize/drag 全量重渲染
  const sameUrls = p.imageUrls === n.imageUrls
    || (p.imageUrls.length === n.imageUrls.length && p.imageUrls.every((u, i) => u === n.imageUrls[i]));
  const sameGens = p.gens === n.gens
    || (p.gens.length === n.gens.length && p.gens.every((g, i) => g === n.gens[i]));
  const sameAudioLines = audioLinesKey(p.node.storyAudioLines) === audioLinesKey(n.node.storyAudioLines);
  return (
    p.isSel === n.isSel &&
    p.customSize === n.customSize &&
    p.prompt === n.prompt &&
    p.customLabel === n.customLabel &&
    sameUrls &&
    p.naturalSize === n.naturalSize &&
    sameGens &&
    p.node.type === n.node.type &&
    p.node.inputStatus === n.node.inputStatus &&
    p.node.traceText === n.node.traceText &&
    p.node.inspirationKind === n.node.inspirationKind &&
    p.node.inspirationTitle === n.node.inspirationTitle &&
    p.node.inspirationContent === n.node.inspirationContent &&
    p.node.templateName === n.node.templateName &&
    p.node.wfConfirmed === n.node.wfConfirmed &&
    // ★ wfCaptured 必须比较：编辑器「选择完毕」写回后若 memo 判定相同不重渲，
    //   卡片按钮闭包拿旧 nn（wfCaptured 空）→「运转工作流」首次点击静默失败（要点两下）
    p.node.wfCaptured === n.node.wfCaptured &&
    p.node.wfGenerating === n.node.wfGenerating &&
    p.node.wfPromptId === n.node.wfPromptId &&
    p.wfProgress === n.wfProgress &&
    p.wfProgressNode === n.wfProgressNode &&
    p.node.wfDraft === n.node.wfDraft &&
    p.comfyUrl === n.comfyUrl &&
    p.node.referenceImageUrl === n.node.referenceImageUrl &&
    p.node.referenceImageTitle === n.node.referenceImageTitle &&
    p.node.storyText === n.node.storyText &&
    p.node.storyImage === n.node.storyImage &&
    p.node.storyVideo === n.node.storyVideo &&
    p.node.storyAudio === n.node.storyAudio &&
    sameAudioLines &&
    p.node.storyThinking === n.node.storyThinking &&
    p.node.storyIndex === n.node.storyIndex &&
    p.node.storyTotal === n.node.storyTotal &&
    p.displayRegex === n.displayRegex
  );
}

export const CardNodeComponent = memo(function CardNodeComponent({ id, data, selected }: CardNodeProps) {
  const d = data;
  const images = d.imageUrls.slice(0, 8);
  const size = d.naturalSize;
  // 剧情节点正文：跑显示层正则（markdownOnly）后 Markdown 渲染（与对话模式同款管线）
  const storyHtml = useMemo(() => {
    const raw = (d.displayRegex && d.displayRegex.length > 0 && d.node.storyText)
      ? runScripts(d.node.storyText, Placement.AI_OUTPUT, d.displayRegex, { isMarkdown: true, depth: 0 })
      : (d.node.storyText || "");
    return renderMarkdown(raw || "（空楼层）");
  }, [d.displayRegex, d.node.storyText]);
  // NodeResizer 的 onResize 引用必须稳定：其内部 effect 依赖 onResize，
  // 若投影合并触发本组件重渲染导致 onResize 引用变化，会 destroy+重建 d3 drag → 拖拽中途断掉。
  // 用 ref 保持最新回调，useCallback([id]) 让引用跨渲染稳定。
  const resizeRef = useRef(d.onResize);
  resizeRef.current = d.onResize;
  const handleResize = useCallback(
    (_: unknown, params: { width: number; height: number }) => {
      resizeRef.current(id, Math.round(params.width), Math.round(params.height));
    },
    [id],
  );
  // onResizeEnd 同样用 ref 保持引用稳定：拉伸结束把最终尺寸写回布局（一次性落 state，无中间抖动）
  const resizeEndRef = useRef(d.onResizeEnd);
  resizeEndRef.current = d.onResizeEnd;
  const handleResizeEnd = useCallback(
    (_: unknown, params: { width: number; height: number }) => {
      resizeEndRef.current?.(id, Math.round(params.width), Math.round(params.height));
    },
    [id],
  );
  // 手动 resize 过的卡片（customSize）内容填满 wrapper；否则固定 CARD_W 宽度、高度自适应。
  // ★ 不能加「|| selected」：选中时 fillWrapper=true → width:100% 在 fit-content wrapper 里
  //   会被浏览器按 max-content 解析（图片源图宽度）→ 选中即放大、取消恢复（用户报的 bug）。
  //   拉伸中内容跟随由 onResize 同步 data.customSize=true 实现（见 CanvasStageFlow onResize）。
  const fillWrapper = d.node.type === "inspiration-card" || d.node.type === "story" || !!d.customSize;
  return (
    <div
      data-card-id={id}
      onClick={(e) => { d.onSelect(id); }}
      onDoubleClick={(e) => { e.stopPropagation(); d.onOpen(d.node); }}
      onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); d.onNodeCtx(e, d.node); }}
      style={{
        width: fillWrapper ? "100%" : CARD_W,
        height: fillWrapper ? "100%" : undefined,
        background: "var(--card-bg, #1e2126)",
        border: `2px solid ${d.isSel || selected ? "var(--primary, #3b82f6)" : "var(--border, #333)"}`,
        borderRadius: 10,
        padding: 8,
        boxSizing: "border-box",
        boxShadow: selected ? "0 4px 16px rgba(59,130,246,0.25)" : "0 2px 8px rgba(0,0,0,0.3)",
        fontSize: 11,
      }}
    >
      <NodeResizer minWidth={160} minHeight={80} isVisible={d.isSel || !!selected}
        onResize={handleResize} onResizeEnd={handleResizeEnd} />
      {/* 连线手柄：source 右侧、target 左侧 */}
      <Handle type="target" position={Position.Left} style={{ background: "var(--primary, #3b82f6)" }} />
      <Handle type="source" position={Position.Right} style={{ background: "var(--primary, #3b82f6)" }} />
      {/* 内容按节点类型 */}
      {d.node.type === "workflow-tool" ? (
        <div style={{
          background: "linear-gradient(135deg, rgba(99,102,241,0.18), rgba(139,92,246,0.10))",
          borderRadius: 6, padding: 10, display: "flex", flexDirection: "column", gap: 6,
          flex: 1, minHeight: 0,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 16 }}>🛠️</span>
            <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 500 }}>工作流工具</span>
          </div>
          <div style={{
            fontSize: 13, color: "var(--text)", fontWeight: 500, lineHeight: 1.3,
            overflow: "hidden", textOverflow: "ellipsis",
            display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
          }} title={d.node.templateName || ""}>
            {d.node.templateName || "未命名模板"}
          </div>
          <div style={{ fontSize: 10, color: "var(--text-muted)", display: "flex", gap: 4, flexWrap: "wrap" }}>
            <span style={{
              padding: "1px 6px", borderRadius: 3,
              background: d.node.wfConfirmed ? "rgba(34,197,94,0.18)" : "rgba(234,179,8,0.18)",
              color: d.node.wfConfirmed ? "rgb(134,239,172)" : "rgb(253,224,71)",
            }}>
              {d.node.wfConfirmed ? "已选择" : "未选择"}
            </span>
            <span style={{ padding: "1px 6px", borderRadius: 3, background: "rgba(255,255,255,0.06)" }}>
              {Math.max(d.node.wfExposedIds?.length || 0, d.node.wfEstimatedNodeCount || 0)} 节点
            </span>
          </div>
          {/* 未选择：iframe 节点卡（对齐对话模式 NodeCard，每节点一迷你 ComfyUI 画布随真实比例）。
              超大模板（>8 节点）只展示前 8 个避免同时渲染 N 个 ComfyUI iframe 把浏览器拖垮。
              wfDraft 空时（卡片刚创建、模板未加载）显示引导文字，而非空白——避免用户看到卡片但看不到节点。 */}
          {!d.node.wfConfirmed ? (
            <div style={{
              maxHeight: 170, overflowY: "auto", display: "flex", flexDirection: "column", gap: 8,
              marginTop: 2,
            }}>
              {d.node.wfDraft ? (
                <>
                  {(d.node.wfExposedIds || []).slice(0, 8).map((id, i) => (
                    <NodeCard
                      key={id}
                      cardId={d.node.id}
                      nodeId={id}
                      index={i}
                      workflow={d.node.wfDraft}
                      comfyUrl={d.comfyUrl || ""}
                    />
                  ))}
                  {(d.node.wfExposedIds || []).length > 8 && (
                    <div style={{ fontSize: 10, color: "var(--text-muted)", padding: 6, background: "rgba(0,0,0,0.15)", borderRadius: 4, textAlign: "center" }}>
                      …还有 {(d.node.wfExposedIds || []).length - 8} 个节点，双击打开编辑器查看全部
                    </div>
                  )}
                  {(d.node.wfExposedIds || []).length === 0 && (
                    <div style={{ fontSize: 10, color: "var(--text-muted)", padding: 6, background: "rgba(0,0,0,0.15)", borderRadius: 4 }}>
                      模板未标记可编辑节点（无 exposed_ids），请双击打开编辑器查看。
                    </div>
                  )}
                </>
              ) : (
                <div style={{ fontSize: 10, color: "var(--text-muted)", padding: 6, background: "rgba(0,0,0,0.15)", borderRadius: 4, textAlign: "center" }}>
                  双击打开编辑器载入节点预览
                </div>
              )}
            </div>
          ) : null}
          {d.node.wfConfirmed ? (
            <>
              {/* 对齐对话模式 WorkflowCard 已确认态：收起说明 + 参数已确认 + 运转/更改。
                  画布侧 AI 编排已在右侧对话面板，免按钮；复制到 AI 搭工作流页画布无意义，去掉。 */}
              {(() => {
                const collapsedCount = Math.max(d.node.wfExposedIds?.length || 0, d.node.wfEstimatedNodeCount || 0);
                return collapsedCount > 0 ? (
                  <div style={{ fontSize: 10, color: "var(--text-muted)", lineHeight: 1.5, marginTop: 2 }}>
                    已收起 {collapsedCount} 个节点画布（节省性能）。点「更改」重新打开调参。
                  </div>
                ) : null;
              })()}
              <div style={{ fontSize: 10, color: "var(--text-muted)", lineHeight: 1.5 }}>
                参数已确认。点「运转工作流」提交；点「更改」进入编辑器调参。
              </div>
              <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                {/* nodrag：阻止 ReactFlow 在 mousedown 时启动拖拽/选中重渲（NodeResizer 挂载位移会吞掉首次 click → 要点两下） */}
                <button
                  className="btn primary nodrag"
                  style={{ fontSize: 10, padding: "3px 10px", flex: 1 }}
                  onClick={(e) => { e.stopPropagation(); d.onRunWorkflow?.(d.node); }}
                  title="提交任务到 ComfyUI 队列"
                >
                  运转工作流
                </button>
                <button
                  className="btn nodrag"
                  style={{ fontSize: 10, padding: "3px 10px" }}
                  onClick={(e) => { e.stopPropagation(); d.onChangeWorkflow?.(d.node); }}
                  title="返回编辑节点参数"
                >
                  更改
                </button>
              </div>
            </>
          ) : (
            <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
              <button
                className="btn primary nodrag"
                style={{ fontSize: 10, padding: "3px 10px", flex: 1 }}
                onClick={(e) => { e.stopPropagation(); d.onConfirmWorkflow?.(d.node); }}
                title="锁定当前参数选择"
              >
                选择完毕
              </button>
            </div>
          )}
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>双击打开编辑器</div>
        </div>
      ) : d.node.type === "group" ? (
        // 组容器：虚线边框 + 半透明背景，仅作视觉收纳
        // 双击标题 → inline 编辑组名（无取消/确定，失焦保存）
        // 右键标题 → 删除组（保留内容）
        <div style={{
          width: "100%", height: "100%", minWidth: 100, minHeight: 60,
          border: "1.5px dashed var(--primary, #3b82f6)", borderRadius: 12,
          background: "rgba(59,130,246,0.05)",
          display: "flex", alignItems: "flex-start", padding: 6, boxSizing: "border-box",
        }}>
          <span
            contentEditable
            suppressContentEditableWarning
            style={{
              fontSize: 10, color: "var(--primary, #3b82f6)",
              background: "rgba(59,130,246,0.15)", padding: "1px 6px", borderRadius: 4,
              cursor: "text", outline: "none", minWidth: 20,
            }}
            onBlur={(e) => {
              const val = (e.target as HTMLSpanElement).textContent?.trim() || "组";
              d.onGroupRename?.({ ...d.node, inspirationTitle: val } as any);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); (e.target as HTMLElement).blur(); }
            }}
            onContextMenu={(e) => {
              e.preventDefault();
              e.stopPropagation();
              d.onNodeCtx?.(e as any, d.node);
            }}
          >
            📁 {d.customLabel || "组"}
          </span>
        </div>
      ) : d.node.type === "inspiration-card" ? (
        // 灵感卡：彩色头部条（按 kind 区分）+ 标题 + 内容预览（6 行截断）+ NodeResizer
        <div style={{
          width: "100%", height: "100%",
          display: "flex", flexDirection: "column", boxSizing: "border-box",
        }}>
          {(() => {
            const meta = INSPIRATION_META[d.node.inspirationKind || "preset"] || INSPIRATION_META.preset;
            return (
              <>
                <div style={{
                  background: meta.color, color: "#fff", padding: "5px 9px",
                  borderRadius: 6, fontSize: 11, fontWeight: 500,
                  display: "flex", alignItems: "center", gap: 6,
                }}>
                  <span style={{ fontSize: 13 }}>{meta.icon}</span>
                  <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {d.node.inspirationTitle || "未命名"}
                  </span>
                  <span style={{ fontSize: 10, opacity: 0.85 }}>{meta.label}</span>
                </div>
                {images.length > 0 ? (
                  <div style={{ flex: 1, padding: 4, display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <img src={images[0]} alt={d.node.inspirationTitle || ""} draggable={false}
                      style={{ width: "100%", maxHeight: "100%", objectFit: "contain", borderRadius: 4, background: "rgba(255,255,255,0.03)" }} />
                  </div>
                ) : (
                  <div style={{
                    flex: 1, padding: "8px 9px", fontSize: 12, lineHeight: 1.5,
                    color: "var(--text)", background: "var(--surface, #1e2126)",
                    borderRadius: "0 0 6px 6px", border: "1px solid var(--border, #333)",
                    borderTop: 0,
                    overflow: "hidden", whiteSpace: "pre-wrap", wordBreak: "break-word",
                    display: "-webkit-box", WebkitLineClamp: 6, WebkitBoxOrient: "vertical",
                  }} title={d.node.inspirationContent || ""}>
                    {d.node.inspirationContent || "（无内容）"}
                  </div>
                )}
                <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4, textAlign: "right" }}>
                  双击编辑 · 右键插入对话
                </div>
              </>
            );
          })()}
        </div>
      ) : d.node.type === "video" ? (
        <div style={{ display: "flex", gap: 8, flex: 1, minHeight: 0 }}>
          {/* 左侧：视频 */}
          <div style={{ flex: "0 0 55%", minWidth: 0 }}>
            <video src={(d.gens[0] as { video_url?: string } | undefined)?.video_url || ""} controls
              style={{ width: "100%", aspectRatio: "16/9", borderRadius: 6, background: "#000" }} />
          </div>
          {/* 右侧：提示词 + 视频参数 */}
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 4, overflow: "hidden" }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontWeight: 500 }}>提示词</div>
            <div style={{ fontSize: 11, color: "var(--text)", lineHeight: 1.4, overflow: "hidden",
              display: "-webkit-box", WebkitLineClamp: 4, WebkitBoxOrient: "vertical",
            }} title={d.prompt}>{d.prompt || "（无）"}</div>
            {d.gens[0] && (
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2, display: "flex", flexDirection: "column", gap: 2 }}>
                {d.gens[0].templateName && <span style={{ color: "var(--primary, #3b82f6)", fontWeight: 500 }}>模板：{d.gens[0].templateName}</span>}
                {d.gens[0].modelName && <span>模型：{d.gens[0].modelName}</span>}
                {d.gens[0].resolution && <span>清晰度：{d.gens[0].resolution}</span>}
                {d.gens[0].duration && <span>时长：{d.gens[0].duration}</span>}
                {d.gens[0].referenceContent && <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>参考：{d.gens[0].referenceContent}</span>}
              </div>
            )}
          </div>
        </div>
      ) : d.node.type === "audio" ? (
        <div style={{ display: "flex", gap: 8, flex: 1, minHeight: 0 }}>
          {/* 左侧：音频 */}
          <div style={{ flex: "0 0 45%", minWidth: 0, borderRadius: 6, background: "rgba(255,255,255,0.03)",
            display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 6, padding: 8 }}>
            <span style={{ fontSize: 18 }}>🎵</span><span style={{ fontSize: 11 }}>音频</span>
            {d.gens[0]?.audio_url && (
              <audio src={d.gens[0].audio_url} controls style={{ width: "100%", marginTop: 4 }} />
            )}
          </div>
          {/* 右侧：提示词 + 音频参数 */}
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 4, overflow: "hidden" }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontWeight: 500 }}>提示词</div>
            <div style={{ fontSize: 11, color: "var(--text)", lineHeight: 1.4, overflow: "hidden",
              display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical",
            }} title={d.prompt}>{d.prompt || "（无）"}</div>
            {d.gens[0] && (
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2, display: "flex", flexDirection: "column", gap: 2 }}>
                {d.gens[0].templateName && <span style={{ color: "var(--primary, #3b82f6)", fontWeight: 500 }}>模板：{d.gens[0].templateName}</span>}
                {d.gens[0].modelName && <span>模型：{d.gens[0].modelName}</span>}
                {d.gens[0].emotionVectors && <span>情感：{d.gens[0].emotionVectors}</span>}
                {d.gens[0].duration && <span>时长：{d.gens[0].duration}</span>}
                {d.gens[0].referenceContent && <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>参考：{d.gens[0].referenceContent}</span>}
              </div>
            )}
          </div>
        </div>
      ) : d.node.type === "input" ? (
        <div style={{ width: "100%", aspectRatio: "9/16", borderRadius: 6,
          background: "rgba(59,130,246,0.08)", border: "1px dashed var(--primary, #3b82f6)",
          display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 6, padding: 8 }}>
          {d.node.inputStatus === "draft" ? (
            <>
              <span style={{ fontSize: 16 }}>✎</span><span style={{ color: "var(--primary, #3b82f6)" }}>双击输入提示词</span>
            </>
          ) : (
            <>
              {/* 调度主管委派/专家执行过程行（对齐对话模式气泡内 trace）：先委派，再生成中 */}
              {d.node.traceText && (
                <span style={{
                  fontSize: 10, color: "var(--primary, #3b82f6)", maxWidth: "100%",
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }} title={d.node.traceText}>
                  {d.node.traceText.split("\n").filter(Boolean).slice(-1)[0] || d.node.traceText}
                </span>
              )}
              <span style={{ fontSize: 16 }}>✦</span><span style={{ color: "var(--primary, #3b82f6)" }}>生成中…</span>
              {/* 任务标签 + 实时进度（工作流运转占位节点；剧情/apikey 生成无进度时只显示标签）。
                  进度语义对齐对话模式：当前节点的采样步进度（每节点 0→100），
                  节点名显示在进度条上方，节点切换时进度回零重走——不是整体任务百分比。 */}
              {d.prompt && (
                <span style={{
                  fontSize: 10, color: "var(--text-muted)", maxWidth: "100%",
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }} title={d.prompt}>{d.prompt}</span>
              )}
              {d.wfProgressNode && (
                <span style={{
                  fontSize: 10, color: "var(--primary, #3b82f6)", maxWidth: "100%",
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }} title={d.wfProgressNode}>{d.wfProgressNode}</span>
              )}
              {d.wfProgress != null && (
                <div style={{ width: "80%" }} title="当前节点采样步进度（节点切换时回零重走）">
                  <div className="wf-progress">
                    <div className="wf-progress-bar" style={{ width: `${d.wfProgress}%` }} />
                    <span className="wf-progress-txt">{d.wfProgress}%</span>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      ) : d.node.type === "reference-image" ? (
        <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", borderRadius: 6, overflow: "hidden" }}>
          {/* 标题栏 */}
          <div style={{
            background: "rgba(245,158,11,0.15)", padding: "4px 8px",
            display: "flex", alignItems: "center", gap: 4,
            borderBottom: "1px solid rgba(245,158,11,0.2)",
          }}>
            <span style={{ fontSize: 12 }}>🖼️</span>
            <span style={{
              fontSize: 10, color: "var(--text)", fontWeight: 500,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1,
            }} title={d.node.referenceImageTitle || "参考图"}>
              {d.node.referenceImageTitle || "参考图"}
            </span>
            <span style={{ fontSize: 9, color: "rgba(245,158,11,0.7)" }}>参考</span>
          </div>
          {/* 图片区域 */}
          <div style={{ flex: 1, minHeight: 0, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,0.15)", padding: 4 }}>
            {images.length > 0 ? (
              <img src={images[0]} alt={d.node.referenceImageTitle || ""} draggable={false}
                style={{ width: "100%", height: "100%", objectFit: "contain", borderRadius: 4 }}
                onLoad={(e) => { const img = e.currentTarget; if (img.naturalWidth) d.onImgLoaded(images[0], img.naturalWidth, img.naturalHeight); }}
                ref={(el) => { if (el && el.complete && el.naturalWidth && !d.naturalSize) d.onImgLoaded(images[0], el.naturalWidth, el.naturalHeight); }}
              />
            ) : (
              <span style={{ fontSize: 10, color: "var(--text-muted)" }}>加载中…</span>
            )}
          </div>
        </div>
      ) : d.node.type === "story" ? (
        <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
          {/* 楼层标题栏 */}
          <div style={{
            background: "rgba(139,92,246,0.15)", padding: "4px 8px",
            display: "flex", alignItems: "center", gap: 4,
            borderBottom: "1px solid rgba(139,92,246,0.2)", flex: "0 0 auto",
          }}>
            <span style={{ fontSize: 12 }}>📜</span>
            <span style={{
              fontSize: 10, color: "var(--text)", fontWeight: 500,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1,
            }} title="剧情楼层">剧情楼层</span>
            {d.node.storyIndex && d.node.storyTotal ? (
              <span
                style={{
                  fontSize: 10, fontWeight: 600, color: "#a78bfa", flex: "0 0 auto",
                  background: "rgba(139,92,246,0.18)", borderRadius: 4, padding: "0 5px",
                  lineHeight: "16px", fontVariantNumeric: "tabular-nums",
                }}
                title={`剧情顺序：第 ${d.node.storyIndex} / ${d.node.storyTotal} 段`}
              >
                #{d.node.storyIndex}/{d.node.storyTotal}
              </span>
            ) : null}
          </div>
          {d.node.storyVideo || d.node.storyImage || d.node.storyAudioLines?.length || d.node.storyAudio ? (
            // 有媒体（视频 > 图 > 音频分条）：左媒体右文（对齐图组节点）
            <div style={{ flex: 1, minHeight: 0, display: "flex", gap: 8, overflow: "hidden" }}>
              <div style={{ flex: "0 0 46%", minWidth: 0, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,0.15)" }}>
                {d.node.storyVideo ? (
                  <video src={d.node.storyVideo} controls style={{ width: "100%", height: "100%", objectFit: "contain", borderRadius: 4, background: "#000" }} />
                ) : d.node.storyImage ? (
                  <img src={d.node.storyImage} alt="剧情封面" draggable={false}
                    style={{ width: "100%", height: "100%", objectFit: "cover", borderRadius: 4 }} />
                ) : (
                  <div style={{ width: "100%", padding: 4, display: "flex", flexDirection: "column", gap: 4, overflowY: "auto" }}>
                    {(d.node.storyAudioLines?.length
                      ? d.node.storyAudioLines
                      : (d.node.storyAudio ? [{ speaker: "", url: d.node.storyAudio }] : []))
                      .filter((line) => !!line.url)
                      .map((line, i) => (
                        <div className="audio-bubble" key={`${line.url}-${i}`}>
                          {line.speaker && <div className="audio-speaker-label">{line.speaker}</div>}
                          <AudioPlayer src={line.url} />
                        </div>
                      ))}
                  </div>
                )}
              </div>
              <div
                className="bot-text bot-html story-node-text"
                style={{ flex: 1, minWidth: 0, overflow: "hidden", padding: "6px 4px", fontSize: 11, lineHeight: 1.5, color: "var(--text)" }}
                dangerouslySetInnerHTML={{ __html: storyHtml }}
              />
            </div>
          ) : (
            // 无封面：9:16 竖版卡，正文超长省略，双击看全文
            <div
              className="bot-text bot-html story-node-text"
              style={{ flex: 1, minHeight: 0, overflow: "hidden", padding: "8px 10px", fontSize: 11, lineHeight: 1.6, color: "var(--text)" }}
              dangerouslySetInnerHTML={{ __html: storyHtml }}
            />
          )}
          <div style={{ flex: "0 0 auto", fontSize: 9, color: "var(--text-muted)", textAlign: "center", padding: "2px 0" }}>
            双击查看全文
          </div>
        </div>
      ) : images.length > 0 ? (
        <div style={{ display: "flex", gap: 8, flex: 1, minHeight: 0 }}>
          {/* 左侧：图片 */}
          <div style={{ flex: "0 0 50%", minWidth: 0, display: "flex", flexDirection: "column", gap: 4, overflow: "hidden", justifyContent: "center" }}>
            {images.slice(0, 4).map((u) => {
              const s = d.naturalSize;
              const ar = s && s.w > 0 ? `${s.w} / ${s.h}` : "9 / 16";
              return (
                <img key={u} src={u} alt="生成图" draggable={false}
                  onLoad={(e) => { const img = e.currentTarget; if (img.naturalWidth) d.onImgLoaded(u, img.naturalWidth, img.naturalHeight); }}
                  ref={(el) => { if (el && el.complete && el.naturalWidth && !d.naturalSize) d.onImgLoaded(u, el.naturalWidth, el.naturalHeight); }}
                  style={{ width: "100%", maxHeight: "100%", aspectRatio: ar, objectFit: "contain", borderRadius: 6, background: "rgba(255,255,255,0.03)" }} />
              );
            })}
          </div>
          {/* 右侧：提示词 + 元数据 */}
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 4, overflow: "hidden" }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontWeight: 500 }}>提示词</div>
            <div style={{ fontSize: 11, color: "var(--text)", lineHeight: 1.4, overflow: "hidden",
              display: "-webkit-box", WebkitLineClamp: 5, WebkitBoxOrient: "vertical",
            }} title={d.prompt}>{d.prompt || "（无）"}</div>
            {d.gens[0] && (
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2, display: "flex", flexDirection: "column", gap: 2 }}>
                {d.gens[0].templateName && <span style={{ color: "var(--primary, #3b82f6)", fontWeight: 500 }}>模板：{d.gens[0].templateName}</span>}
                {d.gens[0].modelName && <span>模型：{d.gens[0].modelName}</span>}
                {d.gens[0].loraName !== undefined && <span>LoRA：{d.gens[0].loraName || "无"}</span>}
                {d.gens[0].dimensions && <span>尺寸：{d.gens[0].dimensions}</span>}
                {d.gens[0].tags && d.gens[0].tags.length > 0 && (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 2 }}>
                    {d.gens[0].tags.slice(0, 6).map((t, i) => (
                      <span key={i} style={{ padding: "1px 5px", borderRadius: 3, background: "rgba(255,255,255,0.08)", fontSize: 9 }}>{t}</span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div style={{
          width: "100%",
          // customSize 撑满剩余高度；默认保持 9:16 占位（与旧行为一致）
          ...(fillWrapper ? { flex: 1, minHeight: 0 } : { aspectRatio: "9/16" }),
          borderRadius: 6, background: "rgba(255,255,255,0.03)",
          display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)",
        }}>
          暂无生成图
        </div>
      )}
      {/* 字段（workflow-tool / group / inspiration-card / video / audio / image-group / reference-image / story 节点卡片内已自带信息，不重复渲染） */}
      {d.node.type !== "workflow-tool" && d.node.type !== "group" && d.node.type !== "inspiration-card"
        && d.node.type !== "story"
        && d.node.type !== "video" && d.node.type !== "audio" && d.node.type !== "image-group" && d.node.type !== "reference-image" && (
        <div style={{ marginTop: 6, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", lineHeight: "16px" }} title={d.customLabel || d.prompt}>
          {d.customLabel || d.prompt || `图组 · ${images.length} 张`}
        </div>
      )}
    </div>
  );
}, areCardPropsEqual);

export const canvasNodeTypes = { card: CardNodeComponent };
