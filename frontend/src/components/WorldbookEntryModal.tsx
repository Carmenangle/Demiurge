import { useEffect } from "react";
import { Check, X } from "lucide-react";
import type { WBEntryFields } from "../api/worldbook";

export type WorldbookEditTarget = number | "new";

/** 世界书条目编辑弹窗：世界书页与画布世界书弹窗共用同一套字段与视觉。
 *
 *  draft 由调用方持有（保存/取消的真源仍在页面编排层），本组件只负责展示、
 *  字段回写与键盘关闭。遮罩 z-index 高于窄屏条目面板与画布世界书弹窗。
 */
export function WorldbookEntryModal({
  editIdx, draft, busy, error, onChange, onSave, onCancel,
}: {
  editIdx: WorldbookEditTarget;
  draft: WBEntryFields;
  busy: boolean;
  error: string | null;
  onChange: (patch: Partial<WBEntryFields>) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);
  return (
    <div
      className="modal-mask worldbook-entry-mask"
      onClick={(e) => { if (e.target === e.currentTarget && !busy) onCancel(); }}
    >
      <div
        className="modal worldbook-entry-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="worldbook-entry-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="worldbook-entry-head">
          <div>
            <h3 id="worldbook-entry-title">
              {editIdx === "new" ? "新增条目" : `编辑条目 #${editIdx + 1}`}
            </h3>
            <p className="worldbook-entry-subtitle">修改只作用于当前这本世界书。</p>
          </div>
          <button className="btn worldbook-entry-close" type="button" aria-label="关闭" disabled={busy} onClick={onCancel}>
            <X size={16} />
          </button>
        </div>
        <div className="worldbook-entry-form">
          <label className="worldbook-entry-field">
            <span>标题 / 备注</span>
            <input
              autoFocus={editIdx === "new"}
              placeholder="例如：角色卡·腓特烈大帝"
              value={draft.comment}
              onChange={(e) => onChange({ comment: e.target.value })}
            />
          </label>
          <label className="worldbook-entry-field">
            <span>条目内容</span>
            <textarea
              autoFocus={editIdx !== "new"}
              className="worldbook-entry-content"
              rows={12}
              placeholder="写进模型上下文的世界书正文。可用【段落标题】组织人物设定、外貌、性格、好感分阶等。"
              value={draft.content}
              onChange={(e) => onChange({ content: e.target.value })}
            />
          </label>
          <label className="worldbook-entry-field">
            <span>关键词</span>
            <input
              placeholder="逗号分隔，例如：腓特烈、铁血、旗舰"
              value={draft.keys.join("，")}
              onChange={(e) => onChange({
                keys: e.target.value.split(/[,，\n]/).map((s) => s.trim()).filter(Boolean),
              })}
            />
            <small>非常驻条目靠这些词与语义检索命中；留空则只能靠语义检索。</small>
          </label>
          <div className="worldbook-entry-switches">
            <label>
              <input type="checkbox" checked={draft.constant}
                onChange={(e) => onChange({ constant: e.target.checked })} />
              常驻（每轮注入，不走检索）
            </label>
            <label>
              <input type="checkbox" checked={draft.enabled}
                onChange={(e) => onChange({ enabled: e.target.checked })} />
              启用
            </label>
          </div>
          {error && <p className="worldbook-entry-error">{error}</p>}
        </div>
        <div className="modal-actions">
          <button className="btn" type="button" disabled={busy} onClick={onCancel}>取消</button>
          <button className="btn primary" type="button" disabled={busy} onClick={onSave}>
            {busy
              ? "保存中…"
              : <><Check size={14} style={{ marginRight: 4 }} /> 保存</>}
          </button>
        </div>
      </div>
    </div>
  );
}
