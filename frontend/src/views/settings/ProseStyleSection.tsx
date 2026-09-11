import { useEffect, useState } from "react";
import { Save } from "lucide-react";
import { getProseStyle, saveProseStyle, type ProseStyleConfig } from "../../api/narrative";

// 文风（去 AI 味）分区：S0 lint 与 S1 生成侧预防共用一份词表（属主后端 prose_style）。
// enabled 关闭时：roleplay system 不注入文风约束段，Narrative CI 也不再产出文风诊断。
export function ProseStyleSection() {
  const [cfg, setCfg] = useState<ProseStyleConfig | null>(null);
  const [extraText, setExtraText] = useState("");
  const [removedText, setRemovedText] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => { void reload(); }, []);

  async function reload() {
    setLoading(true);
    try {
      const data = await getProseStyle();
      setCfg(data);
      setExtraText(data.extra.join("\n"));
      setRemovedText(data.removed.join("\n"));
    } catch { /* 后端离线：留空 */ } finally { setLoading(false); }
  }

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    try {
      const data = await saveProseStyle({
        enabled: cfg.enabled,
        extra: extraText.split("\n").map((w) => w.trim()).filter(Boolean),
        removed: removedText.split("\n").map((w) => w.trim()).filter(Boolean),
        review_every: cfg.review_every,
      });
      setCfg(data);
      setExtraText(data.extra.join("\n"));
      setRemovedText(data.removed.join("\n"));
      setSaved(true);
      setTimeout(() => setSaved(false), 1800);
    } catch { /* 保存失败保持按钮可重试 */ } finally { setSaving(false); }
  };

  if (loading) return <p className="field-hint">加载文风配置…</p>;
  if (!cfg) return null;

  return (
    <div className="settings-subsection" style={{ marginTop: 16 }}>
      <h4 style={{ margin: "0 0 8px" }}>剧情文风（去 AI 味）</h4>
      <label style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, padding: "2px 0", cursor: "pointer" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>启用文风检查与预防</div>
          <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
            关闭：不注入文风约束段，也不再产出文风诊断（固定搭配/密度/节拍/开场趋同）。
          </div>
        </div>
        <span className="regex-toggle" style={{ marginTop: 4, flexShrink: 0 }} title={cfg.enabled ? "已启用" : "已禁用"}>
          <input
            type="checkbox"
            checked={cfg.enabled}
            onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })}
          />
          <span className="regex-toggle-track" />
        </span>
      </label>
      <div style={{ display: "flex", gap: 12, marginTop: 8, flexWrap: "wrap" }}>
        <label className="field" style={{ flex: 1, minWidth: 220 }}>
          <span>增补 AI 味词（每行一个）</span>
          <textarea
            rows={4}
            value={extraText}
            onChange={(e) => setExtraText(e.target.value)}
            placeholder={"例如模型特有的套路句式"}
          />
        </label>
        <label className="field" style={{ flex: 1, minWidth: 220 }}>
          <span>豁免词（从内置词表移除，每行一个）</span>
          <textarea
            rows={4}
            value={removedText}
            onChange={(e) => setRemovedText(e.target.value)}
            placeholder={"误报的词移到这里"}
          />
        </label>
      </div>
      <label className="field" style={{ marginTop: 8, maxWidth: 360 }}>
        <span>活人感通审频率（每 N 轮一次，0=关闭）</span>
        <input
          type="number"
          min={0}
          value={cfg.review_every}
          onChange={(e) => setCfg({ ...cfg, review_every: Math.max(0, Number(e.target.value) || 0) })}
        />
        <small style={{ color: "var(--text-muted)" }}>
          通审由模型在后台维护通道通读整段正文，给出活人感评分与综述（走 Narrative CI 诊断流）。
        </small>
      </label>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8 }}>
        <button className="btn primary" onClick={save} disabled={saving}>
          <Save size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          {saving ? "保存中…" : "保存文风配置"}
        </button>
        {saved && <small style={{ color: "var(--text-muted)" }}>已保存，下一轮对话生效</small>}
      </div>
    </div>
  );
}
