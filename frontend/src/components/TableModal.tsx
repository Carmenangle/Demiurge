import { useEffect, useRef, useState } from "react";
import { X, Upload, Plus, Trash2, RefreshCw, TableProperties, Settings } from "lucide-react";
import {
  listTables, importTableTemplate, addTableRow, updateTableRow, deleteTableRow,
  createTable, dropTable, setTableMeta,
  type DataTable, type TableConfig, type ChatModelInput,
} from "../api/tables";
import { getState, patchState, type CharacterStateDto } from "../api/state";
import {
  listChronicle, updateChronicle, deleteChronicle,
  type ChronicleEntry,
} from "../api/narrative";
import { groupStateCards } from "../lib/stateCards";
import { useManualTableFill, useTableConfig } from "../lib/useTableWorkflows";

interface Props {
  outputDir: string;
  repoId: string;
  cardName: string;
  chat: ChatModelInput;
  onClose: () => void;
}

// 统一表格弹窗（TavernDB 式）：左侧表名列表、右侧看/编选中表。
// 三类表统一呈现：通用表(table_store 增删改)、状态表(桥接 character_state)、纪要表(桥接 RAG 可编辑)。
// 好感度/纪要仍由各自引擎单一属主，这里只做可视化 + 人工编辑入口，不复制数据。
const STATE_TABLE = "角色状态表（好感度/状态）";
const CHRONICLE_TABLE = "纪要表（往事）";
const MANUAL_FILL_VIEW = "\u0000manual-fill";
const SETTINGS_VIEW = "\u0000settings";  // 左下角设置面板（非真实表名，用哨兵值切换）
const LAYER_LABEL: Record<number, string> = { 0: "细", 1: "中", 2: "粗" };

