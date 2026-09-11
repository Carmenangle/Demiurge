import { useEffect, useRef, useState } from "react";
import { X, Trash2, Pencil, Lock, Check, Search, Download, Upload } from "lucide-react";
import { listDocs, deleteDoc, updateDoc, importDocuments, type RagDoc } from "../api/ai";
import { ConfirmModal } from "./Modal";

type Embed = { baseUrl: string; apiKey: string; modelName: string };

interface Props {
  repoName: string;
  repoId: string;
  busy: boolean;
  embed: Embed;
  onSubmit: (title: string, text: string) => void;  // 录入新参考资料
  onClose: () => void;
}

// 知识库管理弹窗：列出已入库条目(系统指令带锁不可删改) + 删除/编辑 + 录入新资料 + 批量导入导出(⑤)
export function KnowledgeModal({ repoName, repoId, busy, embed, onSubmit, onClose }: Props) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [docs, setDocs] = useState<RagDoc[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [query, setQuery] = useState("");   // 列表过滤关键字
  const [deleting, setDeleting] = useState<RagDoc | null>(null);  // 待确认删除的条目
  const [io, setIo] = useState(false);       // 导入导出进行中
  const fileRef = useRef<HTMLInputElement>(null);

  const reload = () => {
    setLoading(true);
    listDocs(repoId, embed)
      .then((r) => { setDocs(r.items || []); setErr(""); })
      .catch((e) => setErr((e as Error).message))
      .finally(() => setLoading(false));
  };

  useEffect(reload, [repoId]);

  const onDelete = (id: string) => {
    deleteDoc(id, repoId, embed).then(reload).catch((e) => setErr((e as Error).message));
  };

  const saveEdit = (id: string) => {
    updateDoc(id, editText, "", repoId, embed)
      .then(() => { setEditing(null); reload(); })
      .catch((e) => setErr((e as Error).message));
  };

  const kindLabel = (k: string) =>
    k === "system" ? "系统指令" : k === "generation" ? "生成历史" : "参考资料";

  // 导出参考资料（仅 document，不含系统指令/生成历史）为可再导入的 JSON
  const doExport = () => {
    const items = docs.filter((d) => (d.kind || "document") === "document")
      .map((d) => ({ title: d.title || "", text: d.content }));
    const blob = new Blob([JSON.stringify({ version: 1, repo_id: repoId, docs: items }, null, 2)],
      { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `knowledge-${repoId || "export"}.json`; a.click();
    URL.revokeObjectURL(url);
  };

  const onPickFile = async (ev: React.ChangeEvent<HTMLInputElement>) => {
    const f = ev.target.files?.[0];
    ev.target.value = "";
    if (!f) return;
    setIo(true); setErr("");
    try {
      const parsed = JSON.parse(await f.text());
      const raw = Array.isArray(parsed) ? parsed : parsed.docs;
      if (!Array.isArray(raw)) throw new Error("JSON 里没有 docs 数组");
      const items = raw.map((r: { text?: string; content?: string; title?: string }) => ({
        text: String(r.text ?? r.content ?? ""), title: String(r.title || ""),
      })).filter((d: { text: string }) => d.text.trim());
      if (!items.length) throw new Error("没有可导入的参考资料");
      await importDocuments(repoId, items, embed);
      reload();
    } catch (e) { setErr(`导入失败：${(e as Error).message}`); }
    finally { setIo(false); }
  };

  // 关键字过滤（标题 + 正文，不区分大小写）
  const q = query.trim().toLowerCase();
  const filtered = q
    ? docs.filter((d) => (d.content + " " + (d.title || "")).toLowerCase().includes(q))
    : docs;

  // 按 kind 分区，固定顺序：系统指令 → 参考资料 → 生成历史
  const GROUPS: { key: string; label: string }[] = [
    { key: "system", label: "系统指令" },
    { key: "document", label: "参考资料" },
    { key: "generation", label: "生成历史" },
  ];
  const groups = GROUPS
    .map((g) => ({ ...g, items: filtered.filter((d) => (d.kind || "document") === g.key) }))
    .filter((g) => g.items.length > 0);

  // 单条渲染（供各分区复用）
  const renderItem = (d: RagDoc) => (
    <div key={d.id} className="kb-item">
      <div className="kb-item-head">
        <span className={`kb-tag ${d.kind}`}>
          {d.locked && <Lock size={11} style={{ verticalAlign: "-1px", marginRight: 3 }} />}
          {d.title || kindLabel(d.kind)}
        </span>
        {!d.locked && (
          <span style={{ display: "flex", gap: 6 }}>
            {editing === d.id ? (
              <button className="icon-btn" title="保存" onClick={() => saveEdit(d.id)}>
                <Check size={13} />
              </button>
            ) : (
              <button className="icon-btn" title="编辑" onClick={() => { setEditing(d.id); setEditText(d.content); }}>
                <Pencil size={13} />
              </button>
            )}
            <button className="icon-btn" style={{ background: "#d23b3b" }} title="删除" onClick={() => setDeleting(d)}>
              <Trash2 size={13} />
            </button>
          </span>
        )}
      </div>
      {editing === d.id ? (
        <textarea value={editText} onChange={(e) => setEditText(e.target.value)} rows={3} style={{ width: "100%", fontFamily: "inherit" }} />
      ) : (
        <div className="kb-item-body">{d.content}</div>
      )}
    </div>
  );

  return (
    <>
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" style={{ width: 600, maxHeight: "80vh", display: "flex", flexDirection: "column" }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h3 style={{ margin: 0 }}>知识库 · {repoName}</h3>
          <div style={{ display: "flex", gap: 6 }}>
            <button className="icon-btn" style={{ background: "transparent", color: "var(--text)" }}
              title="导出参考资料 JSON" disabled={io || loading} onClick={doExport}>
              <Download size={15} />
            </button>
            <button className="icon-btn" style={{ background: "transparent", color: "var(--text)" }}
              title="批量导入参考资料 JSON" disabled={io} onClick={() => fileRef.current?.click()}>
              <Upload size={15} />
            </button>
            <button className="icon-btn" style={{ background: "transparent", color: "var(--text)" }} onClick={onClose}>
              <X size={16} />
            </button>
          </div>
          <input ref={fileRef} type="file" accept="application/json,.json" hidden onChange={onPickFile} />
        </div>
        <p style={{ color: "var(--text-muted)", fontSize: 13, margin: "8px 0" }}>
          系统指令全局共享（带锁不可删改）；参考资料与生成历史按本仓库独立存放。AI 对话与右下角客服会检索「系统指令 + 本仓库」。
        </p>
        {/* 搜索框：按标题/正文实时过滤 */}
        <div style={{ position: "relative", marginBottom: 10 }}>
          <Search size={14} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)" }} />
          <input
            placeholder="搜索知识点（标题或正文）"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ width: "100%", paddingLeft: 30, boxSizing: "border-box" }}
          />
        </div>
        {/* 已入库条目：按类型分区展示 */}
        <div style={{ flex: 1, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 10, padding: 8, marginBottom: 10 }}>
          {loading ? (
            <p style={{ color: "var(--text-muted)", fontSize: 13, textAlign: "center" }}>载入中…</p>
          ) : err ? (
            <p style={{ color: "#d9534f", fontSize: 13 }}>载入失败：{err}（检查「设置→嵌入模型」）</p>
          ) : docs.length === 0 ? (
            <p style={{ color: "var(--text-muted)", fontSize: 13, textAlign: "center" }}>知识库为空</p>
          ) : groups.length === 0 ? (
            <p style={{ color: "var(--text-muted)", fontSize: 13, textAlign: "center" }}>没有匹配「{query}」的知识点</p>
          ) : (
            groups.map((g) => (
              <div key={g.key} className="kb-group">
                <div className="kb-group-head">{g.label} · {g.items.length}</div>
                {g.items.map(renderItem)}
              </div>
            ))
          )}
        </div>

        {/* 录入新参考资料 */}
        <input
          placeholder="标题（可选，如「角色：阿尼玛」）"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          style={{ marginBottom: 8 }}
        />
        <textarea
          placeholder="参考资料正文，可粘贴多段。空行分段后分别入库。"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={4}
          style={{ width: "100%", resize: "vertical", fontFamily: "inherit" }}
        />
        <div className="modal-actions">
          <button className="btn" onClick={onClose} disabled={busy}>关闭</button>
          <button
            className="btn primary"
            disabled={busy || !text.trim()}
            onClick={() => { onSubmit(title.trim(), text.trim()); setTitle(""); setText(""); setTimeout(reload, 800); }}
          >
            {busy ? "入库中…" : "入库"}
          </button>
        </div>
      </div>
    </div>
    {deleting && (
      <ConfirmModal
        title="删除知识点"
        message={`确认删除「${deleting.title || kindLabel(deleting.kind)}」？此操作立即生效，不可恢复。`}
        confirmText="删除"
        danger
        onConfirm={() => { onDelete(deleting.id); setDeleting(null); }}
        onCancel={() => setDeleting(null)}
      />
    )}
    </>
  );
}
