import { useEffect, useState } from "react";
import { Images, Play, Save, X } from "lucide-react";
import { characterDetail, updateCharacter, avatarUrl } from "../api/characters";
import { CharacterMediaModal } from "./CharacterMediaModal";

// 角色卡预览弹窗：资产库里双击卡片打开，展示卡内容（像世界书那样"预览格式"）。
// 只展示卡本身的信息——标签、创作者注释、角色描述、开场白。内嵌世界书导入时已拆分入库，
// 不在卡预览里重复展示（其条目数在卡内也不可靠）。底部"用此卡新建作品"才进对话。
export function CardPreviewModal({ base, folder, name, onClose, onNewWork, onChanged }: {
  base: string;
  folder: string;
  name: string;
  onClose: () => void;
  onNewWork: () => void;
  onChanged?: () => void;
}) {
  const [card, setCard] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);
  const [showMedia, setShowMedia] = useState(false);
  const [description, setDescription] = useState("");
  const [firstMes, setFirstMes] = useState("");
  const [creatorNotes, setCreatorNotes] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    characterDetail(base, name).then((detail) => {
      setCard(detail);
      setDescription(String(detail.description ?? ""));
      setFirstMes(String(detail.first_mes ?? ""));
      setCreatorNotes(String(detail.creator_notes ?? ""));
    }).catch((e) => setErr(String((e as Error).message || e)));
  }, [base, name]);

  const tags = Array.isArray(card?.tags) ? (card!.tags as unknown[]).map(String).filter(Boolean) : [];

  const save = async () => {
    setBusy(true); setErr(null); setSaved("");
    try {
      const updated = await updateCharacter(base, name, {
        description, first_mes: firstMes, creator_notes: creatorNotes,
      });
      setCard(updated);
      setSaved("已保存");
      onChanged?.();
    } catch (error) { setErr(String((error as Error).message)); }
    finally { setBusy(false); }
  };

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
              <label className="character-card-field">
                <span>角色描述</span>
                <textarea value={description} onChange={(event) => setDescription(event.target.value)}
                  rows={7} placeholder="暂无角色描述" />
              </label>
            </div>
          </div>
          <label className="character-card-field">
            <span>开场白</span>
            <textarea value={firstMes} onChange={(event) => setFirstMes(event.target.value)}
              rows={6} placeholder="暂无开场白" />
          </label>
          <label className="character-card-field">
            <span>创作者注释</span>
            <textarea value={creatorNotes} onChange={(event) => setCreatorNotes(event.target.value)}
              rows={5} placeholder="暂无创作者注释" />
          </label>
        </div>
        <div className="modal-actions" style={{ marginTop: 12 }}>
          <button className="btn" onClick={onClose}>关闭</button>
          <button className="btn" onClick={() => setShowMedia(true)}>
            <Images size={14} /> 头像与表情
          </button>
          <button className="btn" disabled={!card || busy} onClick={() => { void save(); }}>
            <Save size={14} /> {busy ? "保存中…" : "保存修改"}
          </button>
          {saved && <span className="character-card-saved">{saved}</span>}
          <button className="btn primary" onClick={onNewWork}>
            <Play size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} /> 用此卡新建作品
          </button>
        </div>
      </div>
      {showMedia && (
        <CharacterMediaModal base={base} name={name} onClose={() => setShowMedia(false)} onChanged={onChanged} />
      )}
    </div>
  );
}
