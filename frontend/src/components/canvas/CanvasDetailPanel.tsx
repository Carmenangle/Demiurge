// 画布详情面板（双击节点；T2.1-D2：从 views/CanvasStageFlow 返回区外移）。
//
// ▍搬移原则：只搬收口 —— 本文件 JSX 体与搬移前**逐字一致**（仅整体去缩进）；需要的外部符号一律以
//   **同名 props** 解构注入 ⇒「行为面 = 搬移前」由「逐字搬移锁 + 直断言行为网」两件互补证据承担。
//
// ▍红线（防「顺手统一」，改动即破行为面）
//   ① 两分支结构（剧情楼层 92vw 大窗 / 其它 760）与媒体优先级（视频 > 图片 > 音频行 > 空置提示）逐字保留；
//   ② 渲染管线：`displayRegex` 存在时 `storyText` 走 `runScripts(..., Placement.AI_OUTPUT,
//      { isMarkdown: true, depth: 0 })` 再 `renderMarkdown`，否则原文（空楼层兜底「（空楼层）」）逐字保留；
//   ③ 「发送至对话框 / 发送至对话」的 openSend 文案与 payload（含 prompt 回填）与「删除」的
//      requestDelete([detailNode.id]) 逐字保留；props 可空态由视图守卫 `{detail && detailNode && ...}` 收窄。

import type { Dispatch, RefObject, SetStateAction } from "react";
import { Send, Trash2, X } from "lucide-react";
import { AudioPlayer } from "../AudioPlayer";
import { renderMarkdown } from "../../lib/renderMarkdown";
import { runScripts, Placement, type RegexScript } from "../../lib/regexEngine";
import type { CanvasNode, GenLike, NodeDetailData } from "../../lib/canvasRuntime";
import type { SendPayload } from "../SendToChatModal";

