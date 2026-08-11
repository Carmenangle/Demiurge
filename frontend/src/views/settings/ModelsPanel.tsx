import { useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Database, Image, ListChecks, MessageSquare, Plus, Search, Trash2, Video, X, XCircle } from "lucide-react";
import type { PanelProps } from "./GeneralPanel";
import {
  modelDisplayName,
  type ChatModel, type ImageModel, type VideoModel, type EmbedModel,
} from "../../stores/settings";
import { discoverProviderModels } from "../../api/aiProviders";
import { filterModelNames } from "../../lib/modelSearch";
import { probeModel, type ModelProbeKind, type ModelProbeResult } from "../../api/modelProbe";
import { resolveEndpointProxy, resolveModelProxy } from "../../lib/modelProxy";

// 嵌入模型快捷预设
const EMBED_PRESETS = [
  { name: "Ollama 本地", baseUrl: "http://localhost:11434/v1", modelName: "qwen3-embedding:latest", apiKey: "ollama" },
  { name: "智谱 云端", baseUrl: "https://open.bigmodel.cn/api/paas/v4", modelName: "embedding-3", apiKey: "" },
];

// 一张「模型卡」：名称/Key/URL + 「读取模型列表」按钮（调 discover-models 拉列表供选）
function ModelCard({ model, kind, onChange, onRemove, customSizeSupported, onCustomSizeSupport, globalProxyUrl, globalProxyEnabled }: {
  model: { id?: string; displayName?: string; apiKey: string; baseUrl: string; modelName: string; proxyMode?: "on" | "off" | "inherit" };
  kind: Exclude<ModelProbeKind, "embedding-local" | "reranker-local">;
  onChange: (patch: Partial<ChatModel>) => void;
  onRemove: () => void;
  customSizeSupported?: boolean;
  onCustomSizeSupport?: (enabled: boolean) => void;
  globalProxyUrl: string;
  globalProxyEnabled: boolean;
}) {
  const [models, setModels] = useState<string[]>([]);
  const [modelQuery, setModelQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [probe, setProbe] = useState<{ testing?: boolean; result?: ModelProbeResult }>({});

  const discover = async () => {
    if (!model.baseUrl) { setErr("请先填 API URL"); return; }
    setLoading(true); setErr("");
    try {
      const r = await discoverProviderModels(model.baseUrl, model.apiKey);
      if (r.ok) {
        setModels(r.models);
        setModelQuery("");
      }
      else setErr(r.error || "读取失败");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  };
  const filteredModels = filterModelNames(models, modelQuery);

  const test = async () => {
    setProbe({ testing: true });
    try {
      const result = await probeModel({
        kind,
        baseUrl: model.baseUrl,
        apiKey: model.apiKey,
        modelName: model.modelName,
        proxyUrl: resolveModelProxy(model.proxyMode, globalProxyUrl, globalProxyEnabled),
      });
      setProbe({ result });
    } catch (error) {
      setProbe({ result: { status: "error", message: (error as Error).message, billable: false } });
    }
  };

  return (
    <div className="image-model-card">
      <div className="row-head">
        <strong>{modelDisplayName(model)}</strong>
        <button className="icon-btn" style={{ background: "#d23b3b" }} onClick={onRemove}>
          <Trash2 size={14} />
        </button>
      </div>
      <div className="model-card-summary">
        <span>{model.modelName || "未填写模型名称"}</span>
        <span>{model.baseUrl ? "接口已填写" : "接口未填写"}</span>
      </div>
      <details className="model-advanced">
        <summary>高级配置</summary>
        <div className="model-advanced-body">
      <div className="field">
        <label>显示名称</label>
        <input
          value={model.displayName || ""}
          onChange={(e) => onChange({ displayName: e.target.value })}
          placeholder={model.modelName ? `例如：${model.modelName} · 4K令牌` : "例如：GPT Image 2 · 4K令牌"}
        />
        <p className="field-hint">仅用于界面区分，不会作为模型参数发送。</p>
      </div>
      <div className="field">
        <label>API URL</label>
        <input value={model.baseUrl} onChange={(e) => onChange({ baseUrl: e.target.value })} placeholder="https://api.openai.com/v1" />
      </div>
      <div className="field">
        <label>API Key</label>
        <input type="password" value={model.apiKey} onChange={(e) => onChange({ apiKey: e.target.value })} />
      </div>
      <div className="field">
        <label>连接代理</label>
        <select value={model.proxyMode || "on"} onChange={(e) => onChange({ proxyMode: e.target.value as "on" | "off" | "inherit" })}>
          <option value="on">使用代理</option>
          <option value="off">直连</option>
          <option value="inherit">继承全局</option>
        </select>
        <p className="field-hint">使用代理：始终使用全局代理地址；继承全局：跟随全局代理开关。</p>
      </div>
      <div className="field">
        <label>API 模型名称</label>
        <div style={{ display: "flex", gap: 6 }}>
          <input value={model.modelName} onChange={(e) => onChange({ modelName: e.target.value })} placeholder="gpt-4o" style={{ flex: 1 }} />
          <button className="btn" onClick={discover} disabled={loading} title="从该供应商读取可用模型列表">
            <ListChecks size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
            {loading ? "读取中…" : "读取列表"}
          </button>
        </div>
        {err && <p className="field-hint" style={{ color: "#d23b3b" }}>{err}</p>}
        {models.length > 0 && (
          <div className="model-list-picker">
            <div className="model-list-tools">
              <div className="model-list-search">
                <Search size={14} aria-hidden="true" />
                <input
                  value={modelQuery}
                  onChange={(e) => setModelQuery(e.target.value)}
                  placeholder="搜索模型名称…"
                  aria-label="搜索模型名称"
                />
                {modelQuery && (
                  <button type="button" onClick={() => setModelQuery("")} title="清空搜索" aria-label="清空模型搜索">
                    <X size={14} />
                  </button>
                )}
              </div>
              <span>{filteredModels.length}/{models.length}</span>
            </div>
            <select
              value=""
              onChange={(e) => { if (e.target.value) onChange({ modelName: e.target.value }); }}
            >
              <option value="">
                {filteredModels.length > 0 ? "— 选择模型 —" : "— 没有匹配的模型 —"}
              </option>
              {filteredModels.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
        )}
      </div>
      {onCustomSizeSupport && (
        <label className="model-capability-toggle">
          <input
            type="checkbox"
            checked={customSizeSupported === true}
            onChange={(event) => onCustomSizeSupport(event.target.checked)}
          />
          <span>上游支持任意图片尺寸</span>
        </label>
      )}
        </div>
      </details>
      <div className="model-test-row">
        <button className="btn" type="button" onClick={test} disabled={probe.testing}>
          {probe.testing ? "测试中…" : "测试模型"}
        </button>
        {probe.result && <ProbeResultView result={probe.result} />}
      </div>
    </div>
  );
}

function ProbeResultView({ result }: { result: ModelProbeResult }) {
  const Icon = result.status === "success" ? CheckCircle2 : result.status === "warning" ? AlertTriangle : XCircle;
  return (
    <span className={`model-probe-result ${result.status}`} title={result.source || result.message}>
      <Icon size={14} /> {result.message}
    </span>
  );
}

function CapabilityTitle({ icon, title, count, configured }: {
  icon: ReactNode; title: string; count: number; configured: boolean;
}) {
  return <div className="model-capability-title">
    <span className="model-capability-icon">{icon}</span>
    <span><strong>{title}</strong><small>{count ? `${count} 个配置` : "尚未配置"}</small></span>
    <span className={`model-capability-status ${configured ? "ready" : "idle"}`}>
      {configured ? "已配置" : "待配置"}
    </span>
  </div>;
}

export function ModelsPanel({ draft, setDraft }: PanelProps) {
  const [embedProbe, setEmbedProbe] = useState<{ testing?: boolean; result?: ModelProbeResult }>({});
  const [rerankerProbe, setRerankerProbe] = useState<{ testing?: boolean; result?: ModelProbeResult }>({});
  const setEmbed = (patch: Partial<EmbedModel>) =>
    setDraft((d) => ({ ...d, embedModel: { ...d.embedModel, ...patch } }));

  const addChatModel = () =>
    setDraft((d) => ({ ...d, chatModels: [...d.chatModels, { id: crypto.randomUUID(), apiKey: "", baseUrl: "", modelName: "新模型", proxyMode: "on" }] }));
  const updateChatModel = (id: string, patch: Partial<ChatModel>) =>
    setDraft((d) => ({ ...d, chatModels: d.chatModels.map((m) => (m.id === id ? { ...m, ...patch } : m)) }));
  const removeChatModel = (id: string) =>
    setDraft((d) => ({ ...d, chatModels: d.chatModels.filter((m) => m.id !== id), activeChatModelId: d.activeChatModelId === id ? undefined : d.activeChatModelId }));

  const addImageModel = () =>
    setDraft((d) => ({ ...d, imageModels: [...d.imageModels, { id: crypto.randomUUID(), apiKey: "", baseUrl: "", modelName: "新模型", proxyMode: "on" }] }));
  const updateImageModel = (id: string, patch: Partial<ImageModel>) =>
    setDraft((d) => ({ ...d, imageModels: d.imageModels.map((m) => (m.id === id ? { ...m, ...patch } : m)) }));
  const removeImageModel = (id: string) =>
    setDraft((d) => ({ ...d, imageModels: d.imageModels.filter((m) => m.id !== id), activeImageModelId: d.activeImageModelId === id ? undefined : d.activeImageModelId }));

  const addVideoModel = () =>
    setDraft((d) => ({ ...d, videoModels: [...(d.videoModels || []), { id: crypto.randomUUID(), apiKey: "", baseUrl: "", modelName: "新模型", proxyMode: "on" }] }));
  const updateVideoModel = (id: string, patch: Partial<VideoModel>) =>
    setDraft((d) => ({ ...d, videoModels: (d.videoModels || []).map((m) => (m.id === id ? { ...m, ...patch } : m)) }));
  const removeVideoModel = (id: string) =>
    setDraft((d) => ({ ...d, videoModels: (d.videoModels || []).filter((m) => m.id !== id), activeVideoModelId: d.activeVideoModelId === id ? undefined : d.activeVideoModelId }));

  const testEmbed = async () => {
    setEmbedProbe({ testing: true });
    try {
      const result = await probeModel(draft.embedModel.mode === "local"
        ? { kind: "embedding-local", modelDir: draft.embedModel.modelDir }
        : { kind: "embedding", baseUrl: draft.embedModel.baseUrl, apiKey: draft.embedModel.apiKey,
            modelName: draft.embedModel.modelName,
            proxyUrl: resolveEndpointProxy(
              draft.embedModel.baseUrl, draft.embedModel.proxyMode,
              draft.proxyUrl, draft.proxyEnabled,
            ) });
      setEmbedProbe({ result });
    } catch (error) {
      setEmbedProbe({ result: { status: "error", message: (error as Error).message, billable: false } });
    }
  };

  const testReranker = async () => {
    setRerankerProbe({ testing: true });
    try {
      const result = await probeModel({ kind: "reranker-local", modelDir: draft.embedModel.rerankerDir });
      setRerankerProbe({ result });
    } catch (error) {
      setRerankerProbe({ result: { status: "error", message: (error as Error).message, billable: false } });
    }
  };

  const embedProvider = (() => {
    if (draft.embedModel.mode === "local") return "本地模型文件";
    const u = (draft.embedModel.baseUrl || "").toLowerCase();
    if (u.includes("11434") || u.includes("ollama")) return "本地 Ollama";
    if (u.includes("bigmodel.cn")) return "云端智谱";
    if (u.includes("openai.com")) return "云端 OpenAI";
    if (!u.trim()) return "未配置";
    return "自定义 / 中转";
  })();

  return (
    <div className="model-capability-grid">
      <p className="field-hint model-probe-notice">
        模型测试只做无计费连接/模型目录探测；不会调用聊天、Embedding、图片或视频生成。填写本地目录时会执行一次本地最小推理。
      </p>
      {/* 对话模型 */}
      <div className="settings-section model-capability-card">
        <div className="model-capability-head">
          <CapabilityTitle icon={<MessageSquare size={18} />} title="对话" count={draft.chatModels.length}
            configured={draft.chatModels.some((model) => Boolean(model.baseUrl && model.modelName))} />
          <button className="btn" onClick={addChatModel}><Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />添加</button>
        </div>
        <p className="field-hint" style={{ marginTop: 8 }}>智能体的大脑（也用于反推图片）。可配多个供应商，在对话框左下角图标处切换。</p>
        <div style={{ marginTop: 12 }}>
          {draft.chatModels.length === 0 && <p className="field-hint">还没有对话模型，点击「添加」。</p>}
          {draft.chatModels.map((m) => (
            <ModelCard key={m.id} model={m} kind="chat" onChange={(p) => updateChatModel(m.id!, p)} onRemove={() => removeChatModel(m.id!)} globalProxyUrl={draft.proxyUrl} globalProxyEnabled={draft.proxyEnabled} />
          ))}
        </div>
      </div>

      {/* 生图模型 */}
      <div className="settings-section model-capability-card">
        <div className="model-capability-head">
          <CapabilityTitle icon={<Image size={18} />} title="生图" count={draft.imageModels.length}
            configured={draft.imageModels.some((model) => Boolean(model.baseUrl && model.modelName))} />
          <button className="btn" onClick={addImageModel}><Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />添加</button>
        </div>
        <div style={{ marginTop: 12 }}>
          {draft.imageModels.length === 0 && <p className="field-hint">还没有生图模型，点击「添加」。</p>}
          {draft.imageModels.map((m) => (
            <ModelCard
              key={m.id}
              model={m}
              kind="image"
              customSizeSupported={m.supportsCustomSize}
              onCustomSizeSupport={(enabled) => updateImageModel(m.id, { supportsCustomSize: enabled })}
              onChange={(p) => updateImageModel(m.id, p as Partial<ImageModel>)}
              onRemove={() => removeImageModel(m.id)}
              globalProxyUrl={draft.proxyUrl}
              globalProxyEnabled={draft.proxyEnabled}
            />
          ))}
        </div>
      </div>

      {/* 视频模型 */}
      <div className="settings-section model-capability-card">
        <div className="model-capability-head">
          <CapabilityTitle icon={<Video size={18} />} title="视频" count={(draft.videoModels || []).length}
            configured={(draft.videoModels || []).some((model) => Boolean(model.baseUrl && model.modelName))} />
          <button className="btn" onClick={addVideoModel}><Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />添加</button>
        </div>
        <p className="field-hint" style={{ marginTop: 8 }}>文生视频（OpenAI 兼容 video/generations，多为异步任务）。可配多个供应商，对话里说"生成视频"即调用。</p>
        <div style={{ marginTop: 12 }}>
          {(draft.videoModels || []).length === 0 && <p className="field-hint">还没有视频模型，点击「添加」。</p>}
          {(draft.videoModels || []).map((m) => (
            <ModelCard key={m.id} model={m} kind="video" onChange={(p) => updateVideoModel(m.id, p as Partial<VideoModel>)} onRemove={() => removeVideoModel(m.id)} globalProxyUrl={draft.proxyUrl} globalProxyEnabled={draft.proxyEnabled} />
          ))}
        </div>
      </div>

      {/* 嵌入模型 */}
      <div className="settings-section model-capability-card">
        <div className="model-capability-head">
          <CapabilityTitle icon={<Database size={18} />} title="Embedding" count={1}
            configured={draft.embedModel.mode === "local" ? Boolean(draft.embedModel.modelDir) : Boolean(draft.embedModel.baseUrl && draft.embedModel.modelName)} />
        </div>
        <p className="field-hint" style={{ margin: "0 0 10px" }}>用于把仓库资料/生成历史向量化检索。需支持 embeddings 接口，如智谱 embedding-3、OpenAI text-embedding-3、Ollama 本地向量模型。模型文件不随项目发布包提供。</p>
        <div className="embedding-mode-tabs" role="group" aria-label="嵌入模型来源">
          <button
            type="button"
            className={`btn${draft.embedModel.mode === "remote" ? " primary is-selected" : ""}`}
            aria-pressed={draft.embedModel.mode === "remote"}
            onClick={() => setEmbed({ mode: "remote" })}
          >API / Ollama</button>
          <button
            type="button"
            className={`btn${draft.embedModel.mode === "local" ? " primary is-selected" : ""}`}
            aria-pressed={draft.embedModel.mode === "local"}
            onClick={() => setEmbed({ mode: "local" })}
          >本地模型文件</button>
        </div>
        <details className="model-advanced embedding-advanced">
          <summary>连接、模型目录与 Reranker 高级配置</summary>
          <div className="model-advanced-body">
        {draft.embedModel.mode === "remote" && <>
        <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
          {EMBED_PRESETS.map((p) => (
            <button key={p.name} className="btn" onClick={() => setEmbed({ mode: "remote", baseUrl: p.baseUrl, modelName: p.modelName, apiKey: p.apiKey })}>{p.name}</button>
          ))}
        </div>
        <p style={{ fontSize: 12, margin: "0 0 10px" }}>当前使用：<strong>{embedProvider}</strong></p>
        <div className="field"><label>API URL</label><input value={draft.embedModel.baseUrl} onChange={(e) => setEmbed({ baseUrl: e.target.value })} placeholder="http://localhost:11434/v1" /></div>
        <div className="field"><label>API Key</label><input type="password" value={draft.embedModel.apiKey} onChange={(e) => setEmbed({ apiKey: e.target.value })} /></div>
        <div className="field">
          <label>连接代理</label>
          <select value={draft.embedModel.proxyMode || "on"} onChange={(e) => setEmbed({ proxyMode: e.target.value as "on" | "off" | "inherit" })}>
            <option value="on">使用代理</option>
            <option value="off">直连</option>
            <option value="inherit">继承全局</option>
          </select>
        </div>
        <div className="field"><label>模型名称</label><input value={draft.embedModel.modelName} onChange={(e) => setEmbed({ modelName: e.target.value })} placeholder="qwen3-embedding:latest" /></div>
        </>}
        {draft.embedModel.mode === "local" &&
        <div className="field">
          <label>本地嵌入模型文件夹</label>
          <input
            value={draft.embedModel.modelDir || ""}
            onChange={(e) => setEmbed({ modelDir: e.target.value })}
            placeholder="项目目录\\backend\\data\\models\\embedding"
          />
          <p className="field-hint">选择本地模式后只使用该目录，不会调用上方 API 配置。</p>
        </div>
        }
        <div className="model-test-row">
          <button className="btn" type="button" onClick={testEmbed} disabled={embedProbe.testing}>
            {embedProbe.testing ? "测试中…" : "测试嵌入模型"}
          </button>
          {embedProbe.result && <ProbeResultView result={embedProbe.result} />}
        </div>
        <div className="field">
          <label>Reranker 模型文件夹（可选）</label>
          <input
            value={draft.embedModel.rerankerDir || ""}
            onChange={(e) => setEmbed({ rerankerDir: e.target.value })}
            placeholder="完整 RAG 版留空使用内置模型；源码版填写本机模型目录"
          />
          <p className="field-hint">填写已下载的 Cross-Encoder 模型目录，推荐 Qwen3-Reranker-0.6B；模型不随 GitHub 发布包提供，首次使用需安装本地 RAG 可选依赖。NVIDIA 显卡需安装 CUDA 版 PyTorch，否则仅验证文件并自动回退混合检索。</p>
        </div>
        <div className="model-test-row">
          <button className="btn" type="button" onClick={testReranker} disabled={rerankerProbe.testing}>
            {rerankerProbe.testing ? "测试中…" : "测试 Reranker"}
          </button>
          {rerankerProbe.result && <ProbeResultView result={rerankerProbe.result} />}
        </div>
          </div>
        </details>
      </div>
    </div>
  );
}
