import { useEffect, useState } from "react";
import { Link2 } from "lucide-react";
import { listCharacters, type CardSummary } from "../api/characters";
import { listWorldbooks, type WorldbookSummary } from "../api/worldbook";
import { type Repo, type RepoBinding } from "../stores/repos";
import { type UserPersona } from "../stores/settings";

// 仓库绑定弹窗：角色卡 / 独立世界书 / 用户设定三样独立可选，各带「不绑定」。
// 大小仓库都能绑；子仓库留空则运行时继承父仓库（resolveBinding 处理，这里只编辑自身字段）。
export function BindRepoModal({
  repo, characterDir, worldbookDir, personas, onSave, onClose,
}: {
  repo: Repo;
  characterDir: string;
  worldbookDir: string;
  personas: UserPersona[];
  onSave: (patch: Partial<RepoBinding>) => void;
  onClose: () => void;
}) {
  const [cards, setCards] = useState<CardSummary[]>([]);
  const [books, setBooks] = useState<WorldbookSummary[]>([]);
  const [cardName, setCardName] = useState(repo.cardName || "");
  const [worldbookName, setWorldbookName] = useState(repo.worldbookName || "");
  const [personaId, setPersonaId] = useState(repo.personaId || "");
  const [err, setErr] = useState("");

  useEffect(() => {
    if (characterDir) {
      listCharacters(characterDir).then((r) => setCards(r.items)).catch((e) => setErr(String((e as Error).message)));
    }
    if (worldbookDir) {
      listWorldbooks(worldbookDir).then((r) => setBooks(r.items)).catch(() => { /* 无独立世界书目录则空 */ });
    }
  }, [characterDir, worldbookDir]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const save = () => {
    onSave({ cardName, worldbookName, personaId });
    onClose();
  };

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: 460, maxWidth: "92vw" }}>
        <h3 style={{ margin: "0 0 4px", display: "flex", alignItems: "center", gap: 6 }}>
          <Link2 size={17} />为「{repo.name}」绑定资料
        </h3>
        <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "0 0 12px" }}>
          三样各自独立，留空即不绑（小仓库留空则继承所属大仓库的绑定）。绑定角色卡后，本仓库会话第一句自动用该卡开场白。
        </p>
        {err && <p style={{ color: "var(--danger, #c0392b)", fontSize: 13 }}>{err}</p>}

        <label className="bind-field">
          <span className="bind-label">角色卡</span>
          <select value={cardName} onChange={(e) => setCardName(e.target.value)}>
            <option value="">不绑定</option>
            {cards.map((c) => <option key={c.name} value={c.name}>{c.name}{c.has_worldbook ? "（含内嵌世界书）" : ""}</option>)}
          </select>
        </label>

        <label className="bind-field">
          <span className="bind-label">独立世界书</span>
          <select value={worldbookName} onChange={(e) => setWorldbookName(e.target.value)} disabled={!worldbookDir}>
            <option value="">不绑定（绑了卡则自动用同名世界书）</option>
            {books.map((b) => <option key={b.name} value={b.name}>{b.name}</option>)}
          </select>
          {!worldbookDir && <span className="bind-hint">未设置世界书文件夹（设置→路径），无法绑独立世界书。</span>}
          {worldbookDir && (
            <span className="bind-hint">卡内嵌世界书导入时已外拆为同名独立世界书；绑卡后不选这里也会自动加载它。选此处可另挂别的书。</span>
          )}
        </label>

        <label className="bind-field">
          <span className="bind-label">用户设定（我是谁）</span>
          <select value={personaId} onChange={(e) => setPersonaId(e.target.value)}>
            <option value="">用全局选中档</option>
            {personas.map((p) => <option key={p.id} value={p.id}>{p.name || "（未命名人设）"}</option>)}
          </select>
        </label>

        <div className="modal-actions">
          <button className="btn primary" onClick={save}>保存绑定</button>
          <button className="btn" onClick={onClose}>取消</button>
        </div>
      </div>
    </div>
  );
}
