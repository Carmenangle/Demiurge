import { useEffect, useRef, useState } from "react";
import { BookOpen, Pin, Upload, Download, Trash2, Plus, Pencil, Check, X } from "lucide-react";
import { PageShell } from "../components/layout/PageShell";
import { ConfirmModal } from "../components/Modal";
import { downloadJson } from "../lib/download";
import { listCharacters, characterDetail, type CardSummary } from "../api/characters";
import {
  listWorldbooks, importWorldbook, worldbookDetail, deleteWorldbook,
  listWorldbookEntries, addWorldbookEntry, updateWorldbookEntry, deleteWorldbookEntry,
  type WorldbookSummary, type WorldbookConflict, type WBEntryItem, type WBEntryFields, type WBLocation,
} from "../api/worldbook";

type Selection = { kind: "card" | "standalone"; name: string } | null;

const EMPTY_ENTRY: WBEntryFields = { content: "", comment: "", keys: [], constant: false, enabled: true };

export function WorldBook({ characterDir, worldbookDir }: { characterDir: string; worldbookDir: string }) {
  const [cards, setCards] = useState<CardSummary[]>([]);
  const [books, setBooks] = useState<WorldbookSummary[]>([]);
  const [selected, setSelected] = useState<Selection>(null);
  const [entries, setEntries] = useState<WBEntryItem[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState<{ file: File; conflict: WorldbookConflict } | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);  // 拖拽文件到页面：显示高亮反馈
  const fileRef = useRef<HTMLInputElement>(null);
  const entryRefs = useRef<Record<number, HTMLDivElement | null>>({}); // 条目 index → DOM，供右栏导航滚动定位
  const scrollToEntry = (index: number) => {
    entryRefs.current[index]?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  // 条目编辑态：editIdx=null 不编辑；="new" 新增；=index 改某条
  const [editIdx, setEditIdx] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState<WBEntryFields>(EMPTY_ENTRY);

  // 选中项 → 后端条目定位（独立书 base+name；卡内嵌 characterDir+cardName）
  const locOf = (sel: Selection): WBLocation | null => {
    if (!sel) return null;
    return sel.kind === "standalone"
      ? { base: worldbookDir, name: sel.name }
      : { character_dir: characterDir, card_name: sel.name };
  };
  const loc = locOf(selected);

  const reloadEntries = (sel: Selection) => {
    const l = locOf(sel);
    if (!l) { setEntries([]); return; }
    setEditIdx(null);
    listWorldbookEntries(l)
      .then((r) => setEntries(r.entries))
      .catch((e) => setErr(String((e as Error).message || e)));
  };

  const reloadBooks = () => {
    if (!worldbookDir) { setBooks([]); return; }
    listWorldbooks(worldbookDir).then((r) => setBooks(r.items)).catch((e) => setErr(String(e.message || e)));
  };

  useEffect(() => {
    if (characterDir) {
      listCharacters(characterDir)
        .then((r) => setCards(r.items.filter((c) => c.has_worldbook)))
        .catch((e) => setErr(String(e.message || e)));
    } else setCards([]);
  }, [characterDir]);

  useEffect(reloadBooks, [worldbookDir]);

  const openCard = (name: string) => {
    const sel: Selection = { kind: "card", name };
    setSelected(sel); reloadEntries(sel);
  };

  const openBook = (name: string) => {
    const sel: Selection = { kind: "standalone", name };
    setSelected(sel); reloadEntries(sel);
  };

  // 条目增删改
  const startAdd = () => { setDraft(EMPTY_ENTRY); setEditIdx("new"); };
  const startEdit = (e: WBEntryItem) => {
    setDraft({ content: e.content, comment: e.comment, keys: e.keys, constant: e.constant, enabled: e.enabled });
    setEditIdx(e.index);
  };
  const saveEntry = async () => {
    if (!loc) return;
    if (!draft.content.trim()) { setErr("条目内容不能为空"); return; }
    setBusy(true); setErr(null);
    try {
      if (editIdx === "new") await addWorldbookEntry(loc, draft);
      else if (typeof editIdx === "number") await updateWorldbookEntry(loc, editIdx, draft);
      reloadEntries(selected);
      reloadBooks();   // 条数变化刷新左栏
    } catch (e) { setErr(String((e as Error).message || e)); }
    finally { setBusy(false); }
  };
  const removeEntry = async (index: number) => {
    if (!loc || !window.confirm("删除该条目？")) return;
    setBusy(true); setErr(null);
    try { await deleteWorldbookEntry(loc, index); reloadEntries(selected); reloadBooks(); }
    catch (e) { setErr(String((e as Error).message || e)); }
    finally { setBusy(false); }
  };

  const doImport = async (file: File, overwrite: boolean) => {
    setErr(null); setBusy(true);
    try {
      await importWorldbook(file, worldbookDir, overwrite);
      setPending(null);
      reloadBooks();
    } catch (e) {
      const conflict = (e as { conflict?: WorldbookConflict }).conflict;
      if (conflict) setPending({ file, conflict });
      else setErr(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) doImport(file, false);
    e.target.value = "";
  };

  // 拖拽文件到页面（替代点「导入世界书」选文件）。仅接 .json，其它类型静默忽略。
  // 多文件 → 逐个串行导入；首个同名冲突会停在 pending 弹窗，由用户确认/取消后再继续。
  const ACCEPT_RE = /\.json$/i;
  const onDropFiles = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer?.files || []).filter((f) => ACCEPT_RE.test(f.name));
    if (files.length === 0) return;
    void (async () => {
      for (const f of files) {
        try { await doImport(f, false); }
        catch { /* doImport 已通过 pending 弹窗暴露冲突；其它失败已在 setErr 兜底 */ }
      }
    })();
  };
  const onDragOver = (e: React.DragEvent) => {
    if (e.dataTransfer?.types?.includes("Files")) {
      e.preventDefault();
      if (!dragOver) setDragOver(true);
    }
  };
  const onDragLeave = (e: React.DragEvent) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragOver(false);
  };

  // 导出当前选中世界书：独立书导出整本，卡内嵌导出 character_book 部分（都是可再导入的 ST 格式）
  const exportSelected = async () => {
    if (!selected) return;
    try {
      if (selected.kind === "standalone") {
        const r = await worldbookDetail(worldbookDir, selected.name);
        downloadJson(r.book, selected.name);
      } else {
        const card = await characterDetail(characterDir, selected.name);
        const book = (card?.character_book as unknown) ?? { entries: [] };
        downloadJson(book, `${selected.name}-worldbook`);
      }
    } catch (e) {
      setErr(String((e as Error).message || e));
    }
  };

  const removeBook = (name: string) => {
    deleteWorldbook(worldbookDir, name).then(() => {
      if (selected?.kind === "standalone" && selected.name === name) { setSelected(null); setEntries([]); }
      reloadBooks();
    }).catch((e) => setErr(String(e.message || e)));
  };

  const hasList = cards.length > 0 || books.length > 0;

  return (
    <PageShell
      title="世界书"
      onDrop={onDropFiles}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      actions={
        <>
          <input ref={fileRef} type="file" accept=".json" style={{ display: "none" }} onChange={onPick} />
          <button className="btn" disabled={!selected} onClick={exportSelected} title="导出选中世界书为 JSON">
            <Download size={14} style={{ marginRight: 6 }} /> 导出
          </button>
          <button className="btn primary" disabled={!worldbookDir} onClick={() => fileRef.current?.click()}>
            <Upload size={14} style={{ marginRight: 6 }} /> 导入世界书
          </button>
        </>
      }
    >
      {/* 拖拽文件到页面时的高亮遮罩（仅装饰，不拦截事件） */}
      {dragOver && (
        <div
          aria-hidden
          style={{
            position: "fixed", inset: 0, zIndex: 50, pointerEvents: "none",
            background: "rgba(60, 120, 240, 0.10)",
            border: "2px dashed rgba(60, 120, 240, 0.55)",
            borderRadius: 12,
            display: "flex", alignItems: "center", justifyContent: "center",
            color: "rgba(60, 120, 240, 0.95)", fontSize: 18, fontWeight: 600,
          }}
        >
          放开导入世界书（.json）
        </div>
      )}
      {!characterDir && !worldbookDir && (
        <p style={{ color: "var(--text-muted)" }}>请先到「设置 → 路径」设置角色卡文件夹 / 世界书文件夹。</p>
      )}
      {!worldbookDir && characterDir && (
        <p style={{ color: "var(--text-muted)" }}>独立世界书导入需先设置「世界书文件夹」。</p>
      )}
      {err && <p style={{ color: "var(--danger, #c0392b)" }}>{err}</p>}
      {(characterDir || worldbookDir) && !hasList && !err && (
        <p style={{ color: "var(--text-muted)" }}>还没有世界书。导入独立世界书，或导入含 character_book 的角色卡后会在这里显示；也可直接把世界书 .json 拖到本页导入。</p>
      )}
      {hasList && (
        <div className="worldbook-layout">
          <div className="worldbook-library">
            {books.length > 0 && (
              <div style={{ fontSize: 11, color: "var(--text-muted)", margin: "0 0 4px 4px" }}>独立世界书</div>
            )}
            {books.map((b) => (
              <div key={b.file} style={{ display: "flex", alignItems: "center", marginBottom: 4 }}>
                <button
                  className={`nav-item ${selected?.kind === "standalone" && selected.name === b.name ? "active" : ""}`}
                  style={{ flex: 1, minWidth: 0, textAlign: "left" }}
                  onClick={() => openBook(b.name)}
                >
                  <BookOpen size={14} style={{ marginRight: 6 }} /> {b.name}
                  <span style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: 6 }}>{b.entries}</span>
                </button>
                <button className="btn" title="删除" style={{ padding: 4 }} onClick={() => removeBook(b.name)}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
            {cards.length > 0 && (
              <div style={{ fontSize: 11, color: "var(--text-muted)", margin: "10px 0 4px 4px" }}>卡内嵌世界书</div>
            )}
            {cards.map((c) => (
              <button
                key={c.folder}
                className={`nav-item ${selected?.kind === "card" && selected.name === c.name ? "active" : ""}`}
                style={{ width: "100%", marginBottom: 4, textAlign: "left" }}
                onClick={() => openCard(c.name)}
              >
                <BookOpen size={14} style={{ marginRight: 6 }} /> {c.name}
              </button>
            ))}
          </div>
          <div className="worldbook-content">
            {!selected && <p style={{ color: "var(--text-muted)" }}>选择左侧世界书查看条目。</p>}
            {selected && (
              <div style={{ marginBottom: 10 }}>
                <button className="btn" disabled={busy} onClick={startAdd}>
                  <Plus size={14} style={{ marginRight: 6 }} /> 新增条目
                </button>
              </div>
            )}
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
            {selected && entries.length === 0 && editIdx === null && (
              <p style={{ color: "var(--text-muted)" }}>该世界书没有条目。点「新增条目」添加。</p>
            )}
            {entries.map((e) => (
              <div key={e.index} ref={(el) => { entryRefs.current[e.index] = el; }}
                className="chat-box" style={{ marginBottom: 10, opacity: e.enabled ? 1 : 0.55 }}>
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
          <div className={`worldbook-index ${selected && entries.length > 0 ? "" : "is-empty"}`} aria-hidden={!selected || entries.length === 0}>
          {selected && entries.length > 0 && (
            <>
              <div style={{ fontSize: 11, color: "var(--text-muted)", margin: "0 0 4px 4px" }}>条目导航（{entries.length}）</div>
              {entries.map((e) => (
                <button
                  key={e.index}
                  className="nav-item"
                  style={{ width: "100%", marginBottom: 3, textAlign: "left", opacity: e.enabled ? 1 : 0.55, fontSize: 12 }}
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
      )}
      {pending && (
        <ConfirmModal
          title="世界书已存在"
          message={`已存在同名世界书「${pending.conflict.name}」，覆盖将替换原内容。是否覆盖？`}
          confirmText="覆盖"
          danger
          busy={busy}
          onConfirm={() => doImport(pending.file, true)}
          onCancel={() => setPending(null)}
        />
      )}
    </PageShell>
  );
}
