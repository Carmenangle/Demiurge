import { useRef, useState } from "react";
import { Download, Search, Languages, Sparkles } from "lucide-react";
import { type Settings, activeChatModel } from "../stores/settings";
import {
  downloadModel, downloadStatus, fetchModelInfo,
  type ModelType, type DownloadStatus, type ModelInfo,
} from "../api/models";
import { translateText } from "../api/ai";
import { PageShell } from "../components/layout/PageShell";
import { CivitaiBrowseTab } from "./modelDownload/CivitaiBrowseTab";
import { CivArchiveTab } from "./modelDownload/CivArchiveTab";
import { HuggingFaceTab } from "./modelDownload/HuggingFaceTab";
import { DownloadsPanel } from "./modelDownload/DownloadsPanel";

// 多源模型市场外壳：tab 切数据源。OpenModelDB 不做。
type MdTab = "civitai" | "civarchive" | "huggingface" | "link";
export function ModelDownload({ settings }: { settings: Settings }) {
  const [tab, setTab] = useState<MdTab>("civitai");
  const dir = settings.modelsDir.trim()
    || (settings.comfyuiPath.trim() ? settings.comfyuiPath.replace(/[\\/]+$/, "") + "\\models" : "");
  return (
    <PageShell
      title="模型下载"
      toolbar={
        <div className="node-tabs">
          <button className={`node-tab ${tab === "civitai" ? "active" : ""}`} onClick={() => setTab("civitai")}>CivitAI</button>
          <button className={`node-tab ${tab === "civarchive" ? "active" : ""}`} onClick={() => setTab("civarchive")}>CivArchive</button>
          <button className={`node-tab ${tab === "huggingface" ? "active" : ""}`} onClick={() => setTab("huggingface")}>Hugging Face</button>
          <button className={`node-tab ${tab === "link" ? "active" : ""}`} onClick={() => setTab("link")}>链接下载</button>
        </div>
      }
    >
      <DownloadsPanel />
      {tab === "civitai" && <CivitaiBrowseTab settings={settings} modelsDir={dir} />}
      {tab === "civarchive" && <CivArchiveTab settings={settings} modelsDir={dir} />}
      {tab === "huggingface" && <HuggingFaceTab settings={settings} modelsDir={dir} />}
      {tab === "link" && <LinkDownloadTab settings={settings} />}
    </PageShell>
  );
}

const MODEL_TYPES: { value: ModelType; label: string }[] = [
  { value: "checkpoint", label: "大模型 Checkpoint（checkpoints）" },
  { value: "lora", label: "LoRA（loras）" },
  { value: "vae", label: "VAE（vae）" },
  { value: "controlnet", label: "ControlNet（controlnet）" },
  { value: "embedding", label: "Embedding（embeddings）" },
  { value: "upscale", label: "放大模型（upscale_models）" },
  { value: "clip", label: "CLIP（clip）" },
  { value: "clip_vision", label: "CLIP Vision（clip_vision）" },
  { value: "text_encoder", label: "文本编码器（text_encoders）" },
  { value: "diffusion_model", label: "扩散模型/UNet（diffusion_models）" },
  { value: "style_model", label: "风格模型（style_models）" },
  { value: "hypernetwork", label: "Hypernetwork（hypernetworks）" },
  { value: "ipadapter", label: "IPAdapter（ipadapter）" },
  { value: "gligen", label: "GLIGEN（gligen）" },
  { value: "ultralytics", label: "YOLO/Ultralytics（ultralytics）" },
  { value: "sam", label: "SAM（sams）" },
  { value: "photomaker", label: "PhotoMaker（photomaker）" },
  { value: "audio_encoder", label: "音频编码器（audio_encoders）" },
  { value: "other", label: "其他（放 checkpoints）" },
];

const LANGS = ["中文", "English", "日本語", "한국어", "Français", "Deutsch", "Español", "Русский"];

// 链接下载 tab（原「模型下载」整体，作为多源市场的一个入口保留）
function LinkDownloadTab({ settings }: { settings: Settings }) {
  const [url, setUrl] = useState("");
  const [type, setType] = useState<ModelType>("checkpoint");
  const [info, setInfo] = useState<ModelInfo | null>(null);
  const [desc, setDesc] = useState("");          // 当前展示的介绍（可被翻译/润色替换）
  const [lang, setLang] = useState("中文");
  const [loadingInfo, setLoadingInfo] = useState(false);
  const [translating, setTranslating] = useState(false);
  const [st, setSt] = useState<DownloadStatus | null>(null);
  const [err, setErr] = useState("");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const dir = settings.modelsDir.trim()
    || (settings.comfyuiPath.trim() ? settings.comfyuiPath.replace(/[\\/]+$/, "") + "\\models" : "");
  const busy = st?.status === "pending" || st?.status === "downloading";

  const onFetchInfo = async () => {
    setErr(""); setInfo(null); setDesc("");
    if (!url.trim()) { setErr("请填写模型链接"); return; }
    setLoadingInfo(true);
    try {
      const r = await fetchModelInfo(url.trim(), settings.hfToken, settings.civitaiToken, settings.proxyEnabled ? settings.proxyUrl : "");
      setInfo(r); setDesc(r.description || "");
    } catch (e) { setErr((e as Error).message); }
    finally { setLoadingInfo(false); }
  };

  const onTranslate = async (polish: boolean) => {
    if (!desc.trim()) return;
    setTranslating(true);
    try {
      const r = await translateText(desc, lang, activeChatModel(settings), polish);
      setDesc(r.text);
    } catch (e) { setErr((e as Error).message); }
    finally { setTranslating(false); }
  };

  const poll = (taskId: string) => {
    if (timer.current) clearInterval(timer.current);
    timer.current = setInterval(async () => {
      try {
        const s = await downloadStatus(taskId);
        setSt(s);
        if (s.status === "done" || s.status === "error" || s.status === "unknown") {
          if (timer.current) clearInterval(timer.current);
        }
      } catch { /* 抖动忽略 */ }
    }, 1000);
  };

  const onDownload = async () => {
    setErr("");
    if (!dir) { setErr("请先在「设置」填写模型目录或 ComfyUI 目录"); return; }
    const dlUrl = info?.download_url || url.trim();
    if (!dlUrl) { setErr("请填写模型链接"); return; }
    try {
      const r = await downloadModel({ url: dlUrl, modelType: type, modelsDir: dir, hfToken: settings.hfToken, civitaiToken: settings.civitaiToken, name: info?.name || "", proxy: settings.proxyEnabled ? settings.proxyUrl : "" });
      setSt({ status: "pending" });
      poll(r.task_id);
    } catch (e) { setErr((e as Error).message); }
  };

  const pct = st?.total ? Math.floor((st.downloaded || 0) / st.total * 100) : 0;
  const mb = (n?: number) => ((n || 0) / 1048576).toFixed(1);

  return <ModelDownloadView
    url={url} setUrl={setUrl} type={type} setType={setType}
    info={info} desc={desc} setDesc={setDesc} lang={lang} setLang={setLang}
    loadingInfo={loadingInfo} translating={translating} st={st} err={err}
    busy={busy} pct={pct} mb={mb} dir={dir}
    onFetchInfo={onFetchInfo} onTranslate={onTranslate} onDownload={onDownload}
  />;
}

