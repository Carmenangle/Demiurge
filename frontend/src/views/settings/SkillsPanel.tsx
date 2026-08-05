import { useEffect, useState } from "react";
import { Plus, Trash2, Save, Store } from "lucide-react";
import { listSkills, saveSkills, type Skill } from "../../api/skills";
import { SkillsBrowser } from "./SkillsBrowser";

// 技能扩展：可开关的提示词注入片段，启用后拼进智能体 system_prompt。
// 独立于 settings 草稿（存后端 data/skills.json），编辑后点保存整体写回。
export function SkillsPanel() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [browsing, setBrowsing] = useState(false);   // Smithery 技能市场弹窗
  const [depNote, setDepNote] = useState("");         // 添加技能后的 MCP 依赖提示

  useEffect(() => {
    listSkills().then(setSkills).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const add = () =>
    setSkills((s) => [...s, { id: crypto.randomUUID(), name: "新技能", enabled: true, prompt_fragment: "" }]);
  const upd = (id: string, patch: Partial<Skill>) =>
    setSkills((s) => s.map((x) => (x.id === id ? { ...x, ...patch } : x)));
  const del = (id: string) => setSkills((s) => s.filter((x) => x.id !== id));

  const save = async () => {
    setSaving(true);
    try {
      const r = await saveSkills(skills);
      setSkills(r);
      setSaved(true);
      setTimeout(() => setSaved(false), 1800);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="settings-section"><p className="field-hint">加载中…</p></div>;

  return (
    <div className="settings-section">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h4 style={{ margin: 0 }}>技能扩展</h4>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={() => setBrowsing(true)}><Store size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />Smithery 市场</button>
          <button className="btn" onClick={add}><Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />新建</button>
          <button className="btn primary" onClick={save} disabled={saving}>
            <Save size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />{saving ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
      <p className="field-hint" style={{ marginTop: 8 }}>
        技能是可开关的提示词片段，启用后注入智能体，自定义 AI 行为（如「生图时总加高质量负面词」「回答用专业术语」）。纯提示词层，无副作用。改动需点「保存」。
        {saved && <span className="settings-saved" style={{ marginLeft: 8 }}>已保存</span>}
      </p>
      <div style={{ marginTop: 12 }}>
        {skills.length === 0 && <p className="field-hint">还没有技能，点击「新建」。</p>}
        {skills.map((s) => (
          <div className="image-model-card" key={s.id}>
            <div className="row-head">
              <label style={{ display: "flex", alignItems: "center", gap: 6, flex: 1 }}>
                <input type="checkbox" checked={s.enabled} onChange={(e) => upd(s.id, { enabled: e.target.checked })} />
                <input value={s.name} onChange={(e) => upd(s.id, { name: e.target.value })} placeholder="技能名称" style={{ fontWeight: 600, flex: 1 }} />
              </label>
              <button className="icon-btn" style={{ background: "#d23b3b" }} onClick={() => del(s.id)}><Trash2 size={14} /></button>
            </div>
            <div className="field">
              <label>提示词片段</label>
              <textarea
                value={s.prompt_fragment}
                onChange={(e) => upd(s.id, { prompt_fragment: e.target.value })}
                placeholder="例如：生图提示词里总是追加 masterpiece, best quality 等质量词"
                rows={3}
                style={{ width: "100%", resize: "vertical" }}
              />
            </div>
          </div>
        ))}
      </div>
      {depNote && (
        <p className="field-hint" style={{ marginTop: 10, padding: "8px 12px", background: "var(--surface-2, rgba(128,128,128,0.1))", borderRadius: 8 }}>
          {depNote}
        </p>
      )}
      {browsing && (
        <SkillsBrowser
          onClose={() => setBrowsing(false)}
          onAdded={(list, deps, name) => {
            setSkills(list);
            if (deps.length > 0) {
              setDepNote(`技能「${name}」建议配合以下 MCP 服务器使用：${deps.join("、")}。请到「MCP 服务器 → Smithery 市场」搜索添加。`);
            } else {
              setDepNote(`技能「${name}」已添加到列表（已启用）。`);
            }
          }}
        />
      )}
    </div>
  );
}
