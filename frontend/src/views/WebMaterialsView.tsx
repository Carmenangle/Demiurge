// WebMaterialsView.tsx — 上网素材：联网搜索下载的图片 + 灵感卡资产库（M1.4）
// 图片：存 outputDir/_web_materials/；灵感卡：存 outputDir/_web_materials/inspiration/<id>.json。
// 灵感卡封面：有图 → 图片（1:1）；无图 → 文本预览（放部分内容）。
// 双击灵感卡 → 详情弹窗（文本内容，不是生成参数；可删图只留文本）。
// 批量选择：图片 / 灵感卡各自支持 删除 / 发送至画布 / 插入输入框。
import { useEffect, useState } from "react";
import { Check, FileText, Folder, Images, Search, Send, Store, Trash2 } from "lucide-react";
import { PageShell, EmptyState } from "../components/layout/PageShell";
import { ConfirmModal } from "../components/Modal";
import { WebMaterialDetailModal } from "../components/WebMaterialDetailModal";
import { Pager } from "../components/Pager";
import { MaterialMarketModal } from "../components/MaterialMarketModalLive";
import { useSettings } from "../stores/settings";
import { effectiveGlobalProxyUrl } from "../lib/modelProxy";
import { UI_BUILD } from "../lib/buildInfo";
import {
  listWebMaterials, deleteWebMaterial, type WebMaterial,
  listInspirationCards, deleteInspirationCard, updateInspirationCard,
  type InspirationCardAsset,
} from "../api/ai";

const PAGE_SIZE = 32;

type Tab = "images" | "inspiration";

// ===== 多页作品「文件夹」聚合（2026-10-17 用户需求）=====
// pixiv 多页作品下载时每页都带同一个 source_url（作品页 URL）+ 标题「<作品名> · P<n>/<N>」
// ⇒ 前端按 source_url 聚合成一个文件夹卡片，点开再看里面各页（省得 98 张平铺占满素材库）。
const PAGE_RE = / · P(\d+)\/(\d+)$/;
const pageIdxOf = (t: string) => { const m = (t || "").match(PAGE_RE); return m ? parseInt(m[1], 10) : 0; };
const baseTitleOf = (t: string) => (t || "").replace(PAGE_RE, "");

type Cell =
  | { kind: "image"; m: WebMaterial }
  | { kind: "folder"; key: string; title: string; cover: WebMaterial; pages: WebMaterial[] };

function buildImageCells(list: WebMaterial[]): Cell[] {
  const groups = new Map<string, WebMaterial[]>();
  const order: string[] = [];
  const singles: WebMaterial[] = [];
  for (const m of list) {
    // 只聚合图站搜索保存的（有 provider + source_url）；灵感卡/本地上传不聚
    if (m.provider && m.source_url) {
      if (!groups.has(m.source_url)) { groups.set(m.source_url, []); order.push(m.source_url); }
      groups.get(m.source_url)!.push(m);
    } else {
      singles.push(m);
    }
  }
  const cells: Cell[] = [];
  for (const key of order) {
    const pages = groups.get(key)!;
    if (pages.length > 1) {
      const sorted = [...pages].sort((a, b) => pageIdxOf(a.title) - pageIdxOf(b.title));
      cells.push({ kind: "folder", key, title: baseTitleOf(sorted[0].title) || sorted[0].title,
                   cover: sorted[0], pages: sorted });
    } else {
      cells.push({ kind: "image", m: pages[0] });
    }
  }
  for (const m of singles) cells.push({ kind: "image", m });
  return cells;
}

