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
        <label className="field" style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={draft.galleryRemoveFile === true}
            onChange={(e) => set({ galleryRemoveFile: e.target.checked })}
          />
          <span>
            <strong>资产库删除时默认删除本机文件</strong>
            <br />
            <small style={{ color: "var(--text-muted)" }}>
              开启：直接删除本机图片文件。关闭：只移除资产库记录，保留本机文件。
            </small>
          </span>
        </label>
      </div>
    </>
  );
}