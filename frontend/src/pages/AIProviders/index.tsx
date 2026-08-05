import { useEffect, useMemo, useState } from "react";
import {
  addManualProviderModel,
  createAIProvider,
  deleteAIProvider,
  discoverProviderModels,
  listAIProviders,
} from "../../api/aiProviders";
import type { AIProvider } from "../../types";

export function AIProviders() {
  const [name, setName] = useState("OpenAI Compatible");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [manualModel, setManualModel] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [providers, setProviders] = useState<AIProvider[]>([]);
  const [message, setMessage] = useState("");

  const modelOptions = useMemo(() => {
    const merged = [...models];
    if (manualModel.trim() && !merged.includes(manualModel.trim())) {
      merged.push(manualModel.trim());
    }
    return merged;
  }, [manualModel, models]);

  async function refreshProviders() {
    setProviders(await listAIProviders());
  }

  useEffect(() => {
    refreshProviders().catch((error) => setMessage(String(error)));
  }, []);

  async function handleDiscover() {
    setMessage("正在尝试获取模型列表...");
    const result = await discoverProviderModels(baseUrl, apiKey);
    if (result.ok) {
      setModels(result.models);
      setSelectedModel(result.models[0] ?? "");
      setMessage(`已从 ${result.source} 获取 ${result.models.length} 个模型`);
      return;
    }
    setModels([]);
    setMessage(`未能自动获取模型：${result.error}。可以手动输入模型名。`);
  }

  async function handleSave() {
    const provider = await createAIProvider({
      name,
      base_url: baseUrl,
      api_key: apiKey,
      provider_type: "openai_compatible",
      default_model: selectedModel || manualModel,
      enabled: true,
      models: modelOptions,
    });
    if (manualModel.trim()) {
      await addManualProviderModel(provider.id, manualModel.trim());
    }
    setMessage("AI 供应商已保存");
    await refreshProviders();
  }

  async function handleDelete(providerId: string) {
    await deleteAIProvider(providerId);
    await refreshProviders();
  }

  return (
    <section className="stack">
      <h2>AI 供应商配置</h2>
      <label>
        名称
        <input value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      <label>
        URL
        <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.example.com/v1" />
      </label>
      <label>
        API Key
        <input value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="sk-..." type="password" />
      </label>
      <button onClick={handleDiscover} type="button">获取模型列表</button>
      <label>
        手动模型名
        <input value={manualModel} onChange={(event) => setManualModel(event.target.value)} placeholder="模型列表没有时手动输入" />
      </label>
      {modelOptions.length > 0 && (
        <label>
          默认模型
          <select value={selectedModel} onChange={(event) => setSelectedModel(event.target.value)}>
            {modelOptions.map((model) => (
              <option key={model}>{model}</option>
            ))}
          </select>
        </label>
      )}
      <button onClick={handleSave} type="button">保存供应商</button>
      {message && <p>{message}</p>}

      <div className="list">
        {providers.map((provider) => (
          <article className="row" key={provider.id}>
            <div>
              <strong>{provider.name}</strong>
              <p>{provider.base_url}</p>
              <p>默认模型：{provider.default_model || "未设置"}</p>
            </div>
            <button onClick={() => handleDelete(provider.id)} type="button">删除</button>
          </article>
        ))}
      </div>
    </section>
  );
}