export function WebMaterialsView({
  outputDir,
  onSendToCanvas,
  onSendToChat,
  onSendAsRecipe,
  onSendInspirationToChat,
  onSendInspirationToCanvas,
}: {
  outputDir: string;
  onSendToCanvas?: (items: WebMaterial[]) => void;
  /** 发送至对话框：只发图（与资产库同名功能对齐） */
  onSendToChat?: (item: WebMaterial) => void;
  /** 发送至对话：图 + 可读文本（素材用来源/标签/搜索词替代提示词） */
  onSendAsRecipe?: (item: WebMaterial) => void;
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
  const [folderView, setFolderView] = useState<string | null>(null);  // 打开的多页文件夹（其 source_url）
  // 灵感卡详情弹窗（双击打开：文本内容 / 删图只留文本）
  const [detailCard, setDetailCard] = useState<InspirationCardAsset | null>(null);
  const [detailMaterial, setDetailMaterial] = useState<WebMaterial | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  // P2-1：图站素材市场（pixiv / danbooru）
  const [market, setMarket] = useState<"pixiv" | "danbooru" | null>(null);
  const [note, setNote] = useState<{ msg: string; kind: "success" | "error" } | null>(null);
  const { settings } = useSettings();
  const proxy = effectiveGlobalProxyUrl(settings);

  const flashNote = (msg: string, kind: "success" | "error" = "success") => {
    setNote({ msg, kind });
    window.setTimeout(() => setNote(null), 4000);
  };

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

  // 删除整个多页文件夹（逐页删；若正看着这个文件夹则退回主视图）
  const deleteFolder = async (pages: WebMaterial[]) => {
    const src = pages[0]?.source_url || "";
    for (const p of pages) {
      try { await deleteWebMaterial(outputDir, p.filename); } catch { /* 单条失败不阻断 */ }
    }
    if (src && folderView === src) setFolderView(null);
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

  // 图片页：folderView 非空＝钻进某文件夹看它的页（按页码排序）；否则聚合成文件夹卡片 + 单图
  const imageCells: Cell[] = folderView
    ? filteredImages.filter((m) => m.source_url === folderView)
        .sort((a, b) => pageIdxOf(a.title) - pageIdxOf(b.title))
        .map((m) => ({ kind: "image" as const, m }))
    : buildImageCells(filteredImages);

  const displayLen = tab === "images" ? imageCells.length : filteredCards.length;
  const pageCount = Math.max(1, Math.ceil(displayLen / PAGE_SIZE));
  const curPage = Math.min(page, pageCount);
  const start = (curPage - 1) * PAGE_SIZE;
  const shownImages = imageCells.slice(start, start + PAGE_SIZE);
  const shownCards = filteredCards.slice(start, start + PAGE_SIZE);
  const curFolder = folderView ? filteredImages.find((m) => m.source_url === folderView) : undefined;

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

  // 「选本页」：选中当前页显示的所有图（文件夹⇒其全部页；单图⇒自身）
  const selectThisPage = () => {
    if (tab !== "images") { setSelected(new Set(filteredCards.map((m) => m.id))); return; }
    const names = new Set<string>();
    for (const cell of shownImages) {
      if (cell.kind === "image") names.add(cell.m.filename);
      else cell.pages.forEach((p) => names.add(p.filename));
    }
    setSelected(names);
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
              onChange={(e) => { setQuery(e.target.value); setPage(1); setFolderView(null); }}
            />
          </div>
          <div className="lora-mode-switch" role="group" aria-label="素材类型" style={{ display: "flex", gap: 4 }}>
            <button
              className={`btn ${tab === "images" ? "primary" : ""}`}
              onClick={() => { setTab("images"); setSelected(new Set()); setPage(1); setFolderView(null); }}
            >
              <Images size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />图片素材
            </button>
            <button
              className={`btn ${tab === "inspiration" ? "primary" : ""}`}
              onClick={() => { setTab("inspiration"); setSelected(new Set()); setPage(1); setFolderView(null); }}
            >
              <FileText size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />灵感卡
            </button>
          </div>
          <button className="btn" onClick={() => { setSelMode((v) => !v); setSelected(new Set()); }}>
            {selMode ? "退出多选" : "批量选择"}
          </button>
          <button className="btn" title="pixiv 图站搜索（需在「设置 → 凭证」填 Refresh Token）"
            onClick={() => { setMarket("pixiv"); setTab("images"); }}>
            <Store size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />pixiv 市场
          </button>
          <button className="btn" title="danbooru 图站搜索（公开 API，标签用英文下划线）"
            onClick={() => { setMarket("danbooru"); setTab("images"); }}>
            <Store size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />danbooru 市场
          </button>
          <span title="前端构建标记：刷新后仍显示旧值＝标签页没真正重载（需清缓存/无痕）"
            style={{ marginLeft: "auto", alignSelf: "center", fontSize: 11, color: "var(--text-muted)", opacity: 0.75 }}>
            UI {UI_BUILD}
          </span>
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
      ) : displayLen === 0 && items.length === 0 && cards.length === 0 ? (
        <div className="empty-state">
          <Images size={32} strokeWidth={1.4} style={{ opacity: 0.5 }} />
          <p style={{ margin: 0 }}>
            {tab === "images"
              ? "还没有上网素材。联网搜索时下载的图片会自动出现在这里。"
              : "还没有灵感卡。保存灵感卡后（对话框灵感卡 → 保存到素材库）会出现在这里。"}
          </p>
        </div>
      ) : displayLen === 0 ? (
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
                        <Send size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />插入输入框（{selected.size}）
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
              <button className="btn" onClick={selectThisPage}>选本页</button>
              <button className="btn" disabled={selected.size === 0} onClick={() => setSelected(new Set())}>清除</button>
            </div>
          )}
          {/* 文件夹面包屑：钻进多页文件夹时显示，可返回主视图 */}
          {tab === "images" && folderView && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
              <button className="btn" onClick={() => { setFolderView(null); setPage(1); setSelected(new Set()); }}>
                ← 返回素材库
              </button>
              <span style={{ fontWeight: 600, fontSize: 13, display: "flex", alignItems: "center", gap: 5 }}>
                <Folder size={14} /> {curFolder ? (baseTitleOf(curFolder.title) || curFolder.title) : "文件夹"}
              </span>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>（{imageCells.length} 张）</span>
            </div>
          )}
          <div className="web-materials-grid">
            {tab === "images"
              ? shownImages.map((cell) => cell.kind === "folder" ? (
                  <div
                    key={cell.key}
                    className={`web-materials-cell ${selMode && cell.pages.every((p) => selected.has(p.filename)) ? "sel" : ""}`}
                    title={`${cell.title}（${cell.pages.length} 张）${selMode ? "" : " · 点击打开"}`}
                    style={{ cursor: "pointer" }}
                    onClick={() => {
                      if (selMode) {
                        // 选中/取消整个文件夹（全部页）
                        setSelected((s) => {
                          const n = new Set(s);
                          const allSel = cell.pages.every((p) => n.has(p.filename));
                          cell.pages.forEach((p) => { if (allSel) n.delete(p.filename); else n.add(p.filename); });
                          return n;
                        });
                      } else {
                        setFolderView(cell.key); setPage(1); setSelected(new Set());
                      }
                    }}
                  >
                    <img src={cell.cover.url} alt={cell.title} loading="lazy" style={{ objectFit: "cover" }} />
                    <div style={{
                      position: "absolute", top: 8, left: 8, background: "rgba(0,0,0,0.62)", color: "#fff",
                      fontSize: 11.5, padding: "2px 8px", borderRadius: 6,
                      display: "flex", alignItems: "center", gap: 4,
                    }}>
                      <Folder size={12} /> {cell.pages.length} 张
                    </div>
                    {selMode ? (
                      <span className={`wm-check ${cell.pages.every((p) => selected.has(p.filename)) ? "on" : ""}`}>
                        {cell.pages.every((p) => selected.has(p.filename)) && <Check size={14} />}
                      </span>
                    ) : (
                      <button className="wm-del" title="删除整个文件夹（全部页）"
                        onClick={(e) => { e.stopPropagation(); void deleteFolder(cell.pages); }}>
                        <Trash2 size={14} />
                      </button>
                    )}
                    <div className="web-materials-source">{cell.title}</div>
                  </div>
                ) : (
                  <div
                    key={cell.m.filename}
                    className={`web-materials-cell ${selMode && selected.has(cell.m.filename) ? "sel" : ""}`}
                    title={selMode ? cell.m.title : `${cell.m.title}（点击查看详情）`}
                    style={{ cursor: selMode ? "pointer" : "zoom-in" }}
                    onClick={() => {
                      if (selMode) { toggleSel(cell.m.filename); return; }
                      setDetailMaterial(cell.m);   // 详情面板（左图右信息）；图内点击再进全屏 Lightbox
                    }}
                  >
                    <img src={cell.m.url} alt={cell.m.title} loading="lazy" />
                    {selMode ? (
                      <span className={`wm-check ${selected.has(cell.m.filename) ? "on" : ""}`}>
                        {selected.has(cell.m.filename) && <Check size={14} />}
                      </span>
                    ) : (
                      <>
                        {/* 与资产库卡片同构：hover 出现「发送至对话 / 发送至对话框 / 删除」三钮
                            （2026-10-18 用户实测：此前只有删除钮，发送入口只剩详情弹窗，找不到） */}
                        {onSendAsRecipe && (
                          <button className="wm-send-recipe" title="发送至对话（来源/标签/搜索词 + 图）"
                            onClick={(e) => { e.stopPropagation(); onSendAsRecipe(cell.m); }}>
                            <Send size={13} />
                          </button>
                        )}
                        {onSendToChat && (
                          <button className="wm-send" title="发送至对话框（仅图片）"
                            onClick={(e) => { e.stopPropagation(); onSendToChat(cell.m); }}>
                            <Send size={13} />
                          </button>
                        )}
                        <button className="wm-del" title="删除素材"
                          onClick={(e) => { e.stopPropagation(); deleteOne(cell.m.filename); }}>
                          <Trash2 size={14} />
                        </button>
                      </>
                    )}
                    <div className="web-materials-source">{cell.m.source_url || cell.m.filename}</div>
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
        <div className="modal-mask">
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
      {note && (
        <div style={{
          position: "fixed", top: 16, left: "50%", transform: "translateX(-50%)", zIndex: 2000,
          background: note.kind === "success" ? "var(--accent)" : "var(--danger, #c0392b)",
          color: "#fff", padding: "8px 16px", borderRadius: 8, fontSize: 13,
          boxShadow: "0 4px 16px rgba(0,0,0,0.25)",
        }}>
          {note.msg}
        </div>
      )}
      {detailMaterial && (
        <WebMaterialDetailModal
          item={detailMaterial}
          onClose={() => setDetailMaterial(null)}
          onDelete={(filename) => { setDetailMaterial(null); void deleteOne(filename); }}
          onNotify={flashNote}
          onSendToChat={onSendToChat}
          onSendAsRecipe={onSendAsRecipe}
        />
      )}
      {market && (
        <MaterialMarketModal
          source={market}
          outputDir={outputDir}
          proxy={proxy}
          login={settings.danbooruLogin || ""}
          onClose={() => setMarket(null)}
          onSaved={refresh}
          onNotify={flashNote}
        />
      )}
    </PageShell>
  );
}
