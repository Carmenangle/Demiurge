// WebMaterialDetailModal.tsx — 上网素材详情（2026-10-16）
// 结构向画布模式的详情看齐：**左侧完整图片 + 右侧信息**（来源/作者/评级/标签/时间/大小）。
// 图片点击 → 共享 Lightbox 全屏（可滚轮缩放）；标签点击 → 复制。
// 2026-10-18：补标题栏 X + ESC 关闭（应用口径「关闭走 X/ESC」；此前只有底部「关闭」）。
// 2026-11-07：反推与资产库五档对齐（vlm/wd14/gbc/ai/desc）。原站标签与反推标签
// 是两套不同的数据（前者来自 provenance，后者写在 web-materials 资产快照），
// 分开两个分区各自可复制；GBC 打标后自动产官方布局 JSON，这里只留复制入口。
import { useEffect } from "react";
import { useOverlayRoute } from "../lib/overlayRoute";
import { Copy, Trash2, ExternalLink, Send, X } from "lucide-react";

import { openLightbox } from "./Lightbox";
import { CopyButton } from "./CopyButton";
import type { SavedGbc, WebMaterial } from "../api/ai";
import { safeExternalHref } from "../lib/externalUrl";
import { TAG_MODE_HINT, TAG_MODES } from "../lib/repoGalleryShared";

const RATING_LABEL: Record<string, string> = {
  g: "普通 (g)", s: "敏感 (s)", q: "可疑 (q)", e: "露骨 (e)",
};

