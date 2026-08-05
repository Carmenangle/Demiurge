import { useEffect, useState } from "react";
import { Download, X } from "lucide-react";
import { StateHint } from "../../components/layout/PageShell";
import { type Settings } from "../../stores/settings";
import {
  browseCivArchive, civArchiveSources, downloadModel,
  type CivArchiveCard, type CivArchiveSource, type ModelType,
} from "../../api/models";

// CivArchive：跨平台归档搜索。点 file 卡→展开该模型的多下载源(civitai/hf/镜像)→选源下载。
const TYPES = ["", "Checkpoint", "LORA", "VAE", "Controlnet", "TextualInversion"];
const TYPE_MAP: Record<string, ModelType> = {
  Checkpoint: "checkpoint", LORA: "lora", VAE: "vae", Controlnet: "controlnet", TextualInversion: "embedding",
};

export function CivArchiveTab({ settings, modelsDir }: { settings: Settings; modelsDir: string }) {
  const proxy = settings.proxyEnabled ? settings.proxyUrl : "";
  const [query, setQuery] = useState("");
  const [type, setType] = useState("Checkpoint");
  const [nsfw, setNsfw] = useState(false);
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<CivArchiveCard[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [srcCard, setSrcCard] = useState<CivArchiveCard | null>(null);  // 展开下载源的卡
  const [sources, setSources] = useState<CivArchiveSource[] | null>(null);
  const [dlMsg, setDlMsg] = useState("");

  const load = async (p: number) => {
    setLoading(true); setErr("");
    try {
      const r = await browseCivArchive({ proxy, query: query.trim(), type, page: p, nsfw });
      setItems(r.items); setTotal(r.total); setPage(p);
    } catch (e) { setErr((e as Error).message); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(1); /* eslint-disable-next-line */ }, [type, nsfw]);

  const openSources = async (c: CivArchiveCard) => {
    setSrcCard(c); setSources(null); setDlMsg("");
    try {
      const r = await civArchiveSources(proxy, c.sha256);
      setSources(r.files || []);
    } catch (e) { setDlMsg(`获取下载源失败：${(e as Error).message}`); setSources([]); }
  };

  // version kind：直接用 civitai 直下链接
  const doDirect = async (c: CivArchiveCard) => {
    if (!modelsDir) { setDlMsg("未配置模型目录（设置 → 路径）。"); return; }
    const mt = TYPE_MAP[c.type] || "checkpoint";
    setDlMsg(`开始下载「${c.name}」→ ${mt}…（后台进行，切页不中断）`);
    try {
      await downloadModel({ url: c.direct_url, modelType: mt, modelsDir,
        hfToken: settings.hfToken, civitaiToken: settings.civitaiToken, name: c.name,
        proxy: settings.proxyEnabled ? settings.proxyUrl : "" });
      setDlMsg(`已开始下载「${c.name}」，进度见上方「下载任务」面板。`);
    } catch (e) { setDlMsg(`下载失败：${(e as Error).message}`); }
  };

  const doDownload = async (src: CivArchiveSource, card: CivArchiveCard) => {
    if (!modelsDir) { setDlMsg("未配置模型目录（设置 → 路径）。"); return; }
    const mt = TYPE_MAP[card.type] || "checkpoint";
    setDlMsg(`开始下载「${src.filename}」(源:${src.source}) → ${mt}…（后台进行，切页不中断）`);
    try {
      await downloadModel({ url: src.url, modelType: mt, modelsDir,
        hfToken: settings.hfToken, civitaiToken: settings.civitaiToken, name: src.filename,
        proxy: settings.proxyEnabled ? settings.proxyUrl : "" });
      setDlMsg(`已开始下载「${src.filename}」，进度见上方「下载任务」面板。`);
    } catch (e) { setDlMsg(`下载失败：${(e as Error).message}`); }
  };

  const pageCount = Math.max(1, Math.ceil(total / 50));

  return (
    <div>
      <div className="page-toolbar">
        <input placeholder="按模型名 / 文件名 / SHA256 搜索…" value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") load(1); }} style={{ flex: 1, minWidth: 220 }} />
        <button className="btn" onClick={() => load(1)}>搜索</button>
      </div>
      <div className="page-toolbar">
        <label style={{ fontSize: 12, color: "var(--text-muted)", display: "flex", flexDirection: "column", gap: 2 }}>
          类型
          <select value={type} onChange={(e) => setType(e.target.value)}>
            {TYPES.map((t) => <option key={t} value={t}>{t || "全部类型"}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 13, cursor: "pointer", marginLeft: "auto" }}>
          <input type="checkbox" checked={nsfw} onChange={(e) => setNsfw(e.target.checked)} /> 显示 NSFW
        </label>
      </div>
      <p style={{ fontSize: 12, color: "var(--text-muted)" }}>
        CivArchive 聚合多平台镜像：原平台删除的模型也可能从别处下到。点卡片查看可用下载源。
      </p>
      {dlMsg && <p style={{ fontSize: 13, color: "var(--text-muted)" }}>{dlMsg}</p>}
      {err && <StateHint kind="error">{err}</StateHint>}

      <div className="model-grid">
        {items.map((c) => (
          <div className="model-card" key={c.id} style={{ cursor: (c.sha256 || c.direct_url) ? "pointer" : "default" }}
            onClick={() => c.sha256 ? openSources(c) : c.direct_url && doDirect(c)}>
            <div className="model-cover">
              {c.cover ? <img src={c.cover} alt={c.name} loading="lazy" /> : <span className="model-nocover">无预览</span>}
              <span className="model-badge">{c.type}{c.base_model ? ` · ${c.base_model}` : ""}</span>
              {(c.sha256 || c.direct_url) && (
                <button className="model-dl" title={c.sha256 ? "查看下载源" : "下载"}
                  onClick={(e) => { e.stopPropagation(); c.sha256 ? openSources(c) : doDirect(c); }}>
                  <Download size={15} />
                </button>
              )}
            </div>
            <div className="model-meta">
              <span className="model-name" title={c.name}>{c.name}</span>
              <div className="model-sub">
                <span>{c.platform}</span>
                <span className="model-stat"><Download size={11} /> {fmt(c.downloads)}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
      {loading && <StateHint>加载中…</StateHint>}
      {!loading && items.length === 0 && !err && <StateHint>没有匹配的模型。</StateHint>}
      {pageCount > 1 && !loading && (
        <div style={{ display: "flex", gap: 8, justifyContent: "center", marginTop: 16 }}>
          <button className="btn" disabled={page <= 1} onClick={() => load(page - 1)}>上一页</button>
          <span className="page-info" style={{ alignSelf: "center" }}>{page} / {pageCount}</span>
          <button className="btn" disabled={page >= pageCount} onClick={() => load(page + 1)}>下一页</button>
        </div>
      )}

      {srcCard && (
        <div className="modal-mask" onClick={() => setSrcCard(null)}>
          <div className="modal" style={{ width: 560, maxHeight: "80vh", overflow: "auto" }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h3 style={{ margin: 0 }}>下载源 · {srcCard.name}</h3>
              <button className="icon-btn" style={{ background: "transparent", color: "var(--text)" }} onClick={() => setSrcCard(null)}><X size={18} /></button>
            </div>
            {sources === null ? <StateHint>读取下载源…</StateHint>
              : sources.length === 0 ? <StateHint>该模型暂无可用下载源。</StateHint>
              : (
                <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 12 }}>
                  {sources.map((s, i) => (
                    <div key={i} className="ca-source">
                      <div style={{ flex: 1, overflow: "hidden" }}>
                        <div className="ca-src-name" title={s.filename}>{s.filename}</div>
                        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                          源：{s.source}{s.is_gated ? " · 需登录" : ""}{s.is_paid ? " · 付费" : ""}
                        </div>
                      </div>
                      <button className="btn" onClick={() => doDownload(s, srcCard)}>
                        <Download size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />下载
                      </button>
                    </div>
                  ))}
                </div>
              )}
          </div>
        </div>
      )}
    </div>
  );
}

function fmt(n: number): string {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(n);
}