/** 详情面板（视图守卫 `{detail && detailNode && ...}`）。 */
export interface CanvasDetailPanelProps {
  detailNode: CanvasNode;
  detail: NodeDetailData;
  setDetailNode: Dispatch<SetStateAction<CanvasNode | null>>;
  showStoryThinking: boolean;
  setShowStoryThinking: Dispatch<SetStateAction<boolean>>;
  detailHtmlRef: RefObject<HTMLDivElement | null>;
  naturalSizesRef: RefObject<Record<string, { w: number; h: number }>>;
  displayRegex: RegexScript[] | undefined;
  openSend: (title: string, payload: SendPayload) => void;
  requestDelete: (ids: string[]) => void;
}
export function CanvasDetailPanel({ detailNode, detail, setDetailNode, showStoryThinking, setShowStoryThinking,
  detailHtmlRef, naturalSizesRef, displayRegex, openSend, requestDelete }: CanvasDetailPanelProps) {
  return (
    <div className="modal-mask" onClick={() => setDetailNode(null)}>
      <div className="modal" style={{
        width: detailNode.type === "story" ? "92vw" : 760,
        maxWidth: detailNode.type === "story" ? 1200 : undefined,
        height: detailNode.type === "story" ? "86vh" : undefined,
        maxHeight: "88vh",
        display: "flex", flexDirection: "column",
      }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <h3 style={{ margin: 0 }}>{detail.title}</h3>
          <button className="icon-btn" style={{ background: "transparent", color: "var(--text)" }} onClick={() => setDetailNode(null)}>
            <X size={18} />
          </button>
        </div>
        {detailNode.type === "story" ? (
          <div style={{ flex: 1, minHeight: 0, display: "flex", gap: 16, overflow: "hidden" }}>
            {/* 左侧：媒体（视频 > 图片；无则空置提示，不占位） */}
            <div style={{ flex: "0 0 40%", minWidth: 260, maxWidth: 540, display: "flex", flexDirection: "column", gap: 8, overflowY: "auto" }}>
              {detailNode.storyVideo && (
                <video src={detailNode.storyVideo} controls style={{ width: "100%", borderRadius: 10, background: "#000" }} />
              )}
              {detailNode.storyImage && (
                <img src={detailNode.storyImage} alt="剧情封面" style={{ width: "100%", borderRadius: 10, objectFit: "contain", background: "rgba(255,255,255,0.03)" }} />
              )}
              {(detailNode.storyAudioLines?.length ? detailNode.storyAudioLines : (detailNode.storyAudio ? [{ speaker: "", url: detailNode.storyAudio }] : [])).map((line, i) => (
                <div className="audio-bubble" key={`${line.url}-${i}`}>
                  {line.speaker && <div className="audio-speaker-label">{line.speaker}</div>}
                  <AudioPlayer src={line.url} />
                </div>
              ))}
              {!detailNode.storyVideo && !detailNode.storyImage && !detailNode.storyAudioLines?.length && !detailNode.storyAudio && (
                <div style={{ width: "100%", aspectRatio: "9/16", borderRadius: 10, background: "rgba(255,255,255,0.03)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)", fontSize: 13, textAlign: "center", padding: 12 }}>
                  本楼层无配图 / 视频 / 音频
                </div>
              )}
            </div>
            {/* 右侧：对话正文（think/status/roll 与对话模式同管线） */}
            <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 8, overflowY: "auto", paddingRight: 4 }}>
              {detailNode.storyThinking ? (
                <div className="thinking">
                  <button className="thinking-head" onClick={() => setShowStoryThinking((s) => !s)}>
                    {showStoryThinking ? "收起思考" : "查看思考"}
                  </button>
                  {showStoryThinking && <div className="thinking-body">{detailNode.storyThinking}</div>}
                </div>
              ) : null}
              <div className="bot-text bot-html" ref={detailHtmlRef} style={{ fontSize: 14, lineHeight: 1.7, color: "var(--text)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}
                dangerouslySetInnerHTML={{ __html: renderMarkdown(
                  (displayRegex && displayRegex.length > 0 && detailNode.storyText)
                    ? runScripts(detailNode.storyText, Placement.AI_OUTPUT, displayRegex, { isMarkdown: true, depth: 0 })
                    : (detailNode.storyText || "（空楼层）"),
                ) }} />
            </div>
          </div>
        ) : (
        <div style={{ overflowY: "auto", display: "flex", gap: 16, flexWrap: "wrap" }}>
          {/* 左侧：主预览（图/视频/音频/输入按类型） */}
          <div style={{ flex: "1 1 300px", minWidth: 240 }}>
            {detailNode.type === "video" ? (
              <video src={(detail.gens[0] as { video_url?: string } | undefined)?.video_url || ""} controls
                style={{ width: "100%", borderRadius: 10, background: "#000" }} />
            ) : detailNode.type === "audio" ? (
              <div style={{ width: "100%", aspectRatio: "16/9", borderRadius: 10, background: "rgba(255,255,255,0.03)",
                display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
                <span style={{ fontSize: 24 }}>🎵</span>
                <span style={{ fontSize: 13, color: "var(--text-muted)" }}>音频节点（波形预览未接入）</span>
              </div>
            ) : detailNode.type === "input" ? (
              <div style={{ width: "100%", aspectRatio: "9/16", borderRadius: 10, background: "rgba(59,130,246,0.08)",
                border: "1px dashed var(--primary, #3b82f6)", display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 13, color: "var(--primary, #3b82f6)" }}>
                {detailNode.inputStatus === "draft" ? "输入节点（双击卡片输入提示词）" : "输入节点（生成中…）"}
              </div>
            ) : detailNode.type === "reference-image" ? (
              detail.imageUrls.length > 0 ? (
                <img src={detail.imageUrls[0]} alt={detailNode.referenceImageTitle || "参考图"}
                  style={{ width: "100%", borderRadius: 10, background: "rgba(255,255,255,0.03)", objectFit: "contain" }} />
              ) : (
                <div style={{ width: "100%", aspectRatio: "1/1", borderRadius: 10, background: "rgba(255,255,255,0.03)",
                  display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13, color: "var(--text-muted)" }}>
                  🖼️ 参考图加载中…
                </div>
              )
            ) : detail.imageUrls[0] ? (
              <>
                <img src={detail.imageUrls[0]} alt="大图" style={{ width: "100%", borderRadius: 10 }} />
                {(() => {
                  const s = naturalSizesRef.current[detail.imageUrls[0]];
                  return s ? (
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6, textAlign: "center" }}>
                      原图尺寸 {s.w} × {s.h}
                    </div>
                  ) : null;
                })()}
              </>
            ) : null}
          </div>
          {/* 右侧：元数据 */}
          <div style={{ flex: "1 1 260px", minWidth: 220 }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 4 }}>提示词</div>
            <div style={{ fontSize: 13, lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 240, overflowY: "auto" }}>
              {detail.prompt || "（无提示词记录）"}
            </div>
            {(() => {
              const first = detail.gens[0] as (GenLike & { description?: string }) | undefined;
              if (!first) return null;
              const tags = (Array.isArray(first.tags) ? first.tags : []).filter(Boolean);
              const time = first.created_at ? new Date(first.created_at).toLocaleString() : "";
              const templateName = first.templateName || "";
              const modelName = first.modelName || "";
              // loraNames 后端存为逗号分隔字符串，拆成数组展示
              const loraNames: string[] = Array.isArray(first.loraNames) ? first.loraNames : [];
              return (
                <>
                  {tags.length > 0 && (
                    <>
                      <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>标签</div>
                      <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                        {tags.slice(0, 24).map((t) => (
                          <span key={t} style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4, background: "rgba(59,130,246,0.12)", color: "var(--text)" }}>{t}</span>
                        ))}
                      </div>
                    </>
                  )}
                  {templateName ? (
                    <>
                      <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>模板</div>
                      <div style={{ fontSize: 13, color: "var(--text)" }}>{templateName}</div>
                    </>
                  ) : null}
                  {modelName ? (
                    <>
                      <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>模型</div>
                      <div style={{ fontSize: 12, color: "var(--text)", fontFamily: "monospace" }}>{modelName}</div>
                    </>
                  ) : null}
                  {(detailNode.type === "video" || detailNode.type === "audio") && (
                    <>
                      {first.resolution ? (
                        <>
                          <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>清晰度</div>
                          <div style={{ fontSize: 13, color: "var(--text)" }}>{first.resolution}</div>
                        </>
                      ) : null}
                      {first.duration ? (
                        <>
                          <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>时长</div>
                          <div style={{ fontSize: 13, color: "var(--text)" }}>{first.duration}</div>
                        </>
                      ) : null}
                      {first.emotionVectors ? (
                        <>
                          <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>情感向量</div>
                          <div style={{ fontSize: 13, color: "var(--text)" }}>{first.emotionVectors}</div>
                        </>
                      ) : null}
                      {first.referenceContent ? (
                        <>
                          <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>参考内容</div>
                          <div style={{ fontSize: 12, lineHeight: 1.5, color: "var(--text)" }}>{first.referenceContent}</div>
                        </>
                      ) : null}
                    </>
                  )}
                  {loraNames.length > 0 ? (
                    <>
                      <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>LoRA</div>
                      <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                        {loraNames.map((name: string) => (
                          <span key={name} style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4, background: "rgba(168,85,247,0.14)", color: "var(--text)" }}>{name}</span>
                        ))}
                      </div>
                    </>
                  ) : null}
                  {first.description ? (
                    <>
                      <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>描述</div>
                      <div style={{ fontSize: 12, lineHeight: 1.5, color: "var(--text-muted)" }}>{first.description}</div>
                    </>
                  ) : null}
                  {time && (
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 12 }}>生成时间：{time}</div>
                  )}
                </>
              );
            })()}
            {/* 每条生成独立节点（8-21 拍板）→ 无变体聚合，详情面板不再展示「全部变体」 */}
            <div style={{ marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="btn" onClick={() => openSend("发送至对话框", { text: "", images: detail.imageUrls.slice(0, 1) })}>
                <Send size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />发送至对话框
              </button>
              <button className="btn" onClick={() => openSend("发送至对话", { text: `提示词：${detail.prompt || ""}`, images: detail.imageUrls.slice(0, 1), prompt: detail.prompt || "" })}>
                <Send size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />发送至对话
              </button>
              <button className="btn danger" onClick={() => { requestDelete([detailNode.id]); setDetailNode(null); }}>
                <Trash2 size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />删除
              </button>
            </div>
          </div>
        </div>
        )}
      </div>
    </div>
  );
}