function fmtSize(n?: number): string {
  if (!n) return "—";
  return n >= 1048576 ? `${(n / 1048576).toFixed(2)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

/** 详情右栏分区：小标题 + 上分隔线 + 留白（与资产库详情同款分层口径）。 */
function Section({ title, extra, children, first = false }: {
  title: string;
  extra?: React.ReactNode;
  children: React.ReactNode;
  first?: boolean;
}) {
  return (
    <div style={{
      marginTop: first ? 0 : 14,
      paddingTop: first ? 0 : 10,
      borderTop: first ? "none" : "1px solid var(--border)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: 11.5, color: "var(--text-muted)", letterSpacing: 0.4 }}>{title}</span>
        {extra != null && <span style={{ marginLeft: "auto" }}>{extra}</span>}
      </div>
      {children}
    </div>
  );
}

export function WebMaterialDetailModal({
  item, onClose, onDelete, onNotify, onSendToChat, onSendAsRecipe,
  tagMode, onTagMode, onRunTag, tagBusy, tagError, tagSummary,
  reverseTags, reverseTagLabel, gbc, mossBusy, mossError,
}: {
  item: WebMaterial;
  onClose: () => void;
  onDelete?: (filename: string) => void;
  onNotify?: (msg: string, kind?: "success" | "error") => void;
  /** 发送至对话框：只发图（与资产库「发送至对话框」同款） */
  onSendToChat?: (item: WebMaterial) => void;
  /** 发送至对话：图 + 文本（与资产库「发送至对话」同款） */
  onSendAsRecipe?: (item: WebMaterial) => void;
  /** 反推档（五档与资产库同源）：vlm 两段式 / wd14 本地 / gbc 场景图 / ai 纯标签 / desc 只写描述 */
  tagMode?: string;
  onTagMode?: (mode: string) => void;
  onRunTag?: (item: WebMaterial, mode: string) => void;
  tagBusy?: boolean;
  tagError?: string;
  tagSummary?: string;
  /** 已存的反推标签（资产快照，不是原站标签） */
  reverseTags?: string[];
  reverseTagLabel?: string;
  gbc?: SavedGbc | null;
  mossBusy?: boolean;
  mossError?: string;
}) {
  useOverlayRoute("web-material-detail", onClose);
  const tags = item.tags || [];
  const reverse = reverseTags || [];
  const mode = tagMode || "vlm";
  // 两段式复制优先用反推标签（vlm 档的描述与它同批产出；原站标签只在没有反推时兜底）
  const promptTags = reverse.length > 0 ? reverse : tags;

  // ESC 关闭（与市场弹窗/资产库详情同口径；点遮罩仍不关闭，复制粘贴友好）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const copy = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text);
      onNotify?.(`已复制${what}：${text.length > 40 ? text.slice(0, 40) + "…" : text}`, "success");
    } catch {
      onNotify?.("复制失败（浏览器未授权剪贴板）", "error");
    }
  };

  const row = (label: string, value: React.ReactNode) => (
    <div style={{ display: "flex", gap: 8, fontSize: 12.5, lineHeight: 1.9 }}>
      <span style={{ color: "var(--text-muted)", minWidth: 62 }}>{label}</span>
      <span style={{ flex: 1, minWidth: 0, wordBreak: "break-all" }}>{value || "—"}</span>
    </div>
  );

  return (
    <div className="modal-mask">
      <div
        className="modal"
        style={{
          maxWidth: 1020, width: "95vw", maxHeight: "90vh",
          display: "flex", flexDirection: "column",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 style={{ marginTop: 0, marginBottom: 10, flexShrink: 0, display: "flex", alignItems: "center" }}>
          素材详情
          <button
            type="button"
            className="icon-btn"
            style={{ marginLeft: "auto", background: "transparent", color: "var(--text)" }}
            title="关闭（ESC 亦可）"
            aria-label="关闭"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </h3>

        {/* 左图右信息（结构对齐资产库详情 RepoGallery：其源码注释即本例的警告）
            ⚠ 不要 flexWrap：换行会让容器高度需求翻倍 ⇒ 撑破 maxHeight、右栏失去滚动 */}
        <div style={{ flex: 1, minHeight: 0, display: "flex", gap: 14, overflow: "hidden" }}>
          {/* 左：完整图片 / 点击进共享 Lightbox 全屏（2026-10-18 修裁切：
              左列改 0 0 52% 竖排 + 内层 flex:1 盒 ⇒ 图片 maxHeight:100% 有确定高度
              可解析；此前左盒是直接 flex 项、高度由内容推导，竖长图被 overflow 裁底。
              ⚠ 适配语义＝contain「以绑定边为准」：竖长图（长边＝高）按高度收窄、
              宽长图（长边＝宽）按宽度收窄，**完整展示不裁剪**；⛔ 不得改 cover
              （cover＝裁切，正是本刀要修的 bug） */}
          <div style={{ flex: "0 0 52%", minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column" }}>
            <div style={{
              flex: 1, minHeight: 0, display: "flex", alignItems: "center",
              justifyContent: "center", background: "var(--surface-2)",
              borderRadius: 10, overflow: "hidden",
            }}>
              <img
                src={item.url}
                alt={item.title}
                title="点击全屏查看（滚轮缩放）"
                onClick={() => openLightbox(item.url)}
                style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain", cursor: "zoom-in" }}
              />
            </div>
          </div>

          {/* 右：来源与标签（可滚动；flex-basis 0 才不会被内容顶开） */}
          <div style={{ flex: "1 1 0%", minWidth: 0, minHeight: 0, overflowY: "auto", paddingRight: 4 }}>
            {/* 分区 1：来源（谁/从哪来） */}
            <Section title="来源" first>
              {row("来源站", item.provider ? <b>{item.provider}</b> : "—")}
              {row("作者", item.author)}
              {row("评级", item.rating ? (RATING_LABEL[item.rating] || item.rating) : "—")}
              {row("来源页", item.source_url ? (
                <a href={safeExternalHref(item.source_url)} target="_blank" rel="noreferrer"
                  style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                  {item.source_url} <ExternalLink size={11} />
                </a>
              ) : "—")}
            </Section>

            {/* 分区 2：文件（本地这条记录的属性） */}
            <Section title="文件">
              {row("文件名", item.filename)}
              {row("大小", fmtSize(item.size))}
              {row("下载时间", item.downloaded_at ? item.downloaded_at.replace("T", " ").slice(0, 19) : "—")}
              {row("搜索词", item.query)}
            </Section>

            {/* 分区 3：原站标签（provenance 带回来的，保持原样） */}
            <Section title={`原站标签（${tags.length}）`}
              extra={tags.length > 0 ? (
                <button className="btn" style={{ fontSize: 11.5, padding: "1px 6px" }}
                  onClick={() => void copy(tags.join(", "), "全部原站标签")}>
                  <Copy size={11} style={{ verticalAlign: "-1px", marginRight: 3 }} />复制全部
                </button>
              ) : null}
            >
              {tags.length === 0 ? (
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  （无标签：仅图站搜索保存的素材会带原站标签）
                </span>
              ) : (
                <div>
                  {tags.map((t) => (
                    <span
                      key={t}
                      className="market-tag t-general"
                      title="点击复制此标签"
                      onClick={() => void copy(t, "标签")}
                    >{t}</span>
                  ))}
                </div>
              )}
            </Section>

            {/* 分区 4：反推打标（与资产库同一套五档；素材同样落在 web-materials 资产上） */}
            <Section title="反推打标">
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <select
                  value={mode}
                  disabled={tagBusy}
                  title="反推档（与资产库「反推标签」同一套）"
                  onChange={(e) => onTagMode?.(e.target.value)}
                  style={{ minWidth: 0, flex: 1 }}
                >
                  {TAG_MODES.map((m) => (
                    <option key={m.value} value={m.value}>{m.label}</option>
                  ))}
                </select>
                <button
                  className="btn primary"
                  style={{ flexShrink: 0 }}
                  disabled={tagBusy || !item.assetId || !onRunTag}
                  title={item.assetId
                    ? "用当前档反推这张素材（GBC 档会自动生成官方布局 JSON）"
                    : "该素材还没有资产记录：重启后端后新下载的素材会自动补"}
                  onClick={() => onRunTag?.(item, mode)}
                >
                  {tagBusy ? "反推中…" : "开始反推"}
                </button>
              </div>
              <p className="field-hint" style={{ marginTop: 4 }}>{TAG_MODE_HINT[mode] || ""}</p>
              {tagError && (
                <p className="field-hint" style={{ marginTop: 2, color: "var(--danger, #d33)" }}>{tagError}</p>
              )}
              {!tagError && tagSummary && (
                <p className="field-hint" style={{ marginTop: 2 }}>{tagSummary}</p>
              )}
            </Section>

            {/* 分区 5：反推标签（资产快照；与原站标签两套独立复制） */}
            <Section title={reverseTagLabel ? `反推标签 · ${reverseTagLabel}` : "反推标签"}
              extra={reverse.length > 0 ? (
                <CopyButton
                  text={reverse.join(", ")}
                  label="复制反推标签"
                  className="btn"
                />
              ) : null}
            >
              {reverse.length === 0 ? (
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  （还没反推过：选档后点「开始反推」；标签会存成资产快照）
                </span>
              ) : (
                <div>
                  {reverse.map((t) => (
                    <span
                      key={t}
                      className="market-tag t-general"
                      title="点击复制此标签"
                      onClick={() => void copy(t, "反推标签")}
                    >{t}</span>
                  ))}
                </div>
              )}
            </Section>

            {/* 分区 6：视觉描述（desc / VLM 两段式两条链都写这里；面板只读 + 复制） */}
            <Section title="视觉描述"
              extra={item.description ? (
                <span style={{ display: "inline-flex", gap: 6 }}>
                  <button className="btn" style={{ fontSize: 11.5, padding: "1px 6px" }}
                    onClick={() => void copy(item.description || "", "视觉描述")}>
                    <Copy size={11} style={{ verticalAlign: "-1px", marginRight: 3 }} />复制描述
                  </button>
                  <button className="btn" style={{ fontSize: 11.5, padding: "1px 6px" }}
                    title="复制「标签行＋空行＋描述段」两段式，直接贴进 ComfyUI 提示词框"
                    onClick={() => void copy(
                      [promptTags.join(", "), item.description].filter(Boolean).join("\n\n"),
                      "两段式提示词",
                    )}>
                    <Copy size={11} style={{ verticalAlign: "-1px", marginRight: 3 }} />复制提示词
                  </button>
                </span>
              ) : null}
            >
              {item.description ? (
                <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>{item.description}</div>
              ) : (
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  （还没有视觉描述：在「反推打标」选「视觉描述」或「VLM 两段式」后点「开始反推」）
                </span>
              )}
            </Section>

            {/* 分区 7：GBC 场景图 + 官方布局（打标后自动生成，只留复制入口） */}
            {gbc?.ok && gbc.outline && (
              <Section title="GBC 场景图">
                <div style={{ fontSize: 12, lineHeight: 1.7 }}>
                  {gbc.outline.entities.map((e) => (
                    <div key={e.name}>
                      <strong>{e.name}</strong>{e.caption ? `：${e.caption}` : ""}
                    </div>
                  ))}
                  {gbc.outline.relations.map((r, i) => (
                    <div key={`rel-${i}`} style={{ color: "var(--text-muted)", marginTop: 2 }}>{r}</div>
                  ))}
                  {gbc.outline.entities.length === 0 && (
                    <div style={{ color: "var(--text-muted)" }}>
                      这张图没有解析出物体（可换一段场景描述重跑）。
                    </div>
                  )}
                </div>
                <div style={{ marginTop: 6, display: "flex", gap: 6, alignItems: "center" }}>
                  {gbc.moss_layout?.layout && (
                    <CopyButton
                      text={JSON.stringify(gbc.moss_layout.layout, null, 2)}
                      label="复制官方布局 JSON"
                      className="btn"
                    />
                  )}
                  {!gbc.moss_layout?.layout && mossBusy && (
                    <span className="field-hint">官方布局生成中…</span>
                  )}
                </div>
                {gbc.moss_layout?.layout && (
                  <p className="field-hint" style={{ marginTop: 4 }}>
                    官方布局 bbox 以原图中心为原点（y 在前）；MOSS 出图尺寸必须等于原图，否则框会整体偏移。
                  </p>
                )}
                {mossError && (
                  <p className="field-hint" style={{ marginTop: 4, color: "var(--danger, #d33)" }}>
                    {mossError}（重新打开详情会自动重试）
                  </p>
                )}
                {gbc.moss_layout?.layout && gbc.moss_layout.parts && (
                  <div style={{ marginTop: 6, fontSize: 12, lineHeight: 1.7 }}>
                    <div><strong>Style</strong>：{gbc.moss_layout.parts.style}</div>
                    {gbc.moss_layout.parts.cast.length > 0 && (
                      <div style={{ marginTop: 2 }}>
                        <strong>角色卡</strong>
                        {gbc.moss_layout.parts.cast.map((c) => (
                          <div key={c.name} style={{ color: "var(--text-muted)" }}>{c.name}：{c.card}</div>
                        ))}
                      </div>
                    )}
                    <div style={{ marginTop: 2 }}>
                      <strong>逐项描述</strong>
                      {gbc.moss_layout.parts.items.map((it, i) => (
                        <div key={`${it.name}-${i}`} style={{ color: "var(--text-muted)" }}>
                          {it.name}：{it.caption}
                        </div>
                      ))}
                    </div>
                    <div style={{ marginTop: 2, color: "var(--text-muted)" }}>
                      由 {gbc.moss_layout.model || "视觉模型"} 生成
                      {gbc.moss_layout.elapsed_s
                        ? `（${Math.round(gbc.moss_layout.elapsed_s)}s）` : ""}；
                      style 与角色卡已折进 summary.caption（官方格式没有独立字段）。
                    </div>
                  </div>
                )}
              </Section>
            )}
          </div>
        </div>

        <div className="modal-actions" style={{ flexShrink: 0 }}>
          {onDelete && (
            <button className="btn danger" style={{ marginRight: "auto" }} onClick={() => onDelete(item.filename)}>
              <Trash2 size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />删除素材
            </button>
          )}
          {onSendToChat && (
            <button className="btn" title="把这张图发到目标作品的对话框（只发图）"
              onClick={() => { onSendToChat(item); onClose(); }}>
              <Send size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />发送至对话框
            </button>
          )}
          {onSendAsRecipe && (
            <button className="btn" title="把图片连同来源/标签文本一起发到目标作品对话"
              onClick={() => { onSendAsRecipe(item); onClose(); }}>
              <Send size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />发送至对话
            </button>
          )}
          <button className="btn primary" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}
