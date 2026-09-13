import { Plus, Trash2 } from "lucide-react";
import type { PanelProps } from "./GeneralPanel";
import type { StylePreset } from "../../stores/settings";

// 风格模板存档：整段风格文本（画风/结构/负面词），生图时 AI 参照其组织提示词。
// 对话框「提示词风格」下拉可选中启用。此处管理增删改。
export function StylePanel({ draft, setDraft }: PanelProps) {
  const presets = draft.stylePresets || [];

  const add = () =>
    setDraft((d) => ({
      ...d,
      stylePresets: [...(d.stylePresets || []), { id: crypto.randomUUID(), name: "新风格", content: "" }],
    }));
  const upd = (id: string, patch: Partial<StylePreset>) =>
    setDraft((d) => ({
      ...d,
      stylePresets: (d.stylePresets || []).map((p) => (p.id === id ? { ...p, ...patch } : p)),
    }));
  const del = (id: string) =>
    setDraft((d) => ({
      ...d,
      stylePresets: (d.stylePresets || []).filter((p) => p.id !== id),
      imageStyle: d.imageStyle === `preset:${id}` ? "" : d.imageStyle,
    }));

  return (
    <div className="settings-section">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h4 style={{ margin: 0 }}>生图风格存档</h4>
        <button className="btn" onClick={add}><Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />新建</button>
      </div>
      <p className="field-hint" style={{ marginTop: 8 }}>
        每个存档是一整段风格描述（画风、结构、负面词，自由粘贴）。在对话框「提示词风格」下拉里选中即生效，AI 生图时参照它组织提示词。
      </p>
      <div style={{ marginTop: 12 }}>
        {presets.length === 0 && <p className="field-hint">还没有风格存档，点击「新建」。</p>}
        {presets.map((p) => (
          <div className="image-model-card" key={p.id}>
            <div className="row-head">
              <input
                value={p.name}
                onChange={(e) => upd(p.id, { name: e.target.value })}
                placeholder="风格名称"
                style={{ fontWeight: 600, flex: 1, marginRight: 8 }}
              />
              <button className="icon-btn" style={{ background: "#d23b3b" }} onClick={() => del(p.id)}>
                <Trash2 size={14} />
              </button>
            </div>
            <div className="field">
              <label>风格内容</label>
              <textarea
                value={p.content}
                onChange={(e) => upd(p.id, { content: e.target.value })}
                placeholder="粘贴整段风格模板：画风关键词、结构、负面词等"
                rows={5}
                style={{ width: "100%", resize: "vertical" }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