export function TableModal({ outputDir, repoId, cardName, chat, onClose }: Props) {
  const [tables, setTables] = useState<DataTable[]>([]);
  const [state, setState] = useState<CharacterStateDto | null>(null);
  const [chron, setChron] = useState<ChronicleEntry[]>([]);
  const [sel, setSel] = useState<string>(STATE_TABLE);
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = () => {
    setErr("");
    listTables(outputDir, repoId).then((d) => setTables(d.tables)).catch((e) => setErr(String((e as Error).message)));
    getState(outputDir, repoId, cardName).then(setState).catch(() => setState(null));
    listChronicle(outputDir, repoId, 100).then((d) => setChron(d.items)).catch(() => setChron([]));
  };
  useEffect(load, [outputDir, repoId, cardName]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const onImport = async (ev: React.ChangeEvent<HTMLInputElement>) => {
    const f = ev.target.files?.[0];
    ev.target.value = "";
    if (!f) return;
    setBusy(true); setErr("");
    try {
      const tpl = JSON.parse(await f.text());
      const replace = window.confirm("导入模板表结构。\n点「确定」覆盖现有通用表，点「取消」只补新表。");
      const r = await importTableTemplate(outputDir, repoId, tpl, replace);
      setTables(r.tables);
    } catch (e) { setErr(`导入失败：${(e as Error).message}`); }
    finally { setBusy(false); }
  };

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal table-modal" onClick={(e) => e.stopPropagation()}>
        <div className="table-modal-head">
          <h3 style={{ margin: 0, flex: 1 }}>数据表</h3>
          <button className="btn" disabled={busy} onClick={() => fileRef.current?.click()} title="导入 TavernDB 模板（定义通用表结构）">
            <Upload size={15} />
          </button>
          <button className="btn" onClick={onClose} title="关闭"><X size={16} /></button>
          <input ref={fileRef} type="file" accept="application/json,.json" hidden onChange={onImport} />
        </div>
        {err && <p style={{ color: "var(--danger, #c0392b)", fontSize: 13, margin: "4px 0" }}>{err}</p>}
        <div className="table-modal-body">
          <TableList
            tables={tables} state={state} chron={chron} sel={sel} creating={creating}
            onSel={(n) => { setCreating(false); setSel(n); }}
            onNew={() => { setCreating(true); setErr(""); }}
          />
          <div className="table-modal-content">
            {creating ? (
              <CreateTablePane
                busy={busy} setBusy={setBusy} setErr={setErr}
                onCancel={() => setCreating(false)}
                onCreate={async (spec) => {
                  setBusy(true); setErr("");
                  try {
                    const r = await createTable(outputDir, repoId, spec);
                    setTables(r.tables); setCreating(false); setSel(spec.name);
                  } catch (e) { setErr(`建表失败：${(e as Error).message}`); }
                  finally { setBusy(false); }
                }}
              />
            ) : (
              <TablePane
                sel={sel} tables={tables} state={state} chron={chron}
                outputDir={outputDir} repoId={repoId} cardName={cardName}
                chat={chat}
                busy={busy} setBusy={setBusy} setErr={setErr}
                setTables={setTables} setState={setState}
                onDropped={(firstName) => setSel(firstName)}
                reloadChron={() =>
                  listChronicle(outputDir, repoId, 100).then((d) => setChron(d.items)).catch(() => setChron([]))}
                reloadAll={load}
                openManualFill={() => setSel(MANUAL_FILL_VIEW)}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function TableList({ tables, state, chron, sel, creating, onSel, onNew }: {
  tables: DataTable[];
  state: CharacterStateDto | null;
  chron: ChronicleEntry[];
  sel: string;
  creating: boolean;
  onSel: (name: string) => void;
  onNew: () => void;
}) {
  const stateRows = state ? Object.keys(state.数值).length + Object.keys(state.叙事).length : 0;
  const stateActors = state ? groupStateCards(state).length : 0;
  const Item = ({ name, meta }: { name: string; meta: string }) => (
    <button
      className={`table-list-item ${!creating && sel === name ? "is-active" : ""}`}
      onClick={() => onSel(name)}
    >
      <div className="table-list-name">{name}</div>
      <div className="table-list-meta">{meta}</div>
    </button>
  );
  return (
    <div className="table-list">
      <div className="table-list-group">剧情引擎</div>
      <Item name={STATE_TABLE} meta={`${stateActors} 人 · ${stateRows} 项`} />
      <Item name={CHRONICLE_TABLE} meta={`${chron.length} 条`} />
      <div className="table-list-group">剧情资料</div>
      {tables.map((t) => (
        <Item key={t.uid} name={t.name} meta={`${t.rows.length} 行 · ${t.columns.length} 列`} />
      ))}
      <button
        className={`table-list-item table-list-new ${creating ? "is-active" : ""}`}
        onClick={onNew}
      >
        <Plus size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />新建表
      </button>
      <button
        className={`table-list-item table-list-settings ${!creating && sel === SETTINGS_VIEW ? "is-active" : ""}`}
        onClick={() => onSel(SETTINGS_VIEW)}
        title="填表设置：填表频率/回看/最小长度等"
      >
        <Settings size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />填表设置
      </button>
    </div>
  );
}

function TablePane({
  sel, tables, state, chron, outputDir, repoId, cardName,
  busy, setBusy, setErr, setTables, setState, reloadChron, onDropped,
  chat, reloadAll, openManualFill,
}: {
  sel: string;
  tables: DataTable[];
  state: CharacterStateDto | null;
  chron: ChronicleEntry[];
  outputDir: string;
  repoId: string;
  cardName: string;
  chat: ChatModelInput;
  busy: boolean;
  setBusy: (b: boolean) => void;
  setErr: (s: string) => void;
  setTables: (t: DataTable[]) => void;
  setState: (s: CharacterStateDto) => void;
  reloadChron: () => void;
  reloadAll: () => void;
  openManualFill: () => void;
  onDropped: (firstName: string) => void;
}) {
  if (sel === STATE_TABLE) {
    return <StatePane state={state} outputDir={outputDir} repoId={repoId} cardName={cardName}
      busy={busy} setBusy={setBusy} setErr={setErr} setState={setState} />;
  }
  if (sel === CHRONICLE_TABLE) {
    return <ChroniclePane chron={chron} outputDir={outputDir} repoId={repoId}
      busy={busy} setBusy={setBusy} setErr={setErr} reload={reloadChron}
      openManualFill={openManualFill} />;
  }
  if (sel === MANUAL_FILL_VIEW) {
    return <ManualFillPane outputDir={outputDir} repoId={repoId} cardName={cardName}
      chat={chat} busy={busy} setBusy={setBusy} setErr={setErr} reloadAll={reloadAll} />;
  }
  if (sel === SETTINGS_VIEW) {
    return <SettingsPane outputDir={outputDir} repoId={repoId}
      busy={busy} setBusy={setBusy} setErr={setErr} />;
  }
  const table = tables.find((t) => t.name === sel);
  if (!table) return <p style={{ color: "var(--text-muted)" }}>选择左侧的表查看内容，或点「新建表」加一张自定义表。</p>;
  return <GenericPane table={table} outputDir={outputDir} repoId={repoId}
    busy={busy} setBusy={setBusy} setErr={setErr} setTables={setTables} onDropped={onDropped} />;
}

// 通用表：可增行/删行/改单元格（AI 每轮也会自动填，这里是人工编辑入口）
function GenericPane({ table, outputDir, repoId, busy, setBusy, setErr, setTables, onDropped }: {
  table: DataTable;
  outputDir: string;
  repoId: string;
  busy: boolean;
  setBusy: (b: boolean) => void;
  setErr: (s: string) => void;
  setTables: (t: DataTable[]) => void;
  onDropped: (firstName: string) => void;
}) {
  const wrap = async (fn: () => Promise<{ tables: DataTable[] }>) => {
    setBusy(true); setErr("");
    try { setTables((await fn()).tables); }
    catch (e) { setErr(String((e as Error).message)); }
    finally { setBusy(false); }
  };
  const onCell = (row: number, col: string, val: string) =>
    wrap(() => updateTableRow(outputDir, repoId, table.name, row, { [col]: val }));
  const onAdd = () => wrap(() => addTableRow(outputDir, repoId, table.name, {}));
  const onDel = (row: number) => wrap(() => deleteTableRow(outputDir, repoId, table.name, row));
  const onDropTable = async () => {
    if (!window.confirm(`删除整张「${table.name}」表？此表所有行都会删掉。`)) return;
    setBusy(true); setErr("");
    try {
      const r = await dropTable(outputDir, repoId, table.name);
      setTables(r.tables);
      onDropped(STATE_TABLE);
    } catch (e) { setErr(String((e as Error).message)); }
    finally { setBusy(false); }
  };
  const mode = table.mode === "retrieval" ? "retrieval" : "full";
  const onMode = async (m: string) => {
    setBusy(true); setErr("");
    try { setTables((await setTableMeta(outputDir, repoId, table.name, { mode: m })).tables); }
    catch (e) { setErr(String((e as Error).message)); }
    finally { setBusy(false); }
  };

  return (
    <>
      <div className="table-pane-head">
        <div style={{ flex: 1, minWidth: 0 }}>
          {table.note && <p className="table-note">{table.note}</p>}
          {table.rule && <p className="table-note table-rule">更新规则：{table.rule}</p>}
          {table.keyCol && <p className="table-sub">身份列：{table.keyCol}</p>}
          <div className="table-mode-row">
            <span className="table-sub">注入模式：</span>
            <label className="table-mode-opt" title="整表现值每轮进提示词。适合小的状态表（好感度/背包/任务）。">
              <input type="radio" name={`mode-${table.uid}`} checked={mode === "full"}
                disabled={busy} onChange={() => onMode("full")} />全量
            </label>
            <label className="table-mode-opt" title="行索引进 RAG，只召回与本轮剧情相关的行。适合大表（名册/设定/日志），省 token。需已配嵌入模型。">
              <input type="radio" name={`mode-${table.uid}`} checked={mode === "retrieval"}
                disabled={busy} onChange={() => onMode("retrieval")} />检索
            </label>
          </div>
        </div>
        <button className="btn danger" disabled={busy} onClick={onDropTable} title="删除整张表">
          <Trash2 size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />删表
        </button>
      </div>
      <div className="table-scroll">
        <div className="card-grid">
          {table.rows.map((r, ri) => {
            const keyIdx = table.keyCol ? table.columns.indexOf(table.keyCol) : -1;
            const title = keyIdx >= 0 ? (r[keyIdx] || "").trim() : "";
            return (
              <div className="row-card" key={ri}>
                <div className="row-card-head">
                  <span className="row-card-idx">#{ri}</span>
                  {title && <span className="row-card-title">{title}</span>}
                  <button className="btn row-card-del" disabled={busy} onClick={() => onDel(ri)} title="删除此行">
                    <Trash2 size={13} />
                  </button>
                </div>
                <div className="row-card-fields">
                  {table.columns.map((c, ci) => {
                    const cur = r[ci] ?? "";
                    const isNum = table.colTypes[c] === "数字";
                    const long = !isNum && (cur.length > 18 || cur.includes("\n"));
                    return (
                      <label className={`field ${long ? "field-wide" : ""}`} key={c}>
                        <span className="field-label">{c}{c === table.keyCol && <b className="field-key" title="身份列">·标识</b>}</span>
                        {/* 非受控：key 带当前值，落库刷新后 remount 反映新值；onBlur 提交变化 */}
                        {long ? (
                          <textarea
                            key={`${ri}-${ci}-${cur}`} className="field-input" rows={3}
                            defaultValue={cur} disabled={busy}
                            onBlur={(e) => { if (cur !== e.target.value) onCell(ri, c, e.target.value); }}
                          />
                        ) : (
                          <input
                            key={`${ri}-${ci}-${cur}`} className="field-input"
                            type={isNum ? "number" : "text"} defaultValue={cur} disabled={busy}
                            onBlur={(e) => { if (cur !== e.target.value) onCell(ri, c, e.target.value); }}
                          />
                        )}
                      </label>
                    );
                  })}
                </div>
              </div>
            );
          })}
          {table.rows.length === 0 && (
            <p className="card-empty">空表。剧情推进时 AI 会自动填，也可点下方「新增行」手动加。</p>
          )}
        </div>
      </div>
      <button className="btn" disabled={busy} onClick={onAdd} style={{ marginTop: 8 }}>
        <Plus size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />新增行
      </button>
    </>
  );
}

// 角色状态表（虚拟）：按角色聚合数值+叙事，同卡展示好感度、状态、来源与依据。
function StatePane({ state, outputDir, repoId, cardName, busy, setBusy, setErr, setState }: {
  state: CharacterStateDto | null;
  outputDir: string;
  repoId: string;
  cardName: string;
  busy: boolean;
  setBusy: (b: boolean) => void;
  setErr: (s: string) => void;
  setState: (s: CharacterStateDto) => void;
}) {
  if (!state) return <p style={{ color: "var(--text-muted)" }}>加载中…</p>;
  const numEntries = Object.entries(state.数值);
  const narrEntries = Object.entries(state.叙事);
  if (numEntries.length === 0 && narrEntries.length === 0) {
    return <p style={{ color: "var(--text-muted)" }}>本作品线还没有状态。开始剧情对话后引擎自动写入好感度/态度等。</p>;
  }
  const commit = async (field: string, value: number | string) => {
    setBusy(true); setErr("");
    try { setState(await patchState(outputDir, repoId, cardName, [{ field, value }])); }
    catch (e) { setErr(String((e as Error).message)); }
    finally { setBusy(false); }
  };
  const cards = groupStateCards(state);
  return (
    <div className="table-scroll">
      <div className="card-grid state-card-grid">
        {cards.map((card, index) => (
          <section className="row-card state-card" key={card.name}>
            <div className="row-card-head">
              <span className="row-card-idx">#{index + 1}</span>
              <span className="row-card-title">{card.name}</span>
              <span className="state-field-count">{card.fields.length} 个字段</span>
            </div>
            <div className="row-card-fields">
              {card.fields.map((field) => (
                <label className={`field ${field.kind === "text" || field.evidence ? "field-wide" : ""}`} key={field.path}>
                  <span className="field-label">
                    {field.label}
                    {field.kind === "number" && <span>（{field.min}～{field.max}）</span>}
                    <b className={`state-source ${field.source === "user" ? "is-user" : ""}`}>
                      {field.source === "user" ? "手改" : "剧情"}
                    </b>
                  </span>
                  {field.kind === "number" ? (
                    <input key={`${field.path}-${field.value}`} type="number" className="field-input state-value"
                      min={field.min} max={field.max} defaultValue={field.value} disabled={busy}
                      onBlur={(e) => {
                        const n = Number(e.target.value);
                        if (Number.isFinite(n) && n !== field.value) commit(field.path, n);
                      }} />
                  ) : (
                    <textarea key={`${field.path}-${field.value}`} className="field-input state-value" rows={2}
                      defaultValue={String(field.value)} disabled={busy}
                      onBlur={(e) => { if (e.target.value !== field.value) commit(field.path, e.target.value); }} />
                  )}
                  {field.evidence && (
                    <span className="state-evidence"><b>依据</b>{field.evidence}</span>
                  )}
                </label>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

// 纪要表（虚拟）：桥接 RAG（narrative_store）。可改正文/删条目 + 重建索引，即"RAG 可编辑"。
function ChroniclePane({ chron, outputDir, repoId, busy, setBusy, setErr, reload, openManualFill }: {
  chron: ChronicleEntry[];
  outputDir: string;
  repoId: string;
  busy: boolean;
  setBusy: (b: boolean) => void;
  setErr: (s: string) => void;
  reload: () => void;
  openManualFill: () => void;
}) {
  const wrap = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr("");
    try { await fn(); reload(); }
    catch (e) { setErr(String((e as Error).message)); }
    finally { setBusy(false); }
  };
  const onEdit = (e: ChronicleEntry, patch: Partial<ChronicleEntry>) =>
    wrap(() => updateChronicle(outputDir, repoId, e.rowid, {
      text: patch.text ?? e.text,
      overview: patch.overview ?? e.overview,
      dialogue: patch.dialogue ?? e.dialogue,
      characters: patch.characters ?? e.characters,
      turn_start: e.turn_start,
      turn_end: e.turn_end,
      layer: e.layer,
      keywords: e.keywords,
    }));
  const onDel = (rowid: number) => wrap(() => deleteChronicle(outputDir, repoId, [rowid]));
  return (
    <>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 6 }}>
        <button className="btn" disabled={busy} onClick={openManualFill}
          title="查看各表未记录状态，并从当前会话手动补填或局部重填">
          <RefreshCw size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />重建索引
        </button>
      </div>
      <div className="table-scroll">
        <div className="card-grid">
          {chron.map((e) => (
            <div className="row-card" key={e.rowid}>
              <div className="row-card-head">
                <span className="row-card-idx">T{e.turn_start}–{e.turn_end}</span>
                <span className="row-card-title">{LAYER_LABEL[e.layer] || e.layer}</span>
                <button className="btn row-card-del" disabled={busy} onClick={() => onDel(e.rowid)} title="删除此纪要">
                  <Trash2 size={13} />
                </button>
              </div>
              <div className="row-card-fields">
                <label className="field field-wide">
                  <span className="field-label">概览</span>
                  <textarea key={`o-${e.rowid}-${e.overview.length}`} className="field-input" rows={2}
                    defaultValue={e.overview} disabled={busy}
                    onBlur={(ev) => { if (ev.target.value !== e.overview) onEdit(e, { overview: ev.target.value }); }} />
                </label>
                <label className="field field-wide">
                  <span className="field-label">详细纪要</span>
                  <textarea key={`c-${e.rowid}-${e.text.length}`} className="field-input" rows={4}
                    defaultValue={e.text} disabled={busy}
                    onBlur={(ev) => { if (ev.target.value.trim() && ev.target.value !== e.text) onEdit(e, { text: ev.target.value }); }} />
                </label>
                <label className="field field-wide">
                  <span className="field-label">重要对白</span>
                  <textarea key={`d-${e.rowid}-${e.dialogue.length}`} className="field-input" rows={2}
                    defaultValue={e.dialogue} disabled={busy}
                    onBlur={(ev) => { if (ev.target.value !== e.dialogue) onEdit(e, { dialogue: ev.target.value }); }} />
                </label>
                <label className="field field-wide">
                  <span className="field-label">出场人物</span>
                  <input key={`p-${e.rowid}-${e.characters.join(",")}`} className="field-input"
                    defaultValue={e.characters.join("、")} disabled={busy}
                    onBlur={(ev) => {
                      const characters = ev.target.value.split(/[、,，\n]/).map((name) => name.trim()).filter(Boolean);
                      if (characters.join("\n") !== e.characters.join("\n")) onEdit(e, { characters });
                    }} />
                </label>
              </div>
            </div>
          ))}
          {chron.length === 0 && (
            <p className="card-empty">
              本作品线还没有纪要。剧情推进每若干回合，引擎自动把近期事件压成一条纪要。
            </p>
          )}
        </div>
      </div>
    </>
  );
}

function ManualFillPane({ outputDir, repoId, cardName, chat, busy, setBusy, setErr, reloadAll }: {
  outputDir: string;
  repoId: string;
  cardName: string;
  chat: ChatModelInput;
  busy: boolean;
  setBusy: (b: boolean) => void;
  setErr: (s: string) => void;
  reloadAll: () => void;
}) {
  const workflow = useManualTableFill(
    outputDir, repoId, cardName, chat, { busy, setBusy, setError: setErr }, reloadAll,
  );
  const { status, selected, recentTurns, batchTurns, result, run, toggle,
    setRecentTurns, setBatchTurns } = workflow;

  return (
    <div className="manual-fill-workbench">
      <section className="manual-fill-status">
        <div className="table-pane-head">
          <div>
            <strong>表格状态</strong>
            <div className="table-sub">当前会话共 {status?.total_turns ?? 0} 层</div>
          </div>
        </div>
        <div className="manual-fill-status-list">
          {status?.items.map((item) => (
            <label className="manual-fill-status-item" key={item.uid}>
              <input type="checkbox" checked={selected.includes(item.uid)} disabled={busy || !item.selectable}
                onChange={(event) => toggle(item.uid, event.target.checked)} />
              <span>
                <b>{item.name}</b>
                <small>每 {item.frequency} 层 · 未记录 {item.unrecorded} 层 · 上次 T{item.last_turn || 0}</small>
              </span>
            </label>
          ))}
          {!status && <p className="card-empty">正在读取表格状态…</p>}
        </div>
      </section>
      <section className="manual-fill-form">
        <h4>手动填表</h4>
        <label className="create-field">
          <span className="create-label">手动处理最近 N 层</span>
          <input type="number" min={1} max={200} value={recentTurns} disabled={busy}
            onChange={(e) => setRecentTurns(Math.max(1, Math.min(200, Number(e.target.value) || 1)))} />
        </label>
        <label className="create-field">
          <span className="create-label">每 N 层合并一次</span>
          <input type="number" min={1} max={50} value={batchTurns} disabled={busy}
            onChange={(e) => setBatchTurns(Math.max(1, Math.min(50, Number(e.target.value) || 1)))} />
        </label>
        <button className="btn primary" disabled={busy || !status || !selected.length} onClick={run}>
          <RefreshCw size={14} style={{ verticalAlign: "-2px", marginRight: 5 }} />开始处理
        </button>
        {result && <p className="manual-fill-result">{result}</p>}
      </section>
    </div>
  );
}

// 填表设置面板：6 参数。搭车范式下真正生效的是「自动填表频率」（省 token 主开关）和「回复最小长度」，
// 其余项作跨度/容错语义参与。改动即存盘。
const CFG_FIELDS: { key: keyof TableConfig; label: string; hint: string; min: number; max: number }[] = [
  { key: "chronicleEvery", label: "纪要记录频率", hint: "每 N 个助手回合生成一条包含概览、详细纪要、重要对白与出场人物的纪要。", min: 1, max: 20 },
  { key: "fillEvery", label: "自动填表频率", hint: "主角、重要角色、技能、背包、任务和选项每 N 轮更新一次；全局数据与角色状态仍每轮更新。1=全部每轮填。", min: 1, max: 20 },
  { key: "minReplyLen", label: "AI 回复最小长度", hint: "正文短于此字数的回复不写回表（碎回复无信息量）。", min: 0, max: 2000 },
  { key: "contextTurns", label: "填表上下文层数", hint: "填表轮提示 AI 结合最近 N 轮事件补记，避免跳轮遗漏。", min: 1, max: 20 },
  { key: "batchTurns", label: "批处理层数", hint: "一次填表覆盖的回合跨度。", min: 1, max: 20 },
  { key: "skipLatest", label: "跳过最新回复数", hint: "开局不足此轮次不填（避免开局稀薄内容硬填）。", min: 0, max: 20 },
  { key: "maxRetry", label: "填表最大重试", hint: "解析失败时的容错次数。", min: 0, max: 10 },
];

function SettingsPane({ outputDir, repoId, busy, setBusy, setErr }: {
  outputDir: string;
  repoId: string;
  busy: boolean;
  setBusy: (b: boolean) => void;
  setErr: (s: string) => void;
}) {
  const { config: cfg, commit } = useTableConfig(
    outputDir, repoId, { busy, setBusy, setError: setErr },
  );

  if (!cfg) return <p style={{ color: "var(--text-muted)" }}>加载中…</p>;
  return (
    <div className="table-scroll">
      <div className="create-table-form">
        <h4 style={{ margin: "0 0 2px" }}>
          <Settings size={15} style={{ verticalAlign: "-2px", marginRight: 6 }} />填表设置
        </h4>
        <p className="table-sub" style={{ margin: "0 0 10px" }}>
          控制何时填表、写多少，省 token。检索表的内容不占每轮提示词，按剧情相关性召回。
        </p>
        {CFG_FIELDS.map((f) => (
          <label className="create-field" key={f.key}>
            <span className="create-label">{f.label}</span>
            <input type="number" min={f.min} max={f.max} value={cfg[f.key]} disabled={busy}
              onChange={(e) => {
                const n = Number(e.target.value);
                if (Number.isFinite(n)) commit(f.key, Math.max(f.min, Math.min(f.max, Math.round(n))));
              }} />
            <span className="create-hint">{f.hint}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

// 引导式建表：起名 + 说清记什么/何时更新 + 逐列(名+类型+身份列)。用户全程不碰 SQL/行号。
interface DraftCol { name: string; type: string; isKey: boolean }

function CreateTablePane({ busy, setBusy, setErr, onCancel, onCreate }: {
  busy: boolean;
  setBusy: (b: boolean) => void;
  setErr: (s: string) => void;
  onCancel: () => void;
  onCreate: (spec: {
    name: string; columns: string[]; note: string; rule: string;
    col_types: Record<string, string>; key_col: string;
  }) => void;
}) {
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [rule, setRule] = useState("");
  const [cols, setCols] = useState<DraftCol[]>([{ name: "", type: "文本", isKey: false }]);

  const setCol = (i: number, patch: Partial<DraftCol>) =>
    setCols((cs) => cs.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const addCol = () => setCols((cs) => [...cs, { name: "", type: "文本", isKey: false }]);
  const rmCol = (i: number) => setCols((cs) => cs.filter((_, j) => j !== i));
  // 身份列单选：勾一个把其余取消
  const setKey = (i: number) => setCols((cs) => cs.map((c, j) => ({ ...c, isKey: j === i ? !c.isKey : false })));

  const validCols = cols.map((c) => ({ ...c, name: c.name.trim() })).filter((c) => c.name);
  const canCreate = name.trim() && validCols.length > 0 && !busy;

  const submit = () => {
    const columns = validCols.map((c) => c.name);
    const col_types: Record<string, string> = {};
    validCols.forEach((c) => { col_types[c.name] = c.type; });
    const key = validCols.find((c) => c.isKey)?.name || "";
    onCreate({ name: name.trim(), columns, note: note.trim(), rule: rule.trim(), col_types, key_col: key });
  };

  return (
    <div className="table-scroll" onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onCancel(); } }}>
      <div className="create-table-form">
        <h4 style={{ margin: "0 0 2px" }}><TableProperties size={15} style={{ verticalAlign: "-2px", marginRight: 6 }} />新建数据表</h4>
        <p className="table-sub" style={{ margin: "0 0 8px" }}>
          起个名、说清这张表记什么和何时更新、加几列。建好后 AI 每轮会按你写的规则自动填，也可手动改。
        </p>

        <label className="create-field">
          <span className="create-label">表名 <b className="req">*</b></span>
          <input value={name} disabled={busy} placeholder="例：背包物品表 / 任务表 / 符箓表"
            onChange={(e) => setName(e.target.value)} />
        </label>

        <label className="create-field">
          <span className="create-label">这张表记什么</span>
          <textarea value={note} disabled={busy} rows={2}
            placeholder="例：记录主角随身携带的物品，每行一件。"
            onChange={(e) => setNote(e.target.value)} />
        </label>

        <label className="create-field">
          <span className="create-label">何时更新（增 / 改 / 删）</span>
          <textarea value={rule} disabled={busy} rows={3}
            placeholder="例：获得新物品时新增一行；数量变化时改对应行；用光或丢弃时删除该行。"
            onChange={(e) => setRule(e.target.value)} />
          <span className="create-hint">用大白话写清"什么时候该加行、改行、删行"，这是 AI 填表的依据（替代原版要写的四段 SQL）。</span>
        </label>

        <div className="create-field">
          <span className="create-label">列（栏目）<b className="req">*</b></span>
          <span className="create-hint" style={{ marginTop: 0, marginBottom: 6 }}>
            每列就是表格的一栏（如「物品名称」「数量」）。身份列＝这一栏能唯一认出一条记录（如角色名、物品名），
            AI 靠它找到「同一条」来改或删，而不是靠行号；一般给最像"名字"的那列勾上，可不勾。
          </span>
          <div className="create-cols">
            <div className="create-col-row create-col-head">
              <span className="create-col-name">列名</span>
              <span className="create-col-type">类型</span>
              <span className="create-key-head">身份列</span>
              <span className="create-col-del" />
            </div>
            {cols.map((c, i) => (
              <div className="create-col-row" key={i}>
                <input className="create-col-name" value={c.name} disabled={busy}
                  placeholder={`第 ${i + 1} 列名，如 物品名称`}
                  onChange={(e) => setCol(i, { name: e.target.value })} />
                <select className="create-col-type" value={c.type} disabled={busy}
                  onChange={(e) => setCol(i, { type: e.target.value })}>
                  <option value="文本">文本</option>
                  <option value="数字">数字</option>
                </select>
                <label className="create-key" title="身份列：AI 靠它认出「同一条」来更新/删除，而不是靠行号（相当于唯一标识，如角色名/物品名）">
                  <input type="checkbox" checked={c.isKey} disabled={busy} onChange={() => setKey(i)} />
                </label>
                <button className="btn create-col-del" disabled={busy || cols.length <= 1} onClick={() => rmCol(i)} title="删除此列">
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>
          <button className="btn" disabled={busy} onClick={addCol} style={{ marginTop: 6 }}>
            <Plus size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />加一列
          </button>
        </div>

        <div className="create-actions">
          <button className="btn primary" disabled={!canCreate} onClick={submit}>建表</button>
          <button className="btn" disabled={busy} onClick={onCancel}>取消</button>
          <span className="create-hint" style={{ marginLeft: "auto" }}>标 * 为必填</span>
        </div>
      </div>
    </div>
  );
}
