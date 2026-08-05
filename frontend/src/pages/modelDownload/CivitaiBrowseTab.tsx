import { useEffect, useState } from "react";
import { Download, Heart, RefreshCw } from "lucide-react";
import { StateHint } from "../../components/layout/PageShell";
import { type Settings } from "../../stores/settings";
import { browseCivitai, downloadModel, downloadWorkflowTemplate, type CivitaiCard, type ModelType } from "../../api/models";

// CivitAI 浏览：筛选栏 + 网格 + 游标分页 + 一键下载（对齐截图1）。
// 类型选「Workflows」时下载的是工作流模板(.json/.zip)，落到默认工作流文件夹，可当 AI 搭建骨架。
const SORTS = ["Highest Rated", "Most Downloaded", "Newest"];
const PERIODS = ["AllTime", "Year", "Month", "Week", "Day"];
const TYPES = ["", "Checkpoint", "LORA", "VAE", "Controlnet", "TextualInversion", "Upscaler", "Workflows"];
const BASE_MODELS = ["", "SD 1.5", "SDXL 1.0", "Pony", "Illustrious", "Flux.1 D", "Flux.1 S"];

// CivitAI 模型类型 → 本工具下载类型（落 models 子目录）
const TYPE_MAP: Record<string, ModelType> = {
  Checkpoint: "checkpoint", LORA: "lora", VAE: "vae",
  Controlnet: "controlnet", TextualInversion: "embedding", Upscaler: "upscale",
};

export function CivitaiBrowseTab({ settings, modelsDir }: { settings: Settings; modelsDir: string }) {
  const proxy = settings.proxyEnabled ? settings.proxyUrl : "";
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("Highest Rated");
  const [period, setPeriod] = useState("AllTime");
  const [type, setType] = useState("Checkpoint");
  const [baseModel, setBaseModel] = useState("");
  const [nsfw, setNsfw] = useState(false);
  const [items, setItems] = useState<CivitaiCard[]>([]);
  const [cursor, setCursor] = useState("");        // 下一页游标
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [dlMsg, setDlMsg] = useState("");

  const load = async (reset: boolean) => {
    setLoading(true); setErr("");
    try {
      const r = await browseCivitai({
        proxy, query: query.trim(), types: type, sort, period,
        baseModels: baseModel, nsfw, cursor: reset ? "" : cursor, limit: 24,
        civitaiToken: settings.civitaiToken,
      });
      setItems((prev) => (reset ? r.items : [...prev, ...r.items]));
      setCursor(r.next_cursor);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  // 筛选变化即重查（首次也触发）
  useEffect(() => { load(true); /* eslint-disable-next-line */ }, [sort, period, type, baseModel, nsfw]);

  const onDownload = async (c: CivitaiCard) => {
    if (!c.download_url) { setDlMsg(`「${c.name}」无可下载文件（可能是外部模型）。`); return; }
    const proxyUrl = settings.proxyEnabled ? settings.proxyUrl : "";
    // 工作流类型：下到默认工作流文件夹，之后可当 AI 搭建骨架
    if (c.type === "Workflows") {
      if (!settings.workflowDir) { setDlMsg("未配置默认工作流文件夹（设置 → 路径）。"); return; }
      setDlMsg(`开始下载工作流「${c.name}」→ 工作流文件夹…`);
      try {
        await downloadWorkflowTemplate({ url: c.download_url, workflowDir: settings.workflowDir, name: c.name, civitaiToken: settings.civitaiToken, proxy: proxyUrl });
        setDlMsg(`已开始下载工作流「${c.name}」，进度见上方「下载任务」面板。完成后到「AI 搭工作流」选它当骨架。`);
      } catch (e) {
        setDlMsg(`下载失败：${(e as Error).message}`);
      }
      return;
    }
    const mt = TYPE_MAP[c.type] || "checkpoint";
    if (!modelsDir) { setDlMsg("未配置模型目录（设置 → 路径）。"); return; }
    setDlMsg(`开始下载「${c.name}」→ ${mt}…`);
    try {
      await downloadModel({ url: c.download_url, modelType: mt, modelsDir, civitaiToken: settings.civitaiToken, name: c.name, proxy: proxyUrl });
      setDlMsg(`已开始下载「${c.name}」，进度见上方「下载任务」面板。`);
    } catch (e) {
      setDlMsg(`下载失败：${(e as Error).message}`);
    }
  };

  return (
    <div>
      <div className="page-toolbar">
        <input placeholder="搜索模型名 / #标签 / @用户…" value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") load(true); }}
          style={{ flex: 1, minWidth: 220 }} />
        <button className="btn" onClick={() => load(true)}>搜索</button>
      </div>
      <div className="page-toolbar">
        <Sel label="分类" value={sort} opts={SORTS} onChange={setSort} />
        <Sel label="期间" value={period} opts={PERIODS} onChange={setPeriod} />
        <Sel label="类型" value={type} opts={TYPES} onChange={setType} render={(v) => v || "全部类型"} />
        <Sel label="基础模型" value={baseModel} opts={BASE_MODELS} onChange={setBaseModel} render={(v) => v || "全部"} />
        <label style={{ fontSize: 13, cursor: "pointer", marginLeft: "auto" }}>
          <input type="checkbox" checked={nsfw} onChange={(e) => setNsfw(e.target.checked)} /> 显示 NSFW
        </label>
      </div>
      {dlMsg && <p style={{ fontSize: 13, color: "var(--text-muted)" }}>{dlMsg}</p>}
      {err && <StateHint kind="error">{err}</StateHint>}

      <div className="model-grid">
        {items.map((c) => (
          <div className="model-card" key={c.id}>
            <div className="model-cover">
              {c.cover ? <img src={c.cover} alt={c.name} loading="lazy" /> : <span className="model-nocover">无预览</span>}
              <span className="model-badge">{c.type}{c.base_model ? ` · ${c.base_model}` : ""}</span>
              <button className="model-dl" title={c.type === "Workflows" ? "下载到工作流文件夹" : "下载到对应模型目录"} onClick={() => onDownload(c)}>
                <Download size={15} />
              </button>
            </div>
            <div className="model-meta">
              <a href={c.model_url} target="_blank" rel="noreferrer" className="model-name" title={c.name}>{c.name}</a>
              <div className="model-sub">
                <span>{c.creator}</span>
                <span className="model-stat"><Heart size={11} /> {fmt(c.likes)} <Download size={11} /> {fmt(c.downloads)}</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {loading && <StateHint>加载中…</StateHint>}
      {!loading && items.length === 0 && !err && <StateHint>没有匹配的模型。</StateHint>}
      {cursor && !loading && (
        <button className="btn" style={{ margin: "16px auto", display: "block" }} onClick={() => load(false)}>
          <RefreshCw size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />加载更多
        </button>
      )}
    </div>
  );
}

function Sel({ label, value, opts, onChange, render }: {
  label: string; value: string; opts: string[]; onChange: (v: string) => void; render?: (v: string) => string;
}) {
  return (
    <label style={{ fontSize: 12, color: "var(--text-muted)", display: "flex", flexDirection: "column", gap: 2 }}>
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {opts.map((o) => <option key={o} value={o}>{render ? render(o) : o}</option>)}
      </select>
    </label>
  );
}

function fmt(n: number): string {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(n);
}
