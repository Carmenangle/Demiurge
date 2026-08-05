import { useEffect, useRef, useState } from "react";
import { Plus, Trash2, Download, BookOpen, Regex as RegexIcon } from "lucide-react";
import { PageShell } from "../components/layout/PageShell";
import { ConfirmModal } from "../components/Modal";
import { CardPreviewModal } from "../components/CardPreviewModal";
import { downloadJson } from "../lib/download";
import {
  listCharacters, scanLooseCards, snapshotToWork, importCharacter, deleteCharacter,
  exportChat, characterDetail, avatarUrl,
  type CardSummary, type ImportConflict,
} from "../api/characters";

// 覆盖确认的待决状态：记住用户选的文件与冲突信息，确认后带 overwrite 重发
interface PendingOverwrite {
  file: File;
  conflict: ImportConflict;
}

export function CharacterCards({ characterDir, outputDir, worldbookDir, persona, onOpenCard }: {
  characterDir: string;
  outputDir: string;
  worldbookDir: string;  // 已设则导入/扫描时把卡内嵌世界书外拆成独立世界书并从卡剥离
  persona: { name: string; content: string };  // 当前选中的用户人设，随卡快照进作品（绑定）
  onOpenCard: (cardName: string) => void;
}) {
  const [cards, setCards] = useState<CardSummary[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<CardSummary | null>(null);
  const [pending, setPending] = useState<PendingOverwrite | null>(null);
  const [preview, setPreview] = useState<CardSummary | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // 用此卡新建作品：先把源库卡快照进作品仓库文件夹（卡+世界书+正则+头像），再建作品进对话。
  // 快照失败不阻断（运行时回退读源库）。日后改源卡不回灌已建作品（快照隔离）。
  const startWork = async (cardName: string) => {
    if (outputDir) {
      await snapshotToWork(characterDir, cardName, outputDir, persona).catch(() => null);
    }
    setPreview(null);
    onOpenCard(cardName);
  };

  const refresh = () => {
    if (!characterDir) { setCards([]); return; }
    // 先扫描根目录下手动放入的散装卡文件（解析入库+拆出世界书/正则+删源），再列出——
    // 用户把卡丢进文件夹即可，刷新就出现，不必走导入按钮。扫描失败不阻断列表。
    scanLooseCards(characterDir, worldbookDir)
      .catch(() => null)
      .finally(() => {
        listCharacters(characterDir).then((r) => setCards(r.items)).catch((e) => setErr(String(e.message || e)));
      });
  };
  useEffect(refresh, [characterDir, worldbookDir]);

  const doImport = async (file: File, overwrite: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      const res = await importCharacter(file, characterDir, overwrite, worldbookDir);
      setPending(null);
      refresh();
      await startWork(res.name);  // 卡即作品：导入成功即快照进作品文件夹并建/复用作品进对话
    } catch (e) {
      const conflict = (e as { conflict?: ImportConflict }).conflict;
      if (conflict) setPending({ file, conflict });
      else setErr(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";  // 允许重复选同一文件
    if (file) doImport(file, false);
  };

  // 覆盖前若有对话记录，先导出成 .json 让用户留存，再覆盖
  const exportThenOverwrite = async () => {
    if (!pending) return;
    try {
      const { chat } = await exportChat(characterDir, pending.conflict.name);
      downloadJson(chat, `${pending.conflict.name}-chat`);
    } catch { /* 导出失败不阻断覆盖 */ }
    doImport(pending.file, true);
  };

  // 导出卡本体（card.json，可再导入的 ST 格式；内嵌世界书/正则含在其中）
  const exportCard = async (name: string) => {
    try {
      const card = await characterDetail(characterDir, name);
      downloadJson(card, name);
    } catch (e) {
      setErr(String((e as Error).message || e));
    }
  };

  return (
    <PageShell
      title="角色卡"
      actions={
        <button className="btn" disabled={!characterDir || busy} onClick={() => fileRef.current?.click()}>
          <Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          {busy ? "导入中…" : "导入角色卡"}
        </button>
      }
    >
      <input ref={fileRef} type="file" accept=".json,.png" style={{ display: "none" }} onChange={onPick} />
      {!characterDir && (
        <p style={{ color: "var(--text-muted)" }}>
          请先到「设置 → 路径 → 角色卡文件夹」设置存放目录，再导入角色卡。
        </p>
      )}
      {err && <p style={{ color: "var(--danger, #c0392b)" }}>{err}</p>}
      {characterDir && cards.length === 0 && !err && (
        <p style={{ color: "var(--text-muted)" }}>
          还没有角色卡。这里是角色卡源库，供浏览与预览；点「导入角色卡」选 .json/.png，或直接把卡文件
          放进角色卡文件夹后刷新本页——都会自动解出内嵌世界书与正则。双击卡片可预览，用「新建作品」进对话
          （作品会各自保存卡与世界书快照，改源卡不影响已建作品）。
        </p>
      )}
      <CardGrid base={characterDir} cards={cards} onDelete={setDeleting} onExport={(c) => exportCard(c.name)} onOpen={setPreview} />

      {preview && (
        <CardPreviewModal
          base={characterDir}
          folder={preview.folder}
          name={preview.name}
          onClose={() => setPreview(null)}
          onNewWork={() => { void startWork(preview.name); }}
        />
      )}

      {pending && (
        <ConfirmModal
          title="已存在同名角色卡"
          message={
            pending.conflict.has_chat
              ? `「${pending.conflict.name}」已存在且有对话记录。覆盖前可先导出对话备份。是否导出并覆盖？`
              : `「${pending.conflict.name}」已存在。是否用新卡覆盖？`
          }
          confirmText={pending.conflict.has_chat ? "导出并覆盖" : "覆盖"}
          danger
          onConfirm={pending.conflict.has_chat ? exportThenOverwrite : () => doImport(pending.file, true)}
          onCancel={() => setPending(null)}
        />
      )}
      {deleting && (
        <ConfirmModal
          title="删除角色卡"
          message={`确定删除「${deleting.name}」？其文件夹（含卡、世界书、对话记录）将一并删除。`}
          confirmText="删除"
          danger
          onConfirm={async () => {
            await deleteCharacter(characterDir, deleting.name).catch((e) => setErr(String(e.message || e)));
            setDeleting(null);
            refresh();
          }}
          onCancel={() => setDeleting(null)}
        />
      )}
    </PageShell>
  );
}

function CardGrid({ base, cards, onDelete, onExport, onOpen }: {
  base: string;
  cards: CardSummary[];
  onDelete: (c: CardSummary) => void;
  onExport: (c: CardSummary) => void;
  onOpen: (c: CardSummary) => void;
}) {
  if (cards.length === 0) return null;
  const badge = {
    display: "inline-flex", alignItems: "center", gap: 3, fontSize: 11,
    color: "var(--text-muted)", marginRight: 8,
  } as const;
  return (
    <div className="repo-grid">
      {cards.map((c) => (
        <div className="repo-card" key={c.folder}>
          <div className="repo-cover" onDoubleClick={() => onOpen(c)} title="双击预览角色卡">
            {c.has_avatar
              ? <img src={avatarUrl(base, c.folder)} alt={c.name} loading="lazy" />
              : <>暂无立绘</>}
          </div>
          <div className="repo-tools">
            <button className="icon-btn" title="导出卡 JSON" onClick={() => onExport(c)}>
              <Download size={15} />
            </button>
            <button className="icon-btn" title="删除" onClick={() => onDelete(c)}>
              <Trash2 size={15} />
            </button>
          </div>
          <div className="repo-name">{c.name}</div>
          {(c.has_worldbook || c.has_regex) && (
            <div style={{ marginTop: 2 }}>
              {c.has_worldbook && <span style={badge} title="含内嵌世界书"><BookOpen size={12} /> 世界书</span>}
              {c.has_regex && <span style={badge} title="含内嵌正则"><RegexIcon size={12} /> 正则</span>}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
