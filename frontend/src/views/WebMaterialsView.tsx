// WebMaterialsView.tsx — 上网素材：联网搜索下载到本地的图片
// 参照「生成内容」AssetsView 的批量删除模式，支持批量删除清理上网素材。
// 图片存到 outputDir/_web_materials/，数据源：listWebMaterials / deleteWebMaterial。
import { useEffect, useState } from "react";
import { Check, Images, Search, Send, Trash2 } from "lucide-react";
import { PageShell, EmptyState } from "../components/layout/PageShell";
import { ConfirmModal } from "../components/Modal";
import { Pager } from "../components/Pager";
import { listWebMaterials, deleteWebMaterial, type WebMaterial } from "../api/ai";

const PAGE_SIZE = 32;

export function WebMaterialsView({
  outputDir,
  onSendToCanvas,
}: {
  outputDir: string;
  onSendToCanvas?: (items: WebMaterial[]) => void;
}) {
  const [items, setItems] = useState<WebMaterial[]>([]);
  const [loading, setLoading] = useState(true);
  const [selMode, setSelMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchDel, setBatchDel] = useState(false);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);

  const refresh = () => {
    if (!outputDir) { setItems([]); setLoading(false); return; }
    setLoading(true);
    listWebMaterials(outputDir)
      .then((r) => setItems(r.items || []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => { refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outputDir]);

  const toggleSel = (filename: string) =>
    setSelected((s) => { const n = new Set(s); n.has(filename) ? n.delete(filename) : n.add(filename); return n; });

  const deleteOne = async (filename: string) => {
    try { await deleteWebMaterial(outputDir, filename); } catch { /* ignore */ }
    refresh();
  };

  const doBatchDelete = async () => {
    setBatchDel(false);
    for (const fn of selected) {
      try { await deleteWebMaterial(outputDir, fn); } catch { /* 单条失败不阻断 */ }
    }
    setSelected(new Set());
    setSelMode(false);
    refresh();
  };

  const filtered = query.trim()
    ? items.filter((i) =>
        i.title.toLowerCase().includes(query.toLowerCase()) ||
        i.source_url.toLowerCase().includes(query.toLowerCase()))
    : items;

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const curPage = Math.min(page, pageCount);
  const shown = filtered.slice((curPage - 1) * PAGE_SIZE, curPage * PAGE_SIZE);

  return (
    <PageShell
      title="上网素材"
      toolbar={
        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <div className="search-field" style={{ flex: 1, position: "relative" }}>
            <Search size={14} style={{ position: "absolute", left: 9, top: 9, color: "var(--text-muted)" }} />
            <input
              style={{ width: "100%", paddingLeft: 28, boxSizing: "border-box" }}
              placeholder="搜索标题或来源…"
              value={query}
              onChange={(e) => { setQuery(e.target.value); setPage(1); }}
            />
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
      ) : items.length === 0 ? (
        <div className="empty-state">
          <Images size={32} strokeWidth={1.4} style={{ opacity: 0.5 }} />
          <p style={{ margin: 0 }}>还没有上网素材。联网搜索时下载的图片会自动出现在这里。</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <Search size={30} strokeWidth={1.4} style={{ opacity: 0.5 }} />
          <p style={{ margin: 0 }}>没有匹配「{query}」的素材。</p>
        </div>
      ) : (
        <>
          {selMode && (
            <div className="page-toolbar" style={{ marginBottom: 12 }}>
              <button className="btn danger" disabled={selected.size === 0} onClick={() => setBatchDel(true)}>
                <Trash2 size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />删除所选（{selected.size}）
              </button>
              {onSendToCanvas && (
                <button className="btn" disabled={selected.size === 0}
                  onClick={() => {
                    const fnSet = new Set(selected);
                    onSendToCanvas(items.filter((m) => fnSet.has(m.filename)));
                  }}>
                  <Send size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />发送至画布（{selected.size}）
                </button>
              )}
              <button className="btn" onClick={() => setSelected(new Set(shown.map((m) => m.filename)))}>选本页</button>
              <button className="btn" disabled={selected.size === 0} onClick={() => setSelected(new Set())}>清除</button>
            </div>
          )}
          <div className="web-materials-grid">
            {shown.map((m) => (
              <div key={m.filename}
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
            ))}
          </div>
          <Pager page={curPage} pageCount={pageCount} onPage={setPage} always />
        </>
      )}
      {batchDel && (
        <ConfirmModal
          title="批量删除上网素材"
          message={`确认删除选中的 ${selected.size} 张素材？文件会从磁盘删除，此操作不可恢复。`}
          confirmText="删除"
          danger
          onConfirm={doBatchDelete}
          onCancel={() => setBatchDel(false)}
        />
      )}
    </PageShell>
  );
}
