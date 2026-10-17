import { useEffect, useState } from "react";
import { RotateCcw, Save, X } from "lucide-react";
import {
  getIllustrationPromptSettings, saveIllustrationPromptSettings,
  type IllustrationPromptSettings,
} from "../api/illustrationPrompts";

// 多元数据插入 → 提示词设置（右上角齿轮进入，2026-09-18）。
//
// 只列**当前开放**的提示词项：本轮只开放「高潮点定位判据」，先测可行性；
// 视频提示词 / 生图四模式模板等**刻意不在此出现**，等判据档验证通过再逐项开放。
//
// 界面上刻意分成两块，对应两种性质：
//   可编辑 —— 判据（想让模型怎么想）；
//   只读   —— 输出字段名与 anchor 逐字落文校验（程序怎么读，后端给文本，改了会炸解析）。

export function IllustrationPromptModal({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<IllustrationPromptSettings | null>(null);
  const [rubric, setRubric] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let active = true;
    getIllustrationPromptSettings()
      .then((data) => {
        if (!active) return;
        setSettings(data);
        setRubric(data.climax_anchor.rubric);
      })
      .catch((e) => { if (active) setErr(String((e as Error)?.message || e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const save = async () => {
    setSaving(true);
    setErr("");
    try {
      const data = await saveIllustrationPromptSettings({ rubric });
      setSettings(data);
      // 后端会把空判据替换回缺省，所以保存后一律以响应为准回填，避免界面与落盘不一致。
      setRubric(data.climax_anchor.rubric);
      setSaved(true);
      setTimeout(() => setSaved(false), 1800);
    } catch (e) {
      setErr(String((e as Error)?.message || e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-mask">
      <div
        className="modal"
        style={{ width: 720, maxWidth: "94vw", maxHeight: "88vh", overflowY: "auto" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
          <h3 style={{ margin: 0 }}>提示词设置</h3>
          <button className="btn" onClick={onClose} aria-label="关闭"><X size={16} /></button>
        </div>
        <p style={{ color: "var(--text-muted)", marginTop: 0, fontSize: 12, lineHeight: 1.6 }}>
          控制给 LLM 看的提示词。这里只开放「想让模型怎么想」的部分；输出字段名与 anchor 校验
          属程序解析合同，只读展示、不可编辑。
        </p>
        {err && <p style={{ color: "var(--danger, #c0392b)", fontSize: 12 }}>{err}</p>}
        {loading && <p style={{ color: "var(--text-muted)" }}>加载中…</p>}

        {settings && (
          <div style={{ borderTop: "1px solid var(--border)", marginTop: 12, paddingTop: 12 }}>
            <p style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.6, margin: "0 0 14px" }}>
              <strong style={{ fontSize: 13, color: "var(--text)", display: "block", marginBottom: 3 }}>
                高潮点定位判据（常开）
              </strong>
              没有开关：缺插画计划的回合会按下面的判据额外调用一次对话模型；判定失败、输出不合法、
              或该句在正文里找不到原话时，自动回退中文词表，不会中断出图。所以这里要调的是判据本身。
            </p>

            <label style={{ display: "block", marginTop: 14 }}>
              <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>判据（可编辑）</span>
              <textarea
                value={rubric}
                onChange={(e) => setRubric(e.target.value)}
                rows={16}
                spellCheck={false}
                style={{
                  width: "100%", minHeight: 260, resize: "vertical",
                  fontSize: 12, lineHeight: 1.6, boxSizing: "border-box",
                }}
              />
            </label>
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button
                type="button"
                className="btn"
                disabled={saving}
                onClick={() => setRubric(settings.default_rubric)}
                title="把判据回填为后端缺省版本（还需点保存才落盘）"
              >
                <RotateCcw size={14} style={{ marginRight: 4 }} /> 恢复默认判据
              </button>
              <button type="button" className="btn primary" disabled={saving} onClick={() => void save()}>
                <Save size={14} style={{ marginRight: 4 }} />
                {saving ? "保存中…" : saved ? "已保存" : "保存"}
              </button>
            </div>

            <div style={{ marginTop: 18 }}>
              <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>
                输出格式与校验（只读 · 程序解析合同）
              </span>
              <pre
                style={{
                  whiteSpace: "pre-wrap", wordBreak: "break-word",
                  fontSize: 11, lineHeight: 1.7, margin: 0, padding: "8px 10px",
                  background: "var(--bg-soft, rgba(127,127,127,0.08))",
                  border: "1px solid var(--border)", borderRadius: 6,
                  color: "var(--text-muted)",
                }}
              >{settings.output_contract}</pre>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
