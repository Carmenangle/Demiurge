// WebMaterialsView.tsx — 上网素材：联网搜索下载的图片 + 灵感卡资产库（M1.4）
// 图片：存 outputDir/_web_materials/；灵感卡：存 outputDir/_web_materials/inspiration/<id>.json。
// 灵感卡封面：有图 → 图片（1:1）；无图 → 文本预览（放部分内容）。
// 双击灵感卡 → 详情弹窗（文本内容，不是生成参数；可删图只留文本）。
// 批量选择：图片 / 灵感卡各自支持 删除 / 发送至画布 / 发送对话框。
import { useEffect, useState } from "react";
import { Check, FileText, Images, Search, Send, Trash2 } from "lucide-react";
import { PageShell, EmptyState } from "../components/layout/PageShell";
import { ConfirmModal } from "../components/Modal";
import { Pager } from "../components/Pager";
import {
  listWebMaterials, deleteWebMaterial, type WebMaterial,
  listInspirationCards, deleteInspirationCard, updateInspirationCard,
  type InspirationCardAsset,
} from "../api/ai";

const PAGE_SIZE = 32;

type Tab = "images" | "inspiration";

export function WebMaterialsView({
  outputDir,
  onSendToCanvas,
  onSendInspirationToChat,
  onSendInspirationToCanvas,
}: {
  outputDir: string;
  onSendToCanvas?: (items: WebMaterial[]) => void;
  onSendInspirationToChat?: (items: InspirationCardAsset[]) => void;
  onSendInspirationToCanvas?: (items: InspirationCardAsset[]) => void;
}) {
  const [tab, setTab] = useState<Tab>("images");
  const [items, setItems] = useState<WebMaterial[]>([]);
  const [cards, setCards] = useState<InspirationCardAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [selMode, setSelMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchDel, setBatchDel] = useState(false);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  // 灵感卡详情弹窗（双击打开：文本内容 / 删图只留文本）
  const [detailCard, setDetailCard] = useState<InspirationCardAsset | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);

  const refresh = () => {
    if (!outputDir) { setItems([]); setCards([]); setLoading(false); return; }
    setLoading(true);
    Promise.all([listWebMaterials(outputDir), listInspirationCards(outputDir)])
      .then(([imgRes, cardRes]) => {
        setItems(imgRes.items || []);
        setCards(cardRes.items || []);
      })
      .catch(() => { setItems([]); setCards([]); })
      .finally(() => setLoading(false));
  };

  useEffect(() => { refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outputDir]);

  const toggleSel = (id: string) =>
    setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const deleteOne = async (id: string) => {
    try {
      if (tab === "images") await deleteWebMaterial(outputDir, id);
      else await deleteInspirationCard(outputDir, id);
    } catch { /* ignore */ }
    refresh();
  };

  const doBatchDelete = async () => {
    setBatchDel(false);
    for (const id of selected) {
      try {
        if (tab === "images") await deleteWebMaterial(outputDir, id);
        else await deleteInspirationCard(outputDir, id);
      } catch { /* 单条失败不阻断 */ }
    }
    setSelected(new Set());
    setSelMode(false);
    refresh();
  };

  // 详情弹窗：删图只留文本
  const removeCardImage = async (url: string) => {
    if (!detailCard || detailBusy) return;
    setDetailBusy(true);
    try {
      const updated = await updateInspirationCard(outputDir, {
        cardId: detailCard.id,
        removeImageUrls: [url],
      });
      setDetailCard({ ...detailCard, images: updated.images, cover_url: updated.images[0]?.url || "" });
      refresh();
    } catch { /* ignore */ } finally {
      setDetailBusy(false);
    }
  };

  const filteredImages = (() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((i) =>
      (i.title || "").toLowerCase().includes(q) ||
      (i.source_url || "").toLowerCase().includes(q));
  })();

  const filteredCards = (() => {
    const q = query.trim().toLowerCase();
    if (!q) return cards;
    return cards.filter((i) =>
      (i.title || "").toLowerCase().includes(q) ||
      (i.content || "").toLowerCase().includes(q));
  })();

  const filtered = tab === "images" ? filteredImages : filteredCards;

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const curPage = Math.min(page, pageCount);
  const start = (curPage - 1) * PAGE_SIZE;
  const shownImages = filteredImages.slice(start, start + PAGE_SIZE);
  const shownCards = filteredCards.slice(start, start + PAGE_SIZE);

  const sendSelection = (toCanvas: boolean) => {
    if (tab === "images") {
      const fnSet = new Set(selected);
      onSendToCanvas?.(items.filter((m) => fnSet.has(m.filename)));
      return;
    }
    const idSet = new Set(selected);
    const picked = cards.filter((c) => idSet.has(c.id));
    if (toCanvas) onSendInspirationToCanvas?.(picked);
    else onSendInspirationToChat?.(picked);
  };

  return (
    <PageShell
      title="上网素材"
      toolbar={
        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <div className="search-field" style={{ flex: 1, position: "relative" }}>
            <Search size={14} style={{ position: "absolute", left: 9, top: 9, color: "var(--text-muted)" }} />
            <input
              style={{ width: "100%", paddingLeft: 28, boxSizing: "border-box" }}
              placeholder={tab === "images" ? "搜索标题或来源…" : "搜索标题或内容…"}
              value={query}
              onChange={(e) => { setQuery(e.target.value); setPage(1); }}
            />
          </div>
          <div className="lora-mode-switch" role="group" aria-label="素材类型" style={{ display: "flex", gap: 4 }}>
            <button
              className={`btn ${tab === "images" ? "primary" : ""}`}
              onClick={() => { setTab("images"); setSelected(new Set()); setPage(1); }}
            >
              <Images size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />图片素材
            </button>
            <button
              className={`btn ${tab === "inspiration" ? "primary" : ""}`}
              onClick={() => { setTab("inspiration"); setSelected(new Set()); setPage(1); }}
            >
              <FileText size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />灵感卡
            </button>
          </div>
          <button className="btn" onClick={() => { setSelMode((v) => !v); setSelected(new Set()); }}>
            {selMode ? "退出多选" : "批量选择"}
          </button>
        </div>
      }
    >
      {!outputDir ? (
        <EmptyState>请先到「设置 → 路径」设置输出图片路径。</EmptyState>
      ) : loading ? (
        <div className="web-materials-grid">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="web-materials-cell skeleton" style={{ aspectRatio: "1 / 1" }} />
          ))}
        </div>
      ) : filtered.length === 0 && items.length === 0 && cards.length === 0 ? (
        <div className="empty-state">
          <Images size={32} strokeWidth={1.4} style={{ opacity: 0.5 }} />
          <p style={{ margin: 0 }}>
            {tab === "images"
              ? "还没有上网素材。联网搜索时下载的图片会自动出现在这里。"
              : "还没有灵感卡。保存灵感卡后（对话框灵感卡 → 保存到素材库）会出现在这里。"}
          </p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <Search size={30} strokeWidth={1.4} style={{ opacity: 0.5 }} />
          <p style={{ margin: 0 }}>没有匹配「{query}」的{tab === "images" ? "素材" : "灵感卡"}。</p>
        </div>
      ) : (
        <>
          {selMode && (
            <div className="page-toolbar" style={{ marginBottom: 12 }}>
              <button className="btn danger" disabled={selected.size === 0} onClick={() => setBatchDel(true)}>
                <Trash2 size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />删除所选（{selected.size}）
              </button>
              {tab === "inspiration"
                ? (
                  <>
                    {onSendInspirationToChat && (
                      <button className="btn" disabled={selected.size === 0} onClick={() => sendSelection(false)}>
                        <Send size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />发送对话框（{selected.size}）
                      </button>
                    )}
                    {onSendInspirationToCanvas && (
                      <button className="btn" disabled={selected.size === 0} onClick={() => sendSelection(true)}>
                        <Send size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />发送画布（{selected.size}）
                      </button>
                    )}
                  </>
                )
                : onSendToCanvas && (
                  <button className="btn" disabled={selected.size === 0} onClick={() => sendSelection(true)}>
                    <Send size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />发送至画布（{selected.size}）
                  </button>
                )}
              <button className="btn" onClick={() => setSelected(new Set(tab === "images"
                ? filteredImages.map((m) => m.filename)
                : filteredCards.map((m) => m.id)))}>选本页</button>
              <button className="btn" disabled={selected.size === 0} onClick={() => setSelected(new Set())}>清除</button>
            </div>
          )}
          <div className="web-materials-grid">
            {tab === "images"
              ? shownImages.map((m) => (
                  <div
                    key={m.filename}
                    className={`web-materials-cell ${selMode && selected.has(m.filename) ? "sel" : ""}`}
                    title={m.title}
                    onClick={() => selMode && toggleSel(m.filename)}
                  >
                    <img src={m.url} alt={m.title} loading="lazy" />
                    {selMode ? (
                      <span className={`wm-check ${selected.has(m.filename) ? "on" : ""}`}>
                        {selected.has(m.filename) && <Check size={14} />}
                      </span>
                    ) : (
                      <button className="wm-del" title="删除素材"
                        onClick={(e) => { e.stopPropagation(); deleteOne(m.filename); }}>
                        <Trash2 size={14} />
                      </button>
                    )}
                    <div className="web-materials-source">{m.source_url || m.filename}</div>
                  </div>
                ))
              : shownCards.map((c) => (
                  <div
                    key={c.id}
                    className={`web-materials-cell insp-card-cell ${selMode && selected.has(c.id) ? "sel" : ""}`}
                    title={`${c.title}（双击查看内容）`}
                    onClick={() => {
                      if (selMode) { toggleSel(c.id); return; }
                      setDetailCard(c);
                    }}
                  >
                    {c.cover_url ? (
                      <img src={c.cover_url} alt={c.title} loading="lazy" style={{ objectFit: "cover" }} />
                    ) : (
                      <div className="insp-card-text-cover">
                        <div className="insp-card-text-title">{c.title || "（无标题）"}</div>
                        <div className="insp-card-text-body">{c.content || "（无内容）"}</div>
                      </div>
                    )}
                    {selMode ? (
                      <span className={`wm-check ${selected.has(c.id) ? "on" : ""}`}>
                        {selected.has(c.id) && <Check size={14} />}
                      </span>
                    ) : (
                      <button className="wm-del" title="删除灵感卡"
                        onClick={(e) => { e.stopPropagation(); deleteOne(c.id); }}>
                        <Trash2 size={14} />
                      </button>
                    )}
                    <div className="web-materials-source">
                      {`灵感卡${c.images.length > 0 ? ` · ${c.images.length} 图` : " · 纯文本"}`}
                    </div>
                  </div>
                ))}
          </div>
          <Pager page={curPage} pageCount={pageCount} onPage={setPage} always />
        </>
      )}
      {batchDel && (
        <ConfirmModal
          title={tab === "images" ? "批量删除上网素材" : "批量删除灵感卡"}
          message={`确认删除选中的 ${selected.size} 张${tab === "images" ? "素材" : "灵感卡"}？${tab === "inspiration" ? "只删除灵感卡记录，图片文件保留。" : "文件会从磁盘删除，此操作不可恢复。"}`}
          confirmText="删除"
          danger
          onConfirm={doBatchDelete}
          onCancel={() => setBatchDel(false)}
        />
      )}

      {/* 灵感卡详情弹窗：双击打开，显示文本内容（非生成参数），可删图只留文本 */}
      {detailCard && (
        <div className="modal-mask" onClick={() => setDetailCard(null)}>
          <div className="modal" style={{ maxWidth: 620 }} onClick={(e) => e.stopPropagation()}>
            <h3>灵感卡 · {detailCard.title || "（无标题）"}</h3>
            <div style={{ maxHeight: "55vh", overflow: "auto", marginBottom: 12 }}>
              <p style={{ fontSize: 13, lineHeight: 1.7, whiteSpace: "pre-wrap", wordBreak: "break-word", margin: "0 0 12px" }}>
                {detailCard.content || "（无内容）"}
              </p>
              {detailCard.sources.length > 0 && (
                <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 12 }}>
                  <div style={{ fontWeight: 500, marginBottom: 4 }}>来源</div>
                  {detailCard.sources.map((s, i) => (
                    <div key={i} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      <a href={s.url} target="_blank" rel="noreferrer" title={s.url}>{s.title || s.url}</a>
                    </div>
                  ))}
                </div>
              )}
              {detailCard.images.length > 0 && (
                <div>
                  <div style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 500, marginBottom: 6 }}>
                    图片（{detailCard.images.length}）· 点击 × 删除图片，只保留文本
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {detailCard.images.map((img, i) => (
                      <div key={i} style={{ position: "relative", width: 96, height: 96 }}>
                        <img src={img.url} alt={img.title || ""} loading="lazy"
                          style={{ width: "100%", height: "100%", objectFit: "cover", borderRadius: 6 }} />
                        <button
                          className="wm-del"
                          disabled={detailBusy}
                          title="删除这张图片（只留文本）"
                          style={{ top: 4, right: 4 }}
                          onClick={() => void removeCardImage(img.url)}
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <div className="modal-actions">
              <button className="btn" onClick={() => setDetailCard(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}
    </PageShell>
  );
}
