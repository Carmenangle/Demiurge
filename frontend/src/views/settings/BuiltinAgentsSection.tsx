import { useEffect, useState } from "react";
import { Save, RotateCcw } from "lucide-react";
import {
  listBuiltinAgents, saveBuiltinOverrides,
  type BuiltinAgent, type BuiltinOverrides,
} from "../../api/agents";

const KIND_LABEL: Record<string, string> = {
  llm: "语言模型", rules: "规则引擎", specialist: "专家（只读）",
};
const FIELD_LABEL: Record<string, string> = {
  systemPrompt: "系统提示词", temperature: "温度",
  rollInstruction: "命运骰点规则（正文 <roll> 判定，清空则不掷骰）",
  topP: "topP（0~1，留空用模型默认）", maxTokens: "最大 tokens（留空用模型默认）",
  gate: "启用概率 gate（0=关闭该 Agent，>0 才按概率触发）",
  gateFloor: "门控好感度门槛", gateBaseRate: "门控基础概率", tiers: "好感度档位锚点",
};

// ③ 内置智能体分区：展示图里所有默认 Agent（调度主管/剧情推进/角色主导/裁判/生图专家等）
// + 默认参数 + 绑定工具模块，并对高价值字段（提示词/温度/裁判旋钮）开放覆盖。
export function BuiltinAgentsSection() {
  const [agents, setAgents] = useState<BuiltinAgent[]>([]);
  const [draft, setDraft] = useState<Record<string, Record<string, unknown>>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => { void reload(); }, []);

  async function reload() {
    setLoading(true);
    try {
      const list = await listBuiltinAgents();
      setAgents(list);
      // 草稿初始化为当前生效值（含已存覆盖）
      const d: Record<string, Record<string, unknown>> = {};
      for (const a of list) d[a.id] = { ...a.effective };
      setDraft(d);
    } catch { /* 后端离线：留空列表 */ } finally { setLoading(false); }
  }

  const setField = (id: string, field: string, value: unknown) =>
    setDraft((p) => ({ ...p, [id]: { ...p[id], [field]: value } }));

  // 只把「与默认不同」的字段作为覆盖提交，使删改后能回落默认
  function buildOverrides(): BuiltinOverrides {
    const out: BuiltinOverrides = {};
    for (const a of agents) {
      const patch: Record<string, unknown> = {};
      for (const f of a.editable) {
        const cur = draft[a.id]?.[f];
        if (JSON.stringify(cur) !== JSON.stringify(a.defaults[f])) patch[f] = cur;
      }
      if (Object.keys(patch).length) out[a.id] = patch;
    }
    return out;
  }

  const save = async () => {
    setSaving(true);
    try {
      const list = await saveBuiltinOverrides(buildOverrides());
      setAgents(list);
      const d: Record<string, Record<string, unknown>> = {};
      for (const a of list) d[a.id] = { ...a.effective };
      setDraft(d);
      setSaved(true);
      setTimeout(() => setSaved(false), 1800);
    } finally { setSaving(false); }
  };

  const resetOne = (a: BuiltinAgent) =>
    setDraft((p) => ({ ...p, [a.id]: { ...a.defaults } }));

  if (loading) return <p className="field-hint">加载内置智能体…</p>;

  return (
    <div className="settings-subsection">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h4 style={{ margin: 0 }}>内置智能体（默认行为）</h4>
        <button className="btn primary" onClick={save} disabled={saving}>
          <Save size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          {saving ? "保存中…" : "保存内置改动"}
          {saved && <span className="settings-saved" style={{ marginLeft: 8 }}>已保存</span>}
        </button>
      </div>
      <p className="field-hint" style={{ marginTop: 8 }}>
        这些是剧情/生图流程里内置的智能体（调度主管、剧情推进/角色主导、裁判、各生图专家等）。
        语言模型类可改系统提示词和温度；裁判是纯规则引擎，可调门控概率与好感度档位；专家类只展示默认行为与绑定工具。
        改动全局生效，留空/还原即回退默认；对话时选中的自定义智能体预设优先级更高。
      </p>
      {agents.map((a) => {
        const d = draft[a.id] || {};
        const dirty = a.editable.some(
          (f) => JSON.stringify(d[f]) !== JSON.stringify(a.defaults[f]),
        );
        return (
          <div key={a.id} className="field" style={{
            border: "1px solid var(--border, #ccc)", borderRadius: 8, padding: 12, marginTop: 10,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <strong>{a.name}</strong>
              <span className="field-hint" style={{
                border: "1px solid var(--border,#ccc)", borderRadius: 6, padding: "1px 6px", fontSize: 12,
              }}>{KIND_LABEL[a.kind] || a.kind}</span>
              {dirty && <span className="settings-saved" style={{ fontSize: 12 }}>已改</span>}
              {a.editable.length > 0 && (
                <button className="btn" style={{ marginLeft: "auto", padding: "2px 8px" }}
                  onClick={() => resetOne(a)} title="还原此智能体为默认">
                  <RotateCcw size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />还原默认
                </button>
              )}
            </div>
            <p className="field-hint" style={{ marginTop: 4 }}>{a.role}</p>
            {a.tools.length > 0 && (
              <p className="field-hint" style={{ marginTop: 2 }}>
                绑定工具：{a.tools.join(" · ")}
              </p>
            )}
            {a.editable.includes("systemPrompt") && (
              <div style={{ marginTop: 8 }}>
                <label className="field-hint">{FIELD_LABEL.systemPrompt}</label>
                <textarea
                  value={String(d.systemPrompt ?? "")} rows={5} style={{ width: "100%" }}
                  onChange={(e) => setField(a.id, "systemPrompt", e.target.value)}
                />
              </div>
            )}
            {a.editable.includes("rollInstruction") && (
              <div style={{ marginTop: 8 }}>
                <label className="field-hint">{FIELD_LABEL.rollInstruction}</label>
                <textarea
                  value={String(d.rollInstruction ?? "")} rows={6} style={{ width: "100%" }}
                  onChange={(e) => setField(a.id, "rollInstruction", e.target.value)}
                />
              </div>
            )}
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 8 }}>
              {a.editable.includes("temperature") && (
                <div className="field" style={{ margin: 0 }}>
                  <label className="field-hint">{FIELD_LABEL.temperature}</label>
                  <input type="number" min={0} max={2} step={0.1}
                    value={Number(d.temperature ?? 0)}
                    onChange={(e) => setField(a.id, "temperature", Number(e.target.value))} />
                </div>
              )}
              {a.editable.includes("topP") && (
                <div className="field" style={{ margin: 0 }}>
                  <label className="field-hint">{FIELD_LABEL.topP}</label>
                  <input type="number" min={0} max={1} step={0.05}
                    value={d.topP == null ? "" : Number(d.topP)}
                    onChange={(e) => setField(a.id, "topP", e.target.value === "" ? null : Number(e.target.value))} />
                </div>
              )}
              {a.editable.includes("maxTokens") && (
                <div className="field" style={{ margin: 0 }}>
                  <label className="field-hint">{FIELD_LABEL.maxTokens}</label>
                  <input type="number" min={1} step={1}
                    value={d.maxTokens == null ? "" : Number(d.maxTokens)}
                    onChange={(e) => setField(a.id, "maxTokens", e.target.value === "" ? null : Number(e.target.value))} />
                </div>
              )}
              {a.editable.includes("gate") && (
                <div className="field" style={{ margin: 0 }}>
                  <label className="field-hint">{FIELD_LABEL.gate}</label>
                  <input type="number" min={0} max={1} step={0.05}
                    value={Number(d.gate ?? 0)}
                    onChange={(e) => setField(a.id, "gate", Number(e.target.value))} />
                </div>
              )}
              {a.editable.includes("gateFloor") && (
                <div className="field" style={{ margin: 0 }}>
                  <label className="field-hint">{FIELD_LABEL.gateFloor}</label>
                  <input type="number" min={-100} max={100} step={5}
                    value={Number(d.gateFloor ?? 0)}
                    onChange={(e) => setField(a.id, "gateFloor", Number(e.target.value))} />
                </div>
              )}
              {a.editable.includes("gateBaseRate") && (
                <div className="field" style={{ margin: 0 }}>
                  <label className="field-hint">{FIELD_LABEL.gateBaseRate}（0~1）</label>
                  <input type="number" min={0} max={1} step={0.05}
                    value={Number(d.gateBaseRate ?? 0)}
                    onChange={(e) => setField(a.id, "gateBaseRate", Number(e.target.value))} />
                </div>
              )}
            </div>
            {a.editable.includes("tiers") && (
              <div className="field" style={{ marginTop: 8 }}>
                <label className="field-hint">{FIELD_LABEL.tiers}（逗号分隔，升序，如 -50,0,50）</label>
                <input
                  value={(Array.isArray(d.tiers) ? d.tiers : []).join(",")}
                  onChange={(e) => setField(
                    a.id, "tiers",
                    e.target.value.split(",").map((s) => Number(s.trim())).filter((n) => Number.isFinite(n)),
                  )}
                  placeholder="-50,0,50"
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