interface ViewProps {
  url: string; setUrl: (v: string) => void;
  type: ModelType; setType: (v: ModelType) => void;
  info: ModelInfo | null; desc: string; setDesc: (v: string) => void;
  lang: string; setLang: (v: string) => void;
  loadingInfo: boolean; translating: boolean;
  st: DownloadStatus | null; err: string; busy: boolean; pct: number;
  mb: (n?: number) => string; dir: string;
  onFetchInfo: () => void; onTranslate: (polish: boolean) => void; onDownload: () => void;
}

function ModelDownloadView(p: ViewProps) {
  return (
    <div>
      <p style={{ color: "var(--text-muted)", margin: "0 0 16px" }}>
        粘贴 HuggingFace 或 Civitai 链接，可先获取预览图与介绍，再下载到 ComfyUI models 目录（原生识别）。
      </p>

      <div className="md-row">
        <input
          style={{ flex: 1 }}
          value={p.url}
          onChange={(e) => p.setUrl(e.target.value)}
          placeholder="https://huggingface.co/... 或 https://civitai.com/models/..."
        />
        <button className="btn" onClick={p.onFetchInfo} disabled={p.loadingInfo}>
          <Search size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          {p.loadingInfo ? "获取中…" : "获取信息"}
        </button>
      </div>

      {p.info && (
        <div className="md-info">
          {p.info.name && <h3 style={{ margin: "0 0 8px" }}>{p.info.name}</h3>}
          {p.info.images.length > 0 && (
            <div className="md-previews">
              {p.info.images.slice(0, 6).map((u, i) => (
                <img key={i} src={u} alt="预览" loading="lazy" />
              ))}
            </div>
          )}
          <div className="md-desc-tools">
            <select value={p.lang} onChange={(e) => p.setLang(e.target.value)}>
              {LANGS.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
            <button className="btn" onClick={() => p.onTranslate(false)} disabled={p.translating}>
              <Languages size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />翻译
            </button>
            <button className="btn" onClick={() => p.onTranslate(true)} disabled={p.translating}>
              <Sparkles size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />翻译+润色
            </button>
            {p.translating && <span style={{ color: "var(--text-muted)", fontSize: 13 }}>处理中…</span>}
          </div>
          <textarea
            className="md-desc"
            value={p.desc}
            onChange={(e) => p.setDesc(e.target.value)}
            rows={8}
            placeholder="（该模型无介绍）"
          />
        </div>
      )}

      <div className="md-row" style={{ marginTop: 16 }}>
        <select value={p.type} onChange={(e) => p.setType(e.target.value as ModelType)}>
          {MODEL_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
        <button className="btn primary" onClick={p.onDownload} disabled={p.busy}>
          <Download size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          {p.busy ? "下载中…" : "下载到 models"}
        </button>
      </div>
      <p style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 6 }}>
        落盘目录：{p.dir || "（未配置，请在设置填写）"}
      </p>

      {p.err && <p style={{ color: "#d9534f", fontSize: 13, marginTop: 8 }}>{p.err}</p>}
      {p.st && p.st.status !== "unknown" && (
        <div style={{ marginTop: 10, fontSize: 13 }}>
          {p.st.status === "downloading" && (
            <>
              <div style={{ marginBottom: 4 }}>{p.st.filename}　{p.mb(p.st.downloaded)} / {p.st.total ? p.mb(p.st.total) + " MB" : "未知大小"}　{p.st.total ? p.pct + "%" : ""}</div>
              <div style={{ height: 6, background: "var(--border)", borderRadius: 3, overflow: "hidden" }}>
                <div style={{ width: `${p.pct}%`, height: "100%", background: "var(--accent, #4a90d9)" }} />
              </div>
            </>
          )}
          {p.st.status === "pending" && <span style={{ color: "var(--text-muted)" }}>准备中…</span>}
          {p.st.status === "done" && <span style={{ color: "#3c9a5f" }}>✓ 下载完成：{p.st.filename}</span>}
          {p.st.status === "error" && <span style={{ color: "#d9534f" }}>下载失败：{p.st.error}</span>}
        </div>
      )}
    </div>
  );
}
