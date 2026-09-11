import type { PanelProps } from "./GeneralPanel";

export function TokensPanel({ draft, setDraft }: PanelProps) {
  return (
    <div className="settings-section">
      <h4>模型下载鉴权</h4>
      <p className="field-hint" style={{ margin: "0 0 10px" }}>
        下载需登录的模型时填写；公开模型可留空。下载功能在左侧「模型下载」页。
      </p>
      <div className="field">
        <label>HuggingFace Token</label>
        <input type="password" value={draft.hfToken} onChange={(e) => setDraft((d) => ({ ...d, hfToken: e.target.value }))} placeholder="hf_..." />
      </div>
      <div className="field">
        <label>Civitai API Key</label>
        <input type="password" value={draft.civitaiToken} onChange={(e) => setDraft((d) => ({ ...d, civitaiToken: e.target.value }))} />
      </div>
      <div className="field">
        <label>Smithery API Key</label>
        <input
          type="password"
          value={draft.smitheryKey || ""}
          onChange={(e) => setDraft((d) => ({ ...d, smitheryKey: e.target.value }))}
          placeholder="从 smithery.ai 获取，用于浏览/连接 MCP 市场服务器"
        />
        <p className="field-hint">在「扩展 → MCP 服务器 → Smithery 市场」浏览和一键添加服务器时使用。</p>
      </div>
    </div>
  );
}
