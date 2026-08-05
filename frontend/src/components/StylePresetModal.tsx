import { useEffect, useState } from "react";
import { X, Plus, Trash2 } from "lucide-react";
import type { StylePreset } from "../stores/settings";
import { ConfirmModal } from "./Modal";

// 提示词风格存档管理：左侧存档列表（选中/新建/删除），右侧编辑名称与整段模板内容。
// 存档内容是自由文本（画风/结构/负面词），AI 参照其组织形态来写提示词。
export function StylePresetModal({
  presets, onAdd, onUpdate, onRemove, onClose,
}: {
  presets: StylePreset[];
  onAdd: (name: string, content: string) => string;
  onUpdate: (id: string, patch: Partial<StylePreset>) => void;
  onRemove: (id: string) => void;
  onClose: () => void;
}) {
  const [activeId, setActiveId] = useState<string | null>(presets[0]?.id ?? null);
  const [confirmDel, setConfirmDel] = useState(false);  // 删除确认框开关
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (confirmDel) return;  // 确认框开时把 Esc 让给它，不关整个弹窗
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, confirmDel]);

  const active = presets.find((p) => p.id === activeId) || null;

  const create = () => {
    const id = onAdd("新风格存档", "");
    setActiveId(id);
  };

  return (
    <>
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" style={{ width: 860, maxWidth: "94vw", maxHeight: "88vh", display: "flex", flexDirection: "column" }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <h3 style={{ margin: 0 }}>提示词风格存档</h3>
          <button className="icon-btn" style={{ background: "transparent", color: "var(--text)" }} onClick={onClose} aria-label="关闭">
            <X size={18} />
          </button>
        </div>
        <p style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 0 }}>
          粘贴一段风格模板（画风、动作/构图/光影结构、负面词，格式随意）。生图时 AI 会模仿它的组织结构来写提示词，不照抄具体内容。
        </p>
        <div style={{ display: "flex", gap: 12, flex: 1, minHeight: 0 }}>
          {/* 左：存档列表 */}
          <div style={{ width: 220, flexShrink: 0, display: "flex", flexDirection: "column", gap: 6, overflowY: "auto" }}>
            {presets.map((p) => (
              <button
                key={p.id}
                type="button"
                className={`model-switch-item ${p.id === activeId ? "active" : ""}`}
                style={{ textAlign: "left", justifyContent: "flex-start" }}
                onClick={() => setActiveId(p.id)}
              >
                {p.name || "未命名"}
              </button>
            ))}
            <button type="button" className="btn" onClick={create} style={{ marginTop: 4 }}>
              <Plus size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} /> 新建存档
            </button>
          </div>
          {/* 右：编辑区 */}
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 8 }}>
            {active ? (
              <>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    value={active.name}
                    placeholder="存档名称"
                    onChange={(e) => onUpdate(active.id, { name: e.target.value })}
                    style={{ flex: 1 }}
                  />
                  <button className="btn danger" onClick={() => setConfirmDel(true)}>
                    <Trash2 size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} /> 删除
                  </button>
                </div>
                <textarea
                  value={active.content}
                  placeholder="在此粘贴风格模板内容…"
                  onChange={(e) => onUpdate(active.id, { content: e.target.value })}
                  style={{ flex: 1, minHeight: 320, resize: "vertical", font: "inherit", lineHeight: 1.6 }}
                />
              </>
            ) : (
              <div style={{ color: "var(--text-muted)", fontSize: 13, margin: "auto" }}>
                选择左侧存档编辑，或新建一个。
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
    {confirmDel && active && (
      <ConfirmModal
        title="删除风格存档"
        message={`确认删除「${active.name || "未命名"}」？存档内容将立即清除，不可恢复。`}
        confirmText="删除"
        danger
        onConfirm={() => { onRemove(active.id); setActiveId(null); setConfirmDel(false); }}
        onCancel={() => setConfirmDel(false)}
      />
    )}
    </>
  );
}
