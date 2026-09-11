import { useEffect, useState } from "react";
import { X, Search, Plus, BadgeCheck, ShieldCheck, Globe, MousePointerClick, GraduationCap, TrendingUp, Code2, LayoutGrid, Sparkles } from "lucide-react";
import { searchSmithery, addSmithery, type SmitheryServer, type McpServer } from "../../api/mcp";

// 分类：点击往 q 拼关键词搜索（Smithery 无独立分类参数，靠全文检索）
const CATEGORIES = [
  { id: "all", label: "全部", icon: LayoutGrid, q: "" },
  { id: "web", label: "网页搜索", icon: Globe, q: "web search" },
  { id: "browser", label: "浏览器自动化", icon: MousePointerClick, q: "browser automation" },
  { id: "academic", label: "学术研究", icon: GraduationCap, q: "academic research" },
  { id: "finance", label: "金融", icon: TrendingUp, q: "finance" },
  { id: "reasoning", label: "推理", icon: Sparkles, q: "reasoning" },
  { id: "dev", label: "开发工具", icon: Code2, q: "developer tools" },
];

// Smithery 市场浏览：左筛选栏 + 右结果卡列表（对标官网）。
// 过滤走 Smithery 的 q 语法：is:verified / is:deployed(Smithery managed) 等。
export function SmitheryBrowser({ onClose, onAdded }: {
  onClose: () => void;
  onAdded: (servers: McpServer[]) => void;
}) {
  const [keyword, setKeyword] = useState("");       // 用户输入的搜索词
  const [cat, setCat] = useState("all");            // 选中分类
  const [verified, setVerified] = useState(false);  // 仅认证
  const [managed, setManaged] = useState(false);    // 仅 Smithery 托管
  const [results, setResults] = useState<SmitheryServer[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [adding, setAdding] = useState("");

  // 拼最终查询串：关键词 + 分类词 + 过滤语法
  const buildQuery = () => {
    const parts: string[] = [];
    if (keyword.trim()) parts.push(keyword.trim());
    const c = CATEGORIES.find((x) => x.id === cat);
    if (c?.q) parts.push(c.q);
    if (verified) parts.push("is:verified");
    if (managed) parts.push("is:deployed");
    return parts.join(" ");
  };

  const doSearch = async () => {
    setLoading(true); setErr("");
    try {
      const r = await searchSmithery(buildQuery(), 1, 30);
      if (r.ok) {
        setResults(r.servers);
        setTotal((r.pagination as { totalCount?: number })?.totalCount ?? r.servers.length);
      } else setErr(r.error || "搜索失败");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  // 分类/过滤变化时自动重搜；首次进来也搜一次
  useEffect(() => { doSearch(); /* eslint-disable-next-line */ }, [cat, verified, managed]);

  const add = async (s: SmitheryServer) => {
    setAdding(s.qualifiedName);
    try {
      const list = await addSmithery(s.qualifiedName, s.displayName || s.qualifiedName);
      onAdded(list);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setAdding("");
    }
  };

  // APPEND_SMITHERY_RENDER
  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal smithery-modal" onClick={(e) => e.stopPropagation()}>
        <div className="settings-head">
          <h3 style={{ margin: 0 }}>Smithery 市场</h3>
          <button className="icon-btn" style={{ background: "transparent", color: "var(--text)" }} onClick={onClose}><X size={18} /></button>
        </div>

        <div className="smithery-body">
          {/* 左：筛选栏 */}
          <aside className="smithery-filters">
            <div className="smithery-filter-group">
              <div className="smithery-filter-title">筛选</div>
              <button className={verified ? "smithery-filter-item active" : "smithery-filter-item"} onClick={() => setVerified((v) => !v)}>
                <BadgeCheck size={15} /> 已认证
              </button>
              <button className={managed ? "smithery-filter-item active" : "smithery-filter-item"} onClick={() => setManaged((v) => !v)}>
                <ShieldCheck size={15} /> Smithery 托管
              </button>
            </div>
            <div className="smithery-filter-group">
              <div className="smithery-filter-title">分类</div>
              {CATEGORIES.map((c) => (
                <button key={c.id} className={cat === c.id ? "smithery-filter-item active" : "smithery-filter-item"} onClick={() => setCat(c.id)}>
                  <c.icon size={15} /> {c.label}
                </button>
              ))}
            </div>
          </aside>

          {/* 右：搜索 + 结果 */}
          <div className="smithery-results">
            <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
              <input
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") doSearch(); }}
                placeholder="搜索 MCP 服务器…"
                style={{ flex: 1 }}
              />
              <button className="btn primary" onClick={doSearch} disabled={loading}>
                <Search size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />{loading ? "搜索中…" : "搜索"}
              </button>
            </div>
            <div className="smithery-count">
              {loading ? "搜索中…" : `找到 ${total} 个服务器`}
              {err && <span style={{ color: "#d23b3b", marginLeft: 10 }}>{err}</span>}
            </div>
            <div className="smithery-list">
              {results.map((s) => (
                <div key={s.qualifiedName} className="smithery-card">
                  {s.iconUrl
                    ? <img src={s.iconUrl} alt="" className="smithery-icon" />
                    : <div className="smithery-icon smithery-icon-ph">{(s.displayName || "?")[0]}</div>}
                  <div className="smithery-card-main">
                    <div className="smithery-card-head">
                      <strong>{s.displayName || s.qualifiedName}</strong>
                      {s.verified && <BadgeCheck size={14} style={{ color: "var(--accent)" }} />}
                    </div>
                    <div className="smithery-card-meta">
                      {s.qualifiedName}{typeof s.useCount === "number" ? ` · ${formatUses(s.useCount)} 次使用` : ""}
                    </div>
                    <div className="smithery-card-desc">{s.description}</div>
                  </div>
                  <button className="btn" onClick={() => add(s)} disabled={adding === s.qualifiedName}>
                    <Plus size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />{adding === s.qualifiedName ? "添加中…" : "添加"}
                  </button>
                </div>
              ))}
              {!loading && results.length === 0 && <p className="field-hint">没有匹配的服务器。</p>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function formatUses(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(2)}k` : String(n);
}
