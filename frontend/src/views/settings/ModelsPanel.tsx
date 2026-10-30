import { useState, type ReactNode } from "react";
import {
  Database, Eye, Image, ListChecks, MessageSquare, Plus, Search, Trash2, Video, X,
} from "lucide-react";
import type { PanelProps } from "./GeneralPanel";
import {
  chatModelSource, modelDisplayName, remoteModelSource, OLLAMA_DEFAULT_BASE_URL,
  type ChatModel, type ChatModelSource, type ImageModel, type VideoModel, type EmbedModel, type VlmModel,
} from "../../stores/settings";
import { discoverProviderModels } from "../../api/aiProviders";
import { filterModelNames } from "../../lib/modelSearch";
import { probeModel, type ModelProbeKind, type ModelProbeResult } from "../../api/modelProbe";
import { globalProxyAddress, resolveEndpointProxy } from "../../lib/modelProxy";
import { ProbeResultView } from "../../components/ModelProbeResultView";
import { OllamaModelPicker } from "../../components/OllamaModelPicker";
import { LocalGgufImport } from "../../components/LocalGgufImport";
import {
  applyChatLocalImport, applyVlmLocalImport, newChatModel, switchEmbedSource, switchVlmSource,
} from "../../lib/modelSources";

// 三档来源（2026-10-30）：云端 API / 本机 Ollama / 本地模型文件。三者的字段与默认值互不混用。
type RemoteSource = "api" | "ollama";
type SourceTab = RemoteSource | "local";

const EMBED_API_PRESETS = [
  { name: "智谱 云端", baseUrl: "https://open.bigmodel.cn/api/paas/v4", modelName: "embedding-3" },
  { name: "OpenAI 云端", baseUrl: "https://api.openai.com/v1", modelName: "text-embedding-3-small" },
];
const VLM_API_PRESETS = [
  { name: "智谱 GLM-4V", baseUrl: "https://open.bigmodel.cn/api/paas/v4", modelName: "glm-4v-flash" },
  { name: "OpenAI GPT-4o", baseUrl: "https://api.openai.com/v1", modelName: "gpt-4o" },
];
const EMBED_OLLAMA_MODELS = ["qwen3-embedding:latest"];
const VLM_OLLAMA_MODELS = ["qwen3vl:8b", "gemma4:latest"];

type CardModel = {
  id?: string;
  displayName?: string;
  source?: ChatModelSource;
  apiKey: string;
  baseUrl: string;
  modelName: string;
  providerProfile?: ChatModel["providerProfile"];
  proxyMode?: ChatModel["proxyMode"];
  proxyUrl?: string;
  ggufPath?: string;
  mmprojPath?: string;
  ollamaName?: string;
  supportsCustomSize?: boolean;
};

