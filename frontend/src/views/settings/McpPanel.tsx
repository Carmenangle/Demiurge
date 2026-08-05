import { useEffect, useState } from "react";
import { Plus, Trash2, Plug, Save, Store } from "lucide-react";
import { listMcpServers, saveMcpServers, testMcpServer, type McpServer } from "../../api/mcp";
import { SmitheryBrowser } from "./SmitheryBrowser";

// MCP 服务器管理：独立于 settings 草稿（存后端 data/mcp_config.json）。
// 编辑本地列表 → 点保存整体写回后端。测试按钮显示该服务器暴露的工具。
export function McpPanel() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  // 每个服务器的测试结果：id -> {ok, tools, error, testing}
  const [probe, setProbe] = useState<Record<string, { ok?: boolean; tools?: string[]; error?: string; testing?: boolean }>>({});
  const [browsing, setBrowsing] = useState(false);  // Smithery 市场弹窗

  useEffect(() => {
    listMcpServers().then(setServers).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const add = () =>
    setServers((s) => [...s, { id: crypto.randomUUID(), name: "新服务器", type: "stdio", command: "", args: [], url: "", enabled: true }]);
  const upd = (id: string, patch: Partial<McpServer>) =>
    setServers((s) => s.map((x) => (x.id === id ? { ...x, ...patch } : x)));
  const del = (id: string) => setServers((s) => s.filter((x) => x.id !== id));

  const save = async () => {
    setSaving(true);
    try {
      const r = await saveMcpServers(servers);
      setServers(r);
      setSaved(true);
      setTimeout(() => setSaved(false), 1800);
    } finally {
      setSaving(false);
    }
  };

  const test = async (srv: McpServer) => {
    setProbe((p) => ({ ...p, [srv.id]: { testing: true } }));
    try {
      const r = await testMcpServer(srv);
      setProbe((p) => ({ ...p, [srv.id]: { ok: r.ok, tools: r.tools, error: r.error } }));
    } catch (e) {
      setProbe((p) => ({ ...p, [srv.id]: { ok: false, error: (e as Error).message } }));
    }
  };

  // APPEND_MCP_RENDER
  if (loading) return <div className="settings-section"><p className="field-hint">加载中…</p></div>;

  return (
    <div className="settings-section">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h4 style={{ margin: 0 }}>MCP 服务器</h4>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={() => setBrowsing(true)}><Store size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />Smithery 市场</button>
          <button className="btn" onClick={add}><Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />添加</button>
          <button className="btn primary" onClick={save} disabled={saving}>
            <Save size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />{saving ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
      <p className="field-hint" style={{ marginTop: 8 }}>
        接入 MCP 服务器可给智能体挂载外部工具（文件系统、数据库、浏览器等）。stdio=本地命令，sse=远程 URL。改动需点「保存」生效。
        {saved && <span className="settings-saved" style={{ marginLeft: 8 }}>已保存</span>}
      </p>
      <div style={{ marginTop: 12 }}>
        {servers.length === 0 && <p className="field-hint">还没有 MCP 服务器，点击「添加」。</p>}
        {servers.map((s) => {
          const pr = probe[s.id] || {};
          return (
            <div className="image-model-card" key={s.id}>
              <div className="row-head">
                <label style={{ display: "flex", alignItems: "center", gap: 6, flex: 1 }}>
                  <input type="checkbox" checked={s.enabled} onChange={(e) => upd(s.id, { enabled: e.target.checked })} />
                  <input value={s.name} onChange={(e) => upd(s.id, { name: e.target.value })} placeholder="服务器名称" style={{ fontWeight: 600, flex: 1 }} />
                </label>
                <button className="icon-btn" style={{ background: "#d23b3b" }} onClick={() => del(s.id)}><Trash2 size={14} /></button>
              </div>
              <div className="field">
                <label>类型</label>
                <select value={s.type} onChange={(e) => upd(s.id, { type: e.target.value as "stdio" | "sse" })} style={{ width: "100%" }}>
                  <option value="stdio">stdio（本地命令）</option>
                  <option value="sse">sse（远程 URL）</option>
                </select>
              </div>
              {s.type === "stdio" ? (
                <>
                  <div className="field">
                    <label>命令</label>
                    <input value={s.command} onChange={(e) => upd(s.id, { command: e.target.value })} placeholder="npx" />
                  </div>
                  <div className="field">
                    <label>参数（空格分隔）</label>
                    <input
                      value={(s.args || []).join(" ")}
                      onChange={(e) => upd(s.id, { args: e.target.value.split(/\s+/).filter(Boolean) })}
                      placeholder="-y @modelcontextprotocol/server-filesystem D:\\data"
                    />
                  </div>
                </>
              ) : (
                <div className="field">
                  <label>服务器 URL</label>
                  <input value={s.url} onChange={(e) => upd(s.id, { url: e.target.value })} placeholder="http://127.0.0.1:8000/sse" />
                </div>
              )}
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
                <button className="btn" onClick={() => test(s)} disabled={pr.testing}>
                  <Plug size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />{pr.testing ? "测试中…" : "测试连接"}
                </button>
                {pr.ok === true && <span className="settings-saved">✓ {pr.tools?.length || 0} 个工具：{(pr.tools || []).join(", ")}</span>}
                {pr.ok === false && <span style={{ color: "#d23b3b", fontSize: 12 }}>✗ {pr.error}</span>}
              </div>
            </div>
          );
        })}
      </div>
      {browsing && (
        <SmitheryBrowser
          onClose={() => setBrowsing(false)}
          onAdded={(list) => { setServers(list); }}
        />
      )}
    </div>
  );
}
