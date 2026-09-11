import type { Settings } from "../../stores/settings";
import type { PanelProps } from "./GeneralPanel";

export function MiscPanel({ draft, setDraft }: PanelProps) {
  const set = (patch: Partial<Settings>) => setDraft((d) => ({ ...d, ...patch }));

  return (
    <>
      <div className="settings-section">
        <h4>资产库</h4>
        <p className="field-hint" style={{ margin: "0 0 12px" }}>
          控制资产库删除图片记录时的行为。开启后，删除资产库条目时一并删除本机图片文件，不再弹窗询问。
        </p>
        <label style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, padding: "2px 0", cursor: "pointer" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>资产库删除时默认删除本机文件</div>
            <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
              开启：直接删除本机图片文件。关闭：只移除资产库记录，保留本机文件。
            </div>
          </div>
          <span className="regex-toggle" style={{ marginTop: 4, flexShrink: 0 }} title={draft.galleryRemoveFile ? "已启用" : "已禁用"}>
            <input
              type="checkbox"
              checked={draft.galleryRemoveFile === true}
              onChange={(e) => set({ galleryRemoveFile: e.target.checked })}
            />
            <span className="regex-toggle-track" />
          </span>
        </label>
      </div>
    </>
  );
}