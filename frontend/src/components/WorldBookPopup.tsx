// WorldBookPopup.tsx — 世界书弹窗：左侧条目内容（2/3）+ 右侧条目导航（1/3）
// 复用 WorldBook.tsx 的条目渲染逻辑（照搬源代码），去掉左栏书列表，
// 中间条目移到左侧占 2/3，右侧条目导航占 1/3。不损害原 WorldBook.tsx 源代码。
import { useEffect, useRef, useState } from "react";
import { Pin, Plus, Pencil, Check, X, Trash2 } from "lucide-react";
import {
  listWorldbookEntries, addWorldbookEntry, updateWorldbookEntry, deleteWorldbookEntry,
  repoWorldbookEntries, repoWorldbookEntryAdd, repoWorldbookEntryUpdate, repoWorldbookEntryDelete,
  type WBEntryItem, type WBEntryFields, type WBLocation, type RepoWorldbookLoc,
} from "../api/worldbook";

const EMPTY_ENTRY: WBEntryFields = { content: "", comment: "", keys: [], constant: false, enabled: true };

export function WorldBookPopup({
  location, repoLoc, seedFrom, title, onClose,
}: {
  location: WBLocation;
  repoLoc?: RepoWorldbookLoc;
  seedFrom?: { base: string; name: string };
  title?: string;
  onClose: () => void;
}) {
  const [entries, setEntries] = useState<WBEntryItem[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editIdx, setEditIdx] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState<WBEntryFields>(EMPTY_ENTRY);
  const entryRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const [activeIdx, setActiveIdx] = useState<number | null>(null);

  const reloadEntries = () => {
    const promise = repoLoc
      ? repoWorldbookEntries(repoLoc, seedFrom).then((r) => r.entries)
      : listWorldbookEntries(location).then((r) => r.entries);
    promise.then(setEntries).catch((e) => setErr(String((e as Error).message || e)));
  };

  useEffect(() => { reloadEntries(); },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    repoLoc ? [repoLoc.output_dir, repoLoc.repo_id] : ['base' in location ? location.base : location.character_dir,
     'name' in location ? location.name : location.card_name]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const scrollToEntry = (index: number) => {
    setActiveIdx(index);
    entryRefs.current[index]?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const startAdd = () => { setDraft(EMPTY_ENTRY); setEditIdx("new"); };
  const startEdit = (e: WBEntryItem) => {
    setDraft({ content: e.content, comment: e.comment, keys: e.keys, constant: e.constant, enabled: e.enabled });
    setEditIdx(e.index);
  };
  const saveEntry = async () => {
    if (!draft.content.trim()) { setErr("条目内容不能为空"); return; }
    setBusy(true); setErr(null);
    try {
      if (repoLoc) {
        if (editIdx === "new") await repoWorldbookEntryAdd(repoLoc, draft);
        else if (typeof editIdx === "number") await repoWorldbookEntryUpdate(repoLoc, editIdx, draft);
      } else {
        if (editIdx === "new") await addWorldbookEntry(location, draft);
        else if (typeof editIdx === "number") await updateWorldbookEntry(location, editIdx, draft);
      }
      setEditIdx(null);
      reloadEntries();
    } catch (e) { setErr(String((e as Error).message || e)); }
    finally { setBusy(false); }
  };
  const removeEntry = async (index: number) => {
    if (!window.confirm("删除该条目？")) return;
    setBusy(true); setErr(null);
    try {
      if (repoLoc) await repoWorldbookEntryDelete(repoLoc, index);
      else await deleteWorldbookEntry(location, index);
      reloadEntries();
    }
    catch (e) { setErr(String((e as Error).message || e)); }
    finally { setBusy(false); }
  };

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" style={{ width: "90vw", maxWidth: 1200, maxHeight: "85vh", display: "flex", flexDirection: "column" }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>{title || "世界书条目"}</h3>
          <button className="icon-btn" style={{ background: "transparent", color: "var(--text)" }} onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        {err && <p style={{ color: "var(--danger, #c0392b)", fontSize: 13, margin: "4px 0" }}>{err}</p>}
        <div style={{ marginBottom: 10 }}>
          <button className="btn" disabled={busy} onClick={startAdd}>
            <Plus size={14} style={{ marginRight: 6 }} /> 新增条目
          </button>
        </div>
        {/* 左侧条目内容（2/3）+ 右侧条目导航（1/3）—— 照搬 WorldBook.tsx 中栏+右栏逻辑 */}
        <div className="worldbook-popup-layout" style={{ flex: 1, minHeight: 0 }}>
          <div className="worldbook-popup-content">
            {editIdx !== null && (
              <div className="chat-box" style={{ marginBottom: 10 }}>
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
                  {editIdx === "new" ? "新增条目" : `编辑条目 #${(editIdx as number) + 1}`}
                </div>
                <input placeholder="标题/备注（comment）" value={draft.comment}
                  onChange={(e) => setDraft((d) => ({ ...d, comment: e.target.value }))}
                  style={{ width: "100%", marginBottom: 6 }} />
                <textarea placeholder="条目内容（content）" value={draft.content} rows={4}
                  onChange={(e) => setDraft((d) => ({ ...d, content: e.target.value }))}
                  style={{ width: "100%", marginBottom: 6 }} />
                <input placeholder="关键词（逗号分隔，非常驻条目按其语义检索）" value={draft.keys.join(",")}
                  onChange={(e) => setDraft((d) => ({ ...d, keys: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) }))}
                  style={{ width: "100%", marginBottom: 6 }} />
                <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
                  <label style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 4 }}>
                    <input type="checkbox" checked={draft.constant}
                      onChange={(e) => setDraft((d) => ({ ...d, constant: e.target.checked }))} />
                    常驻（每轮注入，不走检索）
                  </label>
                  <label style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 4 }}>
                    <input type="checkbox" checked={draft.enabled}
                      onChange={(e) => setDraft((d) => ({ ...d, enabled: e.target.checked }))} />
                    启用
                  </label>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="btn primary" disabled={busy} onClick={saveEntry}>
                    <Check size={14} style={{ marginRight: 4 }} /> 保存
                  </button>
                  <button className="btn" disabled={busy} onClick={() => setEditIdx(null)}>
                    <X size={14} style={{ marginRight: 4 }} /> 取消
                  </button>
                </div>
              </div>
            )}
            {entries.length === 0 && editIdx === null && (
              <p style={{ color: "var(--text-muted)" }}>该世界书没有条目。点「新增条目」添加。</p>
            )}
            {entries.map((e) => (
              <div key={e.index} ref={(el) => { entryRefs.current[e.index] = el; }}
                className="chat-box" style={{
                  marginBottom: 10, opacity: e.enabled ? 1 : 0.55,
                  borderLeft: activeIdx === e.index ? "3px solid var(--accent, #3b82f6)" : undefined,
                }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  {e.constant && (
                    <span title="常驻（每轮注入）" style={{ display: "inline-flex", alignItems: "center", gap: 3, fontSize: 11, color: "var(--accent)" }}>
                      <Pin size={12} /> 常驻
                    </span>
                  )}
                  <span style={{ fontWeight: 600, fontSize: 13 }}>{e.comment || `条目 ${e.index + 1}`}</span>
                  {!e.enabled && <span style={{ fontSize: 11, color: "var(--text-muted)" }}>（已停用）</span>}
                  {e.keys.length > 0 && (
                    <span style={{ fontSize: 11, color: "var(--text-muted)" }}>关键词：{e.keys.join("、")}</span>
                  )}
                  <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
                    <button className="btn" style={{ padding: 4 }} disabled={busy} onClick={() => startEdit(e)} title="编辑">
                      <Pencil size={13} />
                    </button>
                    <button className="btn" style={{ padding: 4 }} disabled={busy} onClick={() => removeEntry(e.index)} title="删除">
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
                <div style={{ fontSize: 13, whiteSpace: "pre-wrap", color: "var(--text)" }}>{e.content}</div>
              </div>
            ))}
          </div>
          <div className="worldbook-popup-index">
            {entries.length > 0 && (
              <>
                <div style={{ fontSize: 11, color: "var(--text-muted)", margin: "0 0 4px 4px" }}>条目导航（{entries.length}）</div>
                {entries.map((e) => (
                  <button
                    key={e.index}
                    className={`wb-nav-item ${activeIdx === e.index ? "active" : ""}`}
                    title={e.comment || `条目 ${e.index + 1}`}
                    onClick={() => scrollToEntry(e.index)}
                  >
                    {e.constant && <Pin size={11} style={{ marginRight: 4, color: "var(--accent)", flexShrink: 0 }} />}
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {e.comment || `条目 ${e.index + 1}`}
                    </span>
                  </button>
                ))}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
