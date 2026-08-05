import { useEffect, useState } from "react";
import { X, Search, Plus, BadgeCheck, LayoutGrid, Globe, Code2, GraduationCap, TrendingUp, Sparkles } from "lucide-react";
import { searchSmitherySkills, addSmitherySkill, type SmitherySkill, type Skill } from "../../api/skills";

const CATEGORIES = [
  { id: "all", label: "全部", icon: LayoutGrid, q: "" },
  { id: "web", label: "网页搜索", icon: Globe, q: "web search" },
  { id: "research", label: "研究", icon: GraduationCap, q: "research" },
  { id: "finance", label: "金融", icon: TrendingUp, q: "finance" },
  { id: "reasoning", label: "推理", icon: Sparkles, q: "reasoning" },
  { id: "dev", label: "开发", icon: Code2, q: "developer" },
];

// Smithery 技能市场：左筛选 + 右卡片列表（与 MCP 市场同款布局）。
// 添加技能取其 prompt 存为本地技能；若依赖 MCP 服务器则回调提示。
export function SkillsBrowser({ onClose, onAdded }: {
  onClose: () => void;
  onAdded: (skills: Skill[], dependsServers: string[], skillName: string) => void;
}) {
  const [keyword, setKeyword] = useState("");
  const [cat, setCat] = useState("all");
  const [verified, setVerified] = useState(false);
  const [results, setResults] = useState<SmitherySkill[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [adding, setAdding] = useState("");

  const buildQuery = () => {
    const parts: string[] = [];
    if (keyword.trim()) parts.push(keyword.trim());
    const c = CATEGORIES.find((x) => x.id === cat);
    if (c?.q) parts.push(c.q);
    if (verified) parts.push("is:verified");
    return parts.join(" ");
  };

  const doSearch = async () => {
    setLoading(true); setErr("");
    try {
      const r = await searchSmitherySkills(buildQuery(), 1, 30);
      if (r.ok) {
        setResults(r.skills);
        setTotal((r.pagination as { totalCount?: number })?.totalCount ?? r.skills.length);
      } else setErr(r.error || "搜索失败");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { doSearch(); /* eslint-disable-next-line */ }, [cat, verified]);

  const add = async (s: SmitherySkill) => {
    setAdding(s.slug);
    try {
      const r = await addSmitherySkill(s.namespace, s.slug, s.displayName);
      if (r.ok) onAdded(r.skills, r.dependsServers || [], s.displayName || s.slug);
      else setErr(r.error || "添加失败");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setAdding("");
    }
  };

  // APPEND_SKILLS_BROWSER_RENDER
  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal smithery-modal" onClick={(e) => e.stopPropagation()}>
        <div className="settings-head">
          <h3 style={{ margin: 0 }}>Smithery 技能市场</h3>
          <button className="icon-btn" style={{ background: "transparent", color: "var(--text)" }} onClick={onClose}><X size={18} /></button>
        </div>
        <div className="smithery-body">
          <aside className="smithery-filters">
            <div className="smithery-filter-group">
              <div className="smithery-filter-title">筛选</div>
              <button className={verified ? "smithery-filter-item active" : "smithery-filter-item"} onClick={() => setVerified((v) => !v)}>
                <BadgeCheck size={15} /> 已认证
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
          <div className="smithery-results">
            <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
              <input value={keyword} onChange={(e) => setKeyword(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") doSearch(); }} placeholder="搜索技能…" style={{ flex: 1 }} />
              <button className="btn primary" onClick={doSearch} disabled={loading}>
                <Search size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />{loading ? "搜索中…" : "搜索"}
              </button>
            </div>
            <div className="smithery-count">
              {loading ? "搜索中…" : `找到 ${total} 个技能`}
              {err && <span style={{ color: "#d23b3b", marginLeft: 10 }}>{err}</span>}
            </div>
            <div className="smithery-list">
              {results.map((s) => (
                <div key={`${s.namespace}/${s.slug}`} className="smithery-card">
                  <div className="smithery-icon smithery-icon-ph">{(s.displayName || "?")[0]}</div>
                  <div className="smithery-card-main">
                    <div className="smithery-card-head">
                      <strong>{s.displayName || s.slug}</strong>
                      {s.verified && <BadgeCheck size={14} style={{ color: "var(--accent)" }} />}
                    </div>
                    <div className="smithery-card-meta">
                      {s.namespace}/{s.slug}
                      {typeof s.totalActivations === "number" ? ` · ${s.totalActivations} 次激活` : ""}
                      {(s.servers?.length ?? 0) > 0 ? ` · 依赖 ${s.servers!.length} 个 MCP` : ""}
                    </div>
                    <div className="smithery-card-desc">{s.description}</div>
                  </div>
                  <button className="btn" onClick={() => add(s)} disabled={adding === s.slug}>
                    <Plus size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />{adding === s.slug ? "添加中…" : "添加"}
                  </button>
                </div>
              ))}
              {!loading && results.length === 0 && <p className="field-hint">没有匹配的技能。</p>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
