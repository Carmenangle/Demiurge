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

      <h4 style={{ marginTop: 22 }}>上网素材源鉴权</h4>
      <p className="field-hint" style={{ margin: "0 0 10px" }}>
        用于「资产管理 → 上网素材」里的 pixiv / danbooru 市场搜索与下载。
      </p>
      <div className="field">
        <label>pixiv Refresh Token</label>
        <input
          type="password"
          value={draft.pixivRefreshToken || ""}
          onChange={(e) => setDraft((d) => ({ ...d, pixivRefreshToken: e.target.value }))}
          placeholder="选填；pixiv 市场搜索/下载需要"
        />
        <p className="field-hint">
          获取方式：命令行运行 <code>uvx gppt login -j</code>（或参考 pixivpy3 的 auth 脚本），
          复制返回 JSON 里的 refresh_token。
          <br />
          ⚠ pixiv 是 OAuth 令牌（账号身份已编码在 token 内），**不需要**另填用户名。
        </p>
      </div>
      <div className="field">
        <label>danbooru 用户名</label>
        <input
          type="text"
          value={draft.danbooruLogin || ""}
          onChange={(e) => setDraft((d) => ({ ...d, danbooruLogin: e.target.value }))}
          placeholder="选填；与 API Key 配对使用"
        />
      </div>
      <div className="field">
        <label>danbooru API Key</label>
        <input
          type="password"
          value={draft.danbooruApiKey || ""}
          onChange={(e) => setDraft((d) => ({ ...d, danbooruApiKey: e.target.value }))}
          placeholder="选填；需与上方用户名配对填写"
        />
        <p className="field-hint">
          danbooru 鉴权是<b>用户名 + API Key 两个字段配对</b>（Basic 语义），只填 Key 不生效；
          两者都留空=匿名搜索（公开 API 可用，限额较低）。Key 在 danbooru
          「My Account → API Key」获取。
        </p>
        {!!draft.danbooruApiKey && !/^[0-9a-f]{32}$/i.test(draft.danbooruApiKey.trim()) && (
          <p className="field-hint" style={{ color: "var(--danger, #c0392b)" }}>
            ⚠ 这个值的格式不像 danbooru API Key（应为 32 位十六进制字符）——收藏、
            收藏夹浏览的「已收藏」标记、额度提升都会失效。请到 danbooru
            「My Account → API Key」重新复制完整 Key。
          </p>
        )}
      </div>
    </div>
  );
}
