import { useState, type ReactNode } from "react";
import { ListChecks, Search, X } from "lucide-react";
import { discoverProviderModels } from "../api/aiProviders";
import { filterModelNames } from "../lib/modelSearch";

/**
 * 本机 Ollama 的模型选择器：服务地址 + 已安装模型名。
 * 只管「读列表/选名字」，不写来源与代理默认值——那些由所属能力卡决定。
 */
export function OllamaModelPicker({
  baseUrl, modelName, apiKey = "ollama", urlPlaceholder = "http://localhost:11434/v1",
  presets = [], onPatch, urlExtra,
}: {
  baseUrl: string;
  modelName: string;
  apiKey?: string;
  urlPlaceholder?: string;
  /** 常用模型名快捷按钮（只是名字提示，不保证本机已安装）。 */
  presets?: string[];
  /** 地址输入框下方的额外说明/操作（联机手机的回环地址警示用）。 */
  urlExtra?: ReactNode;
  onPatch: (patch: { baseUrl?: string; modelName?: string }) => void;
}) {
  const [models, setModels] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const discover = async () => {
    if (!baseUrl.trim()) { setErr("请先填 Ollama 服务地址"); return; }
    setLoading(true); setErr("");
    try {
      const r = await discoverProviderModels(baseUrl, apiKey);
      if (r.ok) { setModels(r.models); setQuery(""); }
      else setErr(r.error || "读取失败，请确认 Ollama 已启动");
    } catch (error) {
      setErr((error as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const filtered = filterModelNames(models, query);

  return <>
    <div className="field">
      <label>Ollama 服务地址</label>
      <input
        value={baseUrl}
        onChange={(event) => onPatch({ baseUrl: event.target.value })}
        placeholder={urlPlaceholder}
      />
      <p className="field-hint">本机 Ollama 默认 {urlPlaceholder}；局域网 Ollama 填对应 IP。回环地址会自动直连，不走全局代理。</p>
      {urlExtra}
    </div>
    {presets.length > 0 && (
      <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
        {presets.map((name) => (
          <button key={name} className="btn" type="button" onClick={() => onPatch({ modelName: name })}>{name}</button>
        ))}
      </div>
    )}
    <div className="field">
      <label>Ollama 模型名称</label>
      <div style={{ display: "flex", gap: 6 }}>
        <input
          value={modelName}
          onChange={(event) => onPatch({ modelName: event.target.value })}
          placeholder="qwen3vl:8b / qwen3-embedding:latest"
          style={{ flex: 1 }}
        />
        <button className="btn" type="button" onClick={discover} disabled={loading} title="读取本机 Ollama 已安装的模型">
          <ListChecks size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          {loading ? "读取中…" : "读取本机模型"}
        </button>
      </div>
      {err && <p className="field-hint" style={{ color: "#d23b3b" }}>{err}</p>}
      {models.length > 0 && (
        <div className="model-list-picker">
          <div className="model-list-tools">
            <div className="model-list-search">
              <Search size={14} aria-hidden="true" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索模型名称…"
                aria-label="搜索 Ollama 模型名称"
              />
              {query && (
                <button type="button" onClick={() => setQuery("")} title="清空搜索" aria-label="清空模型搜索">
                  <X size={14} />
                </button>
              )}
            </div>
            <span>{filtered.length}/{models.length}</span>
          </div>
          <select value="" onChange={(event) => { if (event.target.value) onPatch({ modelName: event.target.value }); }}>
            <option value="">{filtered.length > 0 ? "— 选择模型 —" : "— 没有匹配的模型 —"}</option>
            {filtered.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </div>
      )}
    </div>
  </>;
}
