import { useEffect, useState } from "react";
import { Play, X } from "lucide-react";
import { characterDetail, avatarUrl } from "../api/characters";

// 角色卡预览弹窗：资产库里双击卡片打开，展示卡内容（像世界书那样"预览格式"）。
// 只展示卡本身的信息——标签、创作者注释、角色描述、开场白。内嵌世界书导入时已拆分入库，
// 不在卡预览里重复展示（其条目数在卡内也不可靠）。底部"用此卡新建作品"才进对话。
export function CardPreviewModal({ base, folder, name, onClose, onNewWork }: {
  base: string;
  folder: string;
  name: string;
  onClose: () => void;
  onNewWork: () => void;
}) {
  const [card, setCard] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    characterDetail(base, name).then(setCard).catch((e) => setErr(String((e as Error).message || e)));
  }, [base, name]);

  const str = (k: string) => String((card?.[k] as string) ?? "").trim();
  const tags = Array.isArray(card?.tags) ? (card!.tags as unknown[]).map(String).filter(Boolean) : [];

  const Field = ({ label, value }: { label: string; value: string }) =>
    value ? (
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 3 }}>{label}</div>
        <div style={{ fontSize: 13, whiteSpace: "pre-wrap", color: "var(--text)" }}>{value}</div>
      </div>
    ) : null;

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 640, width: "90vw", maxHeight: "85vh", display: "flex", flexDirection: "column" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
          <h3 style={{ margin: 0, flex: 1 }}>{name}</h3>
          <button className="icon-btn" onClick={onClose} title="关闭"><X size={16} /></button>
        </div>
        {err && <p style={{ color: "var(--danger, #c0392b)" }}>{err}</p>}
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
              <Field label="描述" value={str("description")} />
            </div>
          </div>
          <Field label="创作者注释" value={str("creator_notes")} />
          <Field label="开场白" value={str("first_mes")} />
        </div>
        <div className="modal-actions" style={{ marginTop: 12 }}>
          <button className="btn" onClick={onClose}>关闭</button>
          <button className="btn primary" onClick={onNewWork}>
            <Play size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} /> 用此卡新建作品
          </button>
        </div>
      </div>
    </div>
  );
}
