// CanvasCharacterModal.tsx — 画布「角色卡」灵感卡双击弹窗
//
// 照搬资产库 CardPreviewModal 的展示形态（头像/标签/描述/开场白/创作者注释），但按画布要求：
//   - 去掉「用此卡新建作品」
//   - 只把「描述」编辑写回画布本地灵感卡（onSave），不调用 updateCharacter → 不与外部资产库同步
//   - 开场白/创作者注释/标签只读参考（剧情模式具备同步当前剧情修改设定条目的能力，不在画布重复做）
import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { characterDetail, avatarUrl } from "../api/characters";

export function CanvasCharacterModal({
  base, name, initialContent, onSave, onClose,
}: {
  base: string;
  name: string;
  initialContent: string;
  onSave: (content: string) => void;
  onClose: () => void;
}) {
  const [card, setCard] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [description, setDescription] = useState(initialContent);
  const [folder, setFolder] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    characterDetail(base, name).then((detail) => {
      setCard(detail);
      setFolder(String(detail.folder ?? ""));
      // 画布本地内容优先；源库描述作为首次回填（仅当本地为空）
      setDescription((cur) => cur || String(detail.description ?? ""));
    }).catch((e) => setErr(String((e as Error).message || e)));
  }, [base, name]);

  const tags = Array.isArray(card?.tags) ? (card!.tags as unknown[]).map(String).filter(Boolean) : [];
  const firstMes = String(card?.first_mes ?? "");
  const creatorNotes = String(card?.creator_notes ?? "");

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 640, width: "90vw", maxHeight: "85vh", display: "flex", flexDirection: "column" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
          <h3 style={{ margin: 0, flex: 1 }}>{name}</h3>
          <button className="icon-btn" onClick={onClose} title="关闭"><X size={16} /></button>
        </div>
        {err && <p style={{ color: "var(--danger, #c0392b)", fontSize: 13 }}>{err}</p>}
        <div style={{ overflowY: "auto", flex: 1, minHeight: 0, paddingRight: 4 }}>
          <div style={{ display: "flex", gap: 14, marginBottom: 12 }}>
            <div style={{ width: 120, flexShrink: 0 }}>
              <img src={avatarUrl(base, folder)} alt={name} loading="lazy"
                style={{ width: 120, borderRadius: 8, objectFit: "cover", background: "var(--surface,#eee)" }}
                onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              {tags.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
                  {tags.map((t, i) => (
                    <span key={i} style={{
                      fontSize: 11, padding: "2px 8px", borderRadius: 10,
                      background: "var(--surface,#eee)", color: "var(--text-muted)",
                    }}>{t}</span>
                  ))}
                </div>
              )}
              <label className="character-card-field">
                <span>角色描述</span>
                <textarea value={description} onChange={(e) => setDescription(e.target.value)}
                  rows={7} placeholder="暂无角色描述" />
              </label>
            </div>
          </div>
          <label className="character-card-field">
            <span>开场白（只读参考）</span>
            <textarea value={firstMes} readOnly rows={6} placeholder="暂无开场白" />
          </label>
          <label className="character-card-field">
            <span>创作者注释（只读参考）</span>
            <textarea value={creatorNotes} readOnly rows={5} placeholder="暂无创作者注释" />
          </label>
          <p style={{ fontSize: 11, color: "var(--text-muted)", margin: "4px 0 0", lineHeight: 1.5 }}>
            修改仅更新画布上的这张灵感卡，不同步到外部资产库；开场白/注释如需改，请在「资产管理 → 角色卡」或剧情模式中编辑。
          </p>
        </div>
        <div className="modal-actions" style={{ marginTop: 12 }}>
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn primary" onClick={() => { onSave(description); onClose(); }}>保存到画布</button>
        </div>
      </div>
    </div>
  );
}