// 一张「模型卡」：来源不同，展示的必填项不同；API 卡保持原有全部字段（折叠在高级配置里）。
function ModelCard({ model, kind, source, onChange, onRemove, customSizeSupported, onCustomSizeSupport, globalProxyUrl, globalProxyEnabled }: {
  model: CardModel;
  kind: Exclude<ModelProbeKind, "embedding-local" | "reranker-local" | "chat-local" | "vlm-local">;
  source?: ChatModelSource;
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

  const isChat = kind === "chat";
  const chatSource = isChat ? (source || chatModelSource(model)) : undefined;
  const isOllama = chatSource === "ollama";
  const isLocal = chatSource === "local";

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
        proxyUrl: resolveEndpointProxy(model.baseUrl, model.proxyMode, globalProxyUrl, globalProxyEnabled),
      });
      setProbe({ result });
    } catch (error) {
      setProbe({ result: { status: "error", message: (error as Error).message, billable: false } });
    }
  };

  return (
    <div className="image-model-card">
      <div className="row-head">
        <div className="model-card-title">
          <strong>{modelDisplayName(model)}</strong>
          {isChat && (
            <span className={`model-source-chip ${chatSource}`}>
              {chatSource === "api" ? "云端 API" : chatSource === "ollama" ? "本机 Ollama" : "本地 GGUF"}
            </span>
          )}
        </div>
        <button className="icon-btn" style={{ background: "#d23b3b" }} onClick={onRemove}>
          <Trash2 size={14} />
        </button>
      </div>
      <div className="model-card-summary">
        <span>{model.modelName || (isLocal ? "未导入" : "未填写模型名称")}</span>
        <span>{isLocal
          ? (model.ggufPath?.trim() ? "本地文件已选" : "未选择文件")
          : model.baseUrl ? "接口已填写" : "接口未填写"}</span>
      </div>

      {isLocal && (
        <>
          <LocalGgufImport
            ggufPath={model.ggufPath}
            mmprojPath={model.mmprojPath}
            ollamaName={model.ollamaName}
            useVision={false}
            onPatch={(patch) => onChange(patch)}
            onImported={(name) => onChange(applyChatLocalImport(model, name))}
          />
          <p className="field-hint">
            当前使用：<strong>本机 Ollama</strong>（{model.baseUrl || OLLAMA_DEFAULT_BASE_URL}）
            {model.modelName ? `，模型 ${model.modelName}` : "；导入成功后自动填入模型名"}。
          </p>
        </>
      )}

      {isOllama && (
        <OllamaModelPicker
          baseUrl={model.baseUrl}
          modelName={model.modelName}
          apiKey={model.apiKey || "ollama"}
          onPatch={(patch) => onChange({ ...patch, source: "ollama", apiKey: "ollama", proxyMode: "off" })}
        />
      )}

      {isChat && !isOllama && !isLocal && (
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
            <div className="field">
              <label>Provider Profile</label>
              <select value={model.providerProfile || "openai_compatible"}
                onChange={(e) => onChange({ providerProfile: e.target.value as ChatModel["providerProfile"] })}>
                <option value="openai_compatible">OpenAI 兼容</option>
                <option value="claude_compatible">Claude 兼容</option>
              </select>
              <p className="field-hint">
                按接口协议选择，不按模型名称选择；OpenAI 兼容代理即使运行 Claude 模型也选 OpenAI 兼容。
              </p>
            </div>
          </div>
        </details>
      )}

      {isOllama && (
        <details className="model-advanced">
          <summary>更多设置</summary>
          <div className="model-advanced-body">
            <div className="field">
              <label>显示名称</label>
              <input
                value={model.displayName || ""}
                onChange={(e) => onChange({ displayName: e.target.value })}
                placeholder={model.modelName || "仅用于界面区分"}
              />
            </div>
            <div className="field">
              <label>连接代理</label>
              <select value={model.proxyMode || "off"} onChange={(e) => onChange({ proxyMode: e.target.value as "on" | "off" | "inherit" })}>
                <option value="off">直连</option>
                <option value="on">使用代理</option>
                <option value="inherit">继承全局</option>
              </select>
              <p className="field-hint">Ollama 在本机时请保持直连；回环地址无论如何都不会走全局代理。</p>
            </div>
          </div>
        </details>
      )}

      {isLocal && (
        <details className="model-advanced">
          <summary>更多设置</summary>
          <div className="model-advanced-body">
            <div className="field">
              <label>显示名称</label>
              <input
                value={model.displayName || ""}
                onChange={(e) => onChange({ displayName: e.target.value })}
                placeholder={model.modelName || "仅用于界面区分"}
              />
            </div>
            <div className="field">
              <label>Ollama 服务地址</label>
              <input
                value={model.baseUrl}
                onChange={(e) => onChange({ baseUrl: e.target.value })}
                placeholder={OLLAMA_DEFAULT_BASE_URL}
              />
            </div>
          </div>
        </details>
      )}

      {isLocal ? (
        <div className="model-test-row">
          <button className="btn" type="button" onClick={test} disabled={probe.testing || !model.modelName}>
            {probe.testing ? "测试中…" : "测试模型"}
          </button>
          {!model.modelName && <span className="field-hint">导入到 Ollama 后即可测试。</span>}
          {probe.result && <ProbeResultView result={probe.result} />}
        </div>
      ) : (
        <div className="model-test-row">
          <button className="btn" type="button" onClick={test} disabled={probe.testing}>
            {probe.testing ? "测试中…" : "测试模型"}
          </button>
          {probe.result && <ProbeResultView result={probe.result} />}
        </div>
      )}

      {!isChat && (
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
      )}
    </div>
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

function SourceTabs({ label, value, onChange }: {
  label: string; value: SourceTab; onChange: (next: SourceTab) => void;
}) {
  const items: { id: SourceTab; text: string }[] = [
    { id: "api", text: "API" },
    { id: "ollama", text: "Ollama" },
    { id: "local", text: "本地模型" },
  ];
  return (
    <div className="embedding-mode-tabs" role="group" aria-label={label}>
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          className={`btn${value === item.id ? " primary is-selected" : ""}`}
          aria-pressed={value === item.id}
          onClick={() => onChange(item.id)}
        >{item.text}</button>
      ))}
    </div>
  );
}

