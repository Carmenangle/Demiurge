// 合集卡角色名册管理（2026-11-10）：自动识别可能漏/多，这里让用户勾选、补选、改名、
// 排序；保存为仓库 characterRoster 覆盖（键为世界书条目稳定 uid，不是易变 index）。
import { useMemo, useState } from "react";
import type { CharacterRosterResult } from "../../api/ai/agent";
import type { CharacterRosterOverrides } from "../../lib/characterRoster";

interface Props {
  roster: CharacterRosterResult | null;
  overrides: CharacterRosterOverrides;
  onSave: (overrides: CharacterRosterOverrides) => void;
  onClose: () => void;
}

const CONFIDENCE_LABEL: Record<string, string> = {
  high: "高置信",
  medium: "中置信",
  low: "低置信",
  manual: "手动加入",
};

export function CharacterRosterModal({ roster, overrides, onSave, onClose }: Props) {
  const candidates = useMemo(() => roster?.candidates || [], [roster]);
  const [mode, setMode] = useState<"auto" | "cards" | "worldbook">(
    overrides.mode || "auto",
  );
  const [selected, setSelected] = useState<Record<string, boolean>>(() => {
    const out: Record<string, boolean> = {};
    for (const item of candidates) out[item.uid] = item.selected;
    return out;
  });
  const [names, setNames] = useState<Record<string, string>>(() => ({ ...(overrides.rename || {}) }));

  const selectedCount = candidates.filter((item) => selected[item.uid]).length;

  const resetAuto = () => {
    const next: Record<string, boolean> = {};
    for (const item of candidates) {
      next[item.uid] = ["high", "medium", "low"].includes(item.confidence);
    }
    setSelected(next);
    setNames({});
    setMode("auto");
  };

  const save = () => {
    const picked = candidates.filter((item) => selected[item.uid]);
    // 「未识别」和「手动加入」都靠 include 留在名册；自动识别的靠 exclude 排除。
    const include = picked
      .filter((item) => !item.confidence || item.confidence === "manual")
      .map((item) => item.uid);
    const exclude = candidates
      .filter((item) => !selected[item.uid])
      .map((item) => item.uid);
    const order = picked.map((item) => item.uid);
    const rename: Record<string, string> = {};
    for (const item of picked) {
      const value = (names[item.uid] || "").trim();
      const original = item.name || item.comment;
      if (value && value !== original) rename[item.uid] = value;
    }
    onSave({ mode, include, exclude, order, rename });
    onClose();
  };

  return (
    <div className="modal-mask" onClick={onClose}>
      <div
        className="modal"
        onClick={(event) => event.stopPropagation()}
        style={{ width: 620, maxWidth: "94vw", maxHeight: "86vh", display: "flex", flexDirection: "column" }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <strong>管理聊天角色</strong>
          <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
            当前来源：{roster?.source === "worldbook" ? "世界书名册" : "绑定角色卡"}
            {selectedCount > 0 ? ` · 已选 ${selectedCount} 人` : ""}
          </span>
          <button className="btn" style={{ marginLeft: "auto" }} onClick={onClose}>关闭</button>
        </div>

        <p style={{ color: "var(--text-muted)", fontSize: 12, margin: "10px 0" }}>
          自动识别按「标题/keys 像人名 + 身份/外貌/性格等人物档案信号」判定，不要求条目写「角色条目」前缀。
          误识别就取消勾选；漏掉的角色在下面补选。条目正文请到世界书页面编辑。
        </p>

        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <span style={{ fontSize: 13 }}>成员来源</span>
          <select className="input" value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}>
            <option value="auto">自动识别（推荐）</option>
            <option value="cards">只用绑定角色卡</option>
            <option value="worldbook">只用世界书名册</option>
          </select>
          <button className="btn" onClick={resetAuto} title="清空手动增删/改名，回到自动识别">恢复自动识别</button>
        </div>

        <div style={{ overflowY: "auto", flex: 1, border: "1px solid var(--border)", borderRadius: 8 }}>
          {candidates.length === 0 && (
            <div style={{ padding: 16, color: "var(--text-muted)", fontSize: 13 }}>
              当前作品世界书没有可用条目。请先在「世界书」里写入角色条目，或改用绑定角色卡。
            </div>
          )}
          {candidates.map((item) => {
            const checked = Boolean(selected[item.uid]);
            const label = item.name || item.comment || `条目 #${item.index + 1}`;
            return (
              <div
                key={item.uid}
                style={{
                  display: "flex", alignItems: "center", gap: 8, padding: "7px 10px",
                  borderBottom: "1px solid var(--border)", opacity: checked ? 1 : 0.55,
                }}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={(event) => setSelected((prev) => ({ ...prev, [item.uid]: event.target.checked }))}
                  aria-label={`加入 ${label}`}
                />
                <span style={{ minWidth: 96, fontSize: 13 }}>{label}</span>
                {item.comment && item.comment !== label && (
                  <span style={{ color: "var(--text-muted)", fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {item.comment}
                  </span>
                )}
                <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)" }}>
                  {item.confidence ? (CONFIDENCE_LABEL[item.confidence] || item.confidence) : "未识别"}
                </span>
                {checked && (
                  <input
                    className="input"
                    style={{ width: 130, fontSize: 12 }}
                    value={names[item.uid] ?? item.name ?? ""}
                    placeholder="改名（可选）"
                    onChange={(event) => setNames((prev) => ({ ...prev, [item.uid]: event.target.value }))}
                    aria-label={`${label} 的展示名`}
                  />
                )}
              </div>
            );
          })}
        </div>

        <div className="modal-actions" style={{ marginTop: 12 }}>
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn primary" onClick={save}>保存名册</button>
        </div>
      </div>
    </div>
  );
}