export function ModelsPanel({ draft, setDraft }: PanelProps) {
  const [embedProbe, setEmbedProbe] = useState<{ testing?: boolean; result?: ModelProbeResult }>({});
  const [rerankerProbe, setRerankerProbe] = useState<{ testing?: boolean; result?: ModelProbeResult }>({});
  const setEmbed = (patch: Partial<EmbedModel>) =>
    setDraft((d) => ({ ...d, embedModel: { ...d.embedModel, ...patch } }));

  const addChatModel = (source: ChatModelSource) =>
    setDraft((d) => ({ ...d, chatModels: [...d.chatModels, newChatModel(source)] }));
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
              globalProxyAddress(draft), draft.proxyEnabled,
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

  const embedSource = remoteModelSource(draft.embedModel);
  const embedTab: SourceTab = draft.embedModel.mode === "local" ? "local" : embedSource;
  const setEmbedTab = (tab: SourceTab) => {
    if (tab === "local") { setEmbed({ mode: "local" }); return; }
    setEmbed(switchEmbedSource(draft.embedModel, tab));
  };

  const embedProvider = (() => {
    if (draft.embedModel.mode === "local") return "本地模型文件";
    if (embedSource === "ollama") return "本机 Ollama";
    const u = (draft.embedModel.baseUrl || "").toLowerCase();
    if (u.includes("bigmodel.cn")) return "云端智谱";
    if (u.includes("openai.com")) return "云端 OpenAI";
    if (!u.trim()) return "未配置";
    return "自定义 / 中转";
  })();

  const setVlm = (patch: Partial<VlmModel>) =>
    setDraft((d) => ({ ...d, vlmModel: { ...d.vlmModel, ...patch } }));

  const [vlmProbe, setVlmProbe] = useState<{ testing?: boolean; result?: ModelProbeResult }>({});

  const testVlm = async () => {
    setVlmProbe({ testing: true });
    try {
      const result = await probeModel(draft.vlmModel.mode === "local"
        ? { kind: "vlm-local", ggufPath: draft.vlmModel.ggufPath }
        : {
            kind: "vlm",
            baseUrl: draft.vlmModel.baseUrl,
            apiKey: draft.vlmModel.apiKey,
            modelName: draft.vlmModel.modelName,
            proxyUrl: resolveEndpointProxy(
              draft.vlmModel.baseUrl, draft.vlmModel.proxyMode,
              globalProxyAddress(draft), draft.proxyEnabled,
            ),
          });
      setVlmProbe({ result });
    } catch (error) {
      setVlmProbe({ result: { status: "error", message: (error as Error).message, billable: false } });
    }
  };

  const vlmSource = remoteModelSource(draft.vlmModel);
  const vlmTab: SourceTab = draft.vlmModel.mode === "local" ? "local" : vlmSource;
  const setVlmTab = (tab: SourceTab) => {
    if (tab === "local") { setVlm({ mode: "local" }); return; }
    setVlm(switchVlmSource(draft.vlmModel, tab));
  };

  const vlmProvider = (() => {
    if (draft.vlmModel.mode === "local") return "本地模型（GGUF，导入 Ollama）";
    if (vlmSource === "ollama") return "本机 Ollama";
    const u = (draft.vlmModel.baseUrl || "").toLowerCase();
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
          <div className="model-add-group">
            <button className="btn" onClick={() => addChatModel("api")}><Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />API</button>
            <button className="btn" onClick={() => addChatModel("ollama")}><Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />Ollama</button>
            <button className="btn" onClick={() => addChatModel("local")}><Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />本地模型</button>
          </div>
        </div>
        <p className="field-hint" style={{ marginTop: 8 }}>
          智能体的大脑（也用于反推图片）。来源三选一：<strong>云端 API</strong>、<strong>本机 Ollama</strong>、
          <strong>本地 GGUF</strong>（导入本机 Ollama）。可配多个，在对话框左下角图标处切换。
        </p>
        <div style={{ marginTop: 12 }}>
          {draft.chatModels.length === 0 && <p className="field-hint">还没有对话模型：按来源点右上角「+ API / + Ollama / + 本地模型」。</p>}
          {draft.chatModels.map((m) => (
            <ModelCard
              key={m.id}
              model={m}
              kind="chat"
              source={chatModelSource(m)}
              onChange={(p) => updateChatModel(m.id!, p)}
              onRemove={() => removeChatModel(m.id!)}
              globalProxyUrl={globalProxyAddress(draft)}
              globalProxyEnabled={draft.proxyEnabled}
            />
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
              globalProxyUrl={globalProxyAddress(draft)}
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
            <ModelCard key={m.id} model={m} kind="video" onChange={(p) => updateVideoModel(m.id, p as Partial<VideoModel>)} onRemove={() => removeVideoModel(m.id)} globalProxyUrl={globalProxyAddress(draft)} globalProxyEnabled={draft.proxyEnabled} />
          ))}
        </div>
      </div>

      {/* 视觉大模型（Visual CI 验收） */}
      <div className="settings-section model-capability-card">
        <div className="model-capability-head">
          <CapabilityTitle icon={<Eye size={18} />} title="视觉大模型" count={1}
            configured={draft.vlmModel.mode === "local"
              // 本地 GGUF 光有路径还不够——必须先「导入 Ollama」拿到 ollamaName 才真能用，
              // 否则会误显示「已配置」而实际调不动（2026-10-18 用户实锤）。
              ? Boolean(draft.vlmModel.ggufPath && draft.vlmModel.ollamaName)
              : Boolean(draft.vlmModel.baseUrl && draft.vlmModel.modelName)} />
        </div>
        <p className="field-hint" style={{ margin: "0 0 10px" }}>
          用于 Visual CI 自动验收插画（检查人物/动作/场景/噪点）。来源三选一：<strong>云端 API</strong>、
          <strong>本机 Ollama</strong>、<strong>本地模型（GGUF）</strong>——本地文件会导入本机 Ollama 运行，全程不走云。
        </p>
        <SourceTabs label="视觉大模型来源" value={vlmTab} onChange={setVlmTab} />
        <details className="model-advanced embedding-advanced">
          <summary>连接与模型配置</summary>
          <div className="model-advanced-body">
            {vlmTab === "api" && <>
              <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
                {VLM_API_PRESETS.map((p) => (
                  <button key={p.name} className="btn" onClick={() => setVlm({ mode: "remote", source: "api", baseUrl: p.baseUrl, modelName: p.modelName, apiKey: "", proxyMode: "inherit" })}>{p.name}</button>
                ))}
              </div>
              <p style={{ fontSize: 12, margin: "0 0 10px" }}>当前使用：<strong>{vlmProvider}</strong></p>
              <div className="field"><label>API URL</label><input value={draft.vlmModel.baseUrl} onChange={(e) => setVlm({ baseUrl: e.target.value })} placeholder="https://open.bigmodel.cn/api/paas/v4" /></div>
              <div className="field"><label>API Key</label><input type="password" value={draft.vlmModel.apiKey} onChange={(e) => setVlm({ apiKey: e.target.value })} /></div>
              <div className="field">
                <label>连接代理</label>
                <select value={draft.vlmModel.proxyMode || "on"} onChange={(e) => setVlm({ proxyMode: e.target.value as "on" | "off" | "inherit" })}>
                  <option value="on">使用代理</option>
                  <option value="off">直连</option>
                  <option value="inherit">继承全局</option>
                </select>
              </div>
              <div className="field"><label>模型名称</label><input value={draft.vlmModel.modelName} onChange={(e) => setVlm({ modelName: e.target.value })} placeholder="glm-4v-flash / gpt-4o" /></div>
            </>}
            {vlmTab === "ollama" && <>
              <p style={{ fontSize: 12, margin: "0 0 10px" }}>当前使用：<strong>{vlmProvider}</strong></p>
              <OllamaModelPicker
                baseUrl={draft.vlmModel.baseUrl}
                modelName={draft.vlmModel.modelName}
                apiKey={draft.vlmModel.apiKey || "ollama"}
                presets={VLM_OLLAMA_MODELS}
                onPatch={(patch) => setVlm({ mode: "remote", source: "ollama", apiKey: "ollama", proxyMode: "off", ...patch })}
              />
            </>}
            {vlmTab === "local" && <>
              <LocalGgufImport
                ggufPath={draft.vlmModel.ggufPath}
                mmprojPath={draft.vlmModel.mmprojPath}
                ollamaName={draft.vlmModel.ollamaName}
                useVision
                onPatch={(patch) => setVlm(patch)}
                onImported={(name) => setVlm(applyVlmLocalImport(name))}
              />
              <p className="field-hint" style={{ marginTop: 6 }}>
                「本地模型」只是 GGUF 文件入口：导入成功后会自动切到 Ollama 档并填好模型名，之后保存即可使用。
              </p>
            </>}
            {vlmTab !== "local" && (
              <div className="model-test-row">
                <button className="btn" type="button" onClick={testVlm} disabled={vlmProbe.testing}>
                  {vlmProbe.testing ? "检测中…" : "测试视觉模型"}
                </button>
                {vlmProbe.result && <ProbeResultView result={vlmProbe.result} />}
              </div>
            )}
          </div>
        </details>
      </div>

      {/* 嵌入模型 */}
      <div className="settings-section model-capability-card">
        <div className="model-capability-head">
          <CapabilityTitle icon={<Database size={18} />} title="Embedding" count={1}
            configured={draft.embedModel.mode === "local" ? Boolean(draft.embedModel.modelDir) : Boolean(draft.embedModel.baseUrl && draft.embedModel.modelName)} />
        </div>
        <p className="field-hint" style={{ margin: "0 0 10px" }}>
          用于把仓库资料/生成历史向量化检索。来源三选一：<strong>云端 API</strong>（智谱 embedding-3 / OpenAI text-embedding-3）、
          <strong>本机 Ollama</strong>（如 qwen3-embedding）、<strong>本地模型文件</strong>（SentenceTransformer 目录）。
        </p>
        <SourceTabs label="嵌入模型来源" value={embedTab} onChange={setEmbedTab} />
        <details className="model-advanced embedding-advanced">
          <summary>连接、模型目录与 Reranker 高级配置</summary>
          <div className="model-advanced-body">
            {embedTab === "api" && <>
              <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
                {EMBED_API_PRESETS.map((p) => (
                  <button key={p.name} className="btn" onClick={() => setEmbed({ mode: "remote", source: "api", baseUrl: p.baseUrl, modelName: p.modelName, apiKey: "", proxyMode: "inherit" })}>{p.name}</button>
                ))}
              </div>
              <p style={{ fontSize: 12, margin: "0 0 10px" }}>当前使用：<strong>{embedProvider}</strong></p>
              <div className="field"><label>API URL</label><input value={draft.embedModel.baseUrl} onChange={(e) => setEmbed({ baseUrl: e.target.value })} placeholder="https://open.bigmodel.cn/api/paas/v4" /></div>
              <div className="field"><label>API Key</label><input type="password" value={draft.embedModel.apiKey} onChange={(e) => setEmbed({ apiKey: e.target.value })} /></div>
              <div className="field">
                <label>连接代理</label>
                <select value={draft.embedModel.proxyMode || "on"} onChange={(e) => setEmbed({ proxyMode: e.target.value as "on" | "off" | "inherit" })}>
                  <option value="on">使用代理</option>
                  <option value="off">直连</option>
                  <option value="inherit">继承全局</option>
                </select>
              </div>
              <div className="field"><label>模型名称</label><input value={draft.embedModel.modelName} onChange={(e) => setEmbed({ modelName: e.target.value })} placeholder="embedding-3" /></div>
            </>}
            {embedTab === "ollama" && <>
              <p style={{ fontSize: 12, margin: "0 0 10px" }}>当前使用：<strong>{embedProvider}</strong></p>
              <OllamaModelPicker
                baseUrl={draft.embedModel.baseUrl}
                modelName={draft.embedModel.modelName}
                apiKey={draft.embedModel.apiKey || "ollama"}
                presets={EMBED_OLLAMA_MODELS}
                onPatch={(patch) => setEmbed({ mode: "remote", source: "ollama", apiKey: "ollama", proxyMode: "off", ...patch })}
              />
            </>}
            {embedTab === "local" && (
              <div className="field">
                <label>本地嵌入模型文件夹</label>
                <input
                  value={draft.embedModel.modelDir || ""}
                  onChange={(e) => setEmbed({ modelDir: e.target.value })}
                  placeholder="项目目录\\backend\\data\\models\\embedding"
                />
                <p className="field-hint">本地模型文件直接由后端加载（SentenceTransformer 目录），不会调用上方 API 配置。</p>
              </div>
            )}
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
