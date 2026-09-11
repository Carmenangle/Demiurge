import { useEffect, useRef, useState } from "react";
import { X, Plus, Trash2, Upload, Download, GripVertical } from "lucide-react";
import { ConfirmModal } from "./Modal";
import { downloadJson } from "../lib/download";
import { listRegex, saveRegex, testRegex } from "../api/regex";
import { characterRegex } from "../api/characters";
import { getPresetRegex, savePresetRegex } from "../api/preset";
import { Placement, type RegexScript } from "../lib/regexEngine";

type Scope = "global" | "preset";

// 把导入的 ST 正则对象规整成本地 RegexScript（字段已是 camelCase，仅补默认值+校验 findRegex）
function normalize(raw: Record<string, unknown>): RegexScript | null {
  const find = raw.findRegex;
  if (typeof find !== "string" || !find) return null;
  const pl = Array.isArray(raw.placement) ? (raw.placement as number[]) : [Placement.AI_OUTPUT];
  return {
    scriptName: typeof raw.scriptName === "string" ? raw.scriptName : "导入正则",
    findRegex: find,
    replaceString: typeof raw.replaceString === "string" ? raw.replaceString : "",
    trimStrings: Array.isArray(raw.trimStrings) ? (raw.trimStrings as string[]) : [],
    placement: pl.length ? pl : [Placement.AI_OUTPUT],
    disabled: !!raw.disabled,
    markdownOnly: !!raw.markdownOnly,
    promptOnly: !!raw.promptOnly,
    runOnEdit: raw.runOnEdit !== false,
    minDepth: typeof raw.minDepth === "number" ? raw.minDepth : null,
    maxDepth: typeof raw.maxDepth === "number" ? raw.maxDepth : null,
    substituteRegex: typeof raw.substituteRegex === "number" ? raw.substituteRegex : 0,
  };
}

// 全局正则管理：跨作品生效的一组 ST 正则脚本。列表 + 开关 + 增删改 + 试跑。
// 显示层（markdownOnly）在前端渲染时跑；存储/发送档在后端 agent_graph 跑。

const PLACEMENT_OPTS: { value: number; label: string }[] = [
  { value: Placement.USER_INPUT, label: "用户输入" },
  { value: Placement.AI_OUTPUT, label: "AI 输出" },
  { value: Placement.SLASH_COMMAND, label: "快捷命令" },
  { value: Placement.WORLD_INFO, label: "世界信息" },
  { value: Placement.REASONING, label: "推理" },
];

function blank(): RegexScript {
  return {
    scriptName: "新正则", findRegex: "", replaceString: "", trimStrings: [],
    placement: [Placement.AI_OUTPUT], disabled: false, markdownOnly: true,
    promptOnly: false, runOnEdit: true, minDepth: null, maxDepth: null,
    substituteRegex: 0,
  };
}

export function RegexModal({ onClose, cardName, characterDir, presetName, presetDir }: {
  onClose: () => void; cardName?: string; characterDir?: string;
  presetName?: string; presetDir?: string;
}) {
  const hasPreset = !!(presetName && presetDir);
  const [scope, setScope] = useState<Scope>("global");
  const [scripts, setScripts] = useState<RegexScript[]>([]);
  const [cardScripts, setCardScripts] = useState<RegexScript[]>([]);  // 当前卡内嵌正则（只读展示）
  const [activeIdx, setActiveIdx] = useState<number | null>(null);
  const [confirmDel, setConfirmDel] = useState(false);
  const [testText, setTestText] = useState("");
  const [testOut, setTestOut] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [importErr, setImportErr] = useState<string | null>(null);
  const [dragIdx, setDragIdx] = useState<number | null>(null);   // 正在拖的行
  const [dropIdx, setDropIdx] = useState<number | null>(null);   // 拖到的目标行（画指示线）
  const fileRef = useRef<HTMLInputElement>(null);

  // 按作用域加载：全局（跨作品）或预设（仅当前激活预设）
  useEffect(() => {
    setActiveIdx(null);
    const loader = scope === "preset" && hasPreset
      ? getPresetRegex(presetDir!, presetName!)
      : listRegex();
    loader.then((r) => {
      setScripts(r.items || []);
      if ((r.items || []).length) setActiveIdx(0);
    }).catch(() => setScripts([]));
  }, [scope, hasPreset, presetDir, presetName]);

  // 当前卡内嵌正则（随卡落盘，只读展示——编辑请回卡里改）
  useEffect(() => {
    if (!cardName || !characterDir) { setCardScripts([]); return; }
    characterRegex(characterDir, cardName)
      .then((r) => setCardScripts((r.items || []) as unknown as RegexScript[]))
      .catch(() => setCardScripts([]));
  }, [cardName, characterDir]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || confirmDel) return;
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, confirmDel]);

  const active = activeIdx != null ? scripts[activeIdx] : null;

  const patch = (p: Partial<RegexScript>) => {
    if (activeIdx == null) return;
    setScripts((cur) => cur.map((s, i) => (i === activeIdx ? { ...s, ...p } : s)));
    setTestOut(null);
  };

  const add = () => {
    setScripts((cur) => [...cur, blank()]);
    setActiveIdx(scripts.length);
    setTestOut(null);
  };

  const del = () => {
    if (activeIdx == null) return;
    setScripts((cur) => cur.filter((_, i) => i !== activeIdx));
    setActiveIdx(null);
    setConfirmDel(false);
  };

  // 拖拽排序（正则按列表顺序执行，排序影响结果）：把 from 位插到 to 位，同步移动选中项。
  const reorder = (from: number, to: number) => {
    if (from === to || from < 0 || to < 0 || from >= scripts.length || to >= scripts.length) return;
    setScripts((cur) => {
      const next = [...cur];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
    setActiveIdx((cur) => {
      if (cur == null) return cur;
      if (cur === from) return to;
      // 被拖项跨过选中项时，选中项相对位移 ±1
      if (from < cur && to >= cur) return cur - 1;
      if (from > cur && to <= cur) return cur + 1;
      return cur;
    });
    setTestOut(null);
  };

  const persist = async () => {
    setSaving(true);
    try {
      const r = scope === "preset" && hasPreset
        ? await savePresetRegex(presetDir!, presetName!, scripts)
        : await saveRegex(scripts);
      setScripts(r.items || []);
      onClose();
    } finally {
      setSaving(false);
    }
  };

  const runTest = async () => {
    if (!active) return;
    try {
      const r = await testRegex({
        script: active, text: testText,
        placement: active.placement?.[0] ?? Placement.AI_OUTPUT,
        isMarkdown: !!active.markdownOnly, isPrompt: !!active.promptOnly,
      });
      setTestOut(r.result);
    } catch (e) {
      setTestOut(`试跑失败：${(e as Error).message}`);
    }
  };

  const onImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setImportErr(null);
    try {
      const parsed = JSON.parse(await file.text());
      const arr = Array.isArray(parsed) ? parsed : [parsed];
      const imported = arr.map((x) => normalize(x as Record<string, unknown>)).filter(Boolean) as RegexScript[];
      if (!imported.length) { setImportErr("未解析到有效正则（缺少 findRegex）。"); return; }
      setScripts((cur) => {
        const next = [...cur, ...imported];
        setActiveIdx(cur.length); // 选中第一条导入的
        return next;
      });
      setTestOut(null);
    } catch {
      setImportErr("文件不是有效的 JSON。");
    }
  };

  const togglePlacement = (v: number) => {
    if (!active) return;
    const cur = active.placement || [];
    patch({ placement: cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v] });
  };

  return (
    <>
      <div className="modal-mask" onClick={onClose}>
        <div className="modal" style={{ width: 900, maxWidth: "95vw", maxHeight: "90vh", display: "flex", flexDirection: "column" }} onClick={(e) => e.stopPropagation()}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <h3 style={{ margin: 0 }}>正则脚本</h3>
              <div className="seg-tabs" style={{ display: "flex", gap: 4 }}>
                <button className={`btn ${scope === "global" ? "primary" : ""}`} style={{ padding: "3px 10px", fontSize: 12 }}
                  onClick={() => setScope("global")} title="影响所有角色，保存在本地设定中">全局</button>
                <button className={`btn ${scope === "preset" ? "primary" : ""}`} style={{ padding: "3px 10px", fontSize: 12 }}
                  disabled={!hasPreset} onClick={() => setScope("preset")}
                  title={hasPreset ? `只影响当前预设「${presetName}」，保存在预设中` : "未激活预设（在设置里选预设后可用）"}>
                  预设{hasPreset ? `（${presetName}）` : ""}
                </button>
              </div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <input ref={fileRef} type="file" accept=".json" style={{ display: "none" }} onChange={onImport} />
              <button className="btn" onClick={() => fileRef.current?.click()}>
                <Upload size={14} style={{ marginRight: 4 }} /> 导入
              </button>
              <button className="btn" disabled={!scripts.length} onClick={() => downloadJson(scripts, "regex-scripts")} title="导出全部全局正则为 JSON">
                <Download size={14} style={{ marginRight: 4 }} /> 导出
              </button>
              <button className="btn" onClick={onClose}><X size={16} /></button>
            </div>
          </div>
          {importErr && <p style={{ color: "var(--danger, #c0392b)", fontSize: 13, marginTop: 0 }}>{importErr}</p>}
          <div style={{ display: "flex", gap: 14, flex: 1, minHeight: 0 }}>
            {/* 左：脚本列表 */}
            <div style={{ width: 220, flexShrink: 0, display: "flex", flexDirection: "column" }}>
              <button className="btn" onClick={add} style={{ marginBottom: 8 }}>
                <Plus size={14} style={{ marginRight: 4 }} /> 新建正则
              </button>
              <div style={{ overflow: "auto", flex: 1 }}>
                {scripts.length === 0 && <p style={{ color: "var(--text-muted)", fontSize: 12 }}>还没有正则脚本。</p>}
                {scripts.map((s, i) => (
                  <div
                    key={s.id || i}
                    className={`regex-row ${i === activeIdx ? "active" : ""} ${dropIdx === i && dragIdx !== i ? "drop-target" : ""}`}
                    onClick={() => { setActiveIdx(i); setTestOut(null); }}
                    onDragOver={(e) => { e.preventDefault(); if (dropIdx !== i) setDropIdx(i); }}
                    onDrop={(e) => { e.preventDefault(); if (dragIdx != null) reorder(dragIdx, i); setDragIdx(null); setDropIdx(null); }}
                    style={{ opacity: dragIdx === i ? 0.4 : 1 }}
                  >
                    {/* 拖拽手柄：只有它 draggable，避免整行拖动干扰点击/滚动 */}
                    <span
                      className="regex-grip"
                      draggable
                      title="拖动排序（正则按顺序执行）"
                      onClick={(e) => e.stopPropagation()}
                      onDragStart={(e) => { setDragIdx(i); e.dataTransfer.effectAllowed = "move"; }}
                      onDragEnd={() => { setDragIdx(null); setDropIdx(null); }}
                    >
                      <GripVertical size={15} />
                    </span>
                    <span className="regex-name" style={{ opacity: s.disabled ? 0.45 : 1 }}>
                      {s.scriptName || `正则 ${i + 1}`}
                    </span>
                    {/* 启用开关 */}
                    <label className="regex-toggle" title={s.disabled ? "已禁用" : "已启用"} onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" checked={!s.disabled}
                        onChange={(e) => { setActiveIdx(i); setScripts((cur) => cur.map((x, j) => (j === i ? { ...x, disabled: !e.target.checked } : x))); }} />
                      <span className="regex-toggle-track" />
                    </label>
                    <button className="regex-row-del" title="删除此正则"
                      onClick={(e) => { e.stopPropagation(); setActiveIdx(i); setConfirmDel(true); }}>
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
                {cardScripts.length > 0 && (
                  <>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", margin: "10px 0 4px", borderTop: "1px solid var(--border)", paddingTop: 8 }}>
                      当前卡内嵌（{cardName}，只读）
                    </div>
                    {cardScripts.map((s, i) => (
                      <div
                        key={`card-${s.id || i}`}
                        className="nav-item"
                        style={{ width: "100%", marginBottom: 4, textAlign: "left", opacity: s.disabled ? 0.4 : 0.75, cursor: "default", fontSize: 12 }}
                        title="卡内嵌正则随卡落盘，只读展示；如需改请在角色卡里改后重新导入"
                      >
                        📇 {s.scriptName || `正则 ${i + 1}`}
                        <span style={{ fontSize: 10, marginLeft: 4, color: "var(--text-muted)" }}>
                          {s.markdownOnly ? "显示" : s.promptOnly ? "发送" : "存储"}
                        </span>
                      </div>
                    ))}
                  </>
                )}
              </div>
            </div>
            {/* 右：编辑 */}
            <div style={{ flex: 1, minWidth: 0, overflow: "auto" }}>
              {!active && <p style={{ color: "var(--text-muted)" }}>选择或新建一条正则进行编辑。</p>}
              {active && (
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  <div className="field">
                    <label>名称</label>
                    <input value={active.scriptName || ""} onChange={(e) => patch({ scriptName: e.target.value })} />
                  </div>
                  <div className="field">
                    <label>查找（findRegex，支持 /body/flags）</label>
                    <textarea value={active.findRegex} rows={2} placeholder="/<think>[\s\S]*?<\/think>\n?/"
                      style={{ width: "100%", resize: "vertical", fontFamily: "monospace" }}
                      onChange={(e) => patch({ findRegex: e.target.value })} />
                  </div>
                  <div className="field">
                    <label>替换（replaceString，$1 / $&lt;name&gt; / {"{{match}}"}，可换行）</label>
                    <textarea value={active.replaceString || ""} rows={2} placeholder="留空=删除匹配；可直接输入换行"
                      style={{ width: "100%", resize: "vertical", fontFamily: "monospace" }}
                      onChange={(e) => patch({ replaceString: e.target.value })} />
                  </div>
                  <div className="field">
                    <label>去除串（trimStrings，逗号分隔）</label>
                    <input value={(active.trimStrings || []).join(",")} onChange={(e) => patch({ trimStrings: e.target.value.split(",").map((t) => t.trim()).filter(Boolean) })} />
                  </div>
                  <div className="field">
                    <label>作用范围（placement）</label>
                    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                      {PLACEMENT_OPTS.map((o) => (
                        <label key={o.value} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 13 }}>
                          <input type="checkbox" checked={(active.placement || []).includes(o.value)} onChange={() => togglePlacement(o.value)} />
                          {o.label}
                        </label>
                      ))}
                    </div>
                  </div>
                  <div className="field-row" style={{ display: "flex", gap: 10 }}>
                    <div className="field" style={{ flex: 1 }}>
                      <label>最小深度（留空=不限）</label>
                      <input type="number" value={active.minDepth ?? ""} onChange={(e) => patch({ minDepth: e.target.value === "" ? null : Number(e.target.value) })} />
                    </div>
                    <div className="field" style={{ flex: 1 }}>
                      <label>最大深度（留空=不限）</label>
                      <input type="number" value={active.maxDepth ?? ""} onChange={(e) => patch({ maxDepth: e.target.value === "" ? null : Number(e.target.value) })} />
                    </div>
                  </div>
                  {/* 其他选项（对标 ST）：已禁用 / 在编辑时运行 / 查找时的宏 */}
                  <div className="regex-opt-group">
                    <div className="regex-opt-title">其他选项</div>
                    <label className="regex-opt">
                      <input type="checkbox" checked={!!active.disabled} onChange={(e) => patch({ disabled: e.target.checked })} />
                      <span>已禁用</span>
                    </label>
                    <label className="regex-opt">
                      <input type="checkbox" checked={active.runOnEdit !== false} onChange={(e) => patch({ runOnEdit: e.target.checked })} />
                      <span>在编辑时运行</span>
                    </label>
                    <div className="regex-opt-sub">正则表达式查找时的宏</div>
                    <select value={active.substituteRegex ?? 0} onChange={(e) => patch({ substituteRegex: Number(e.target.value) })}
                      style={{ width: "100%" }}>
                      <option value={0}>不替换</option>
                      <option value={1}>原始（替换为宏的原始值）</option>
                      <option value={2}>转义（替换为宏的转义值）</option>
                    </select>
                  </div>
                  {/* 短暂（对标 ST）：仅格式显示 markdownOnly / 仅格式提示词 promptOnly */}
                  <div className="regex-opt-group">
                    <div className="regex-opt-title">短暂</div>
                    <label className="regex-opt">
                      <input type="checkbox" checked={!!active.markdownOnly}
                        onChange={(e) => patch({ markdownOnly: e.target.checked })} />
                      <span>仅格式显示</span>
                    </label>
                    <label className="regex-opt">
                      <input type="checkbox" checked={!!active.promptOnly}
                        onChange={(e) => patch({ promptOnly: e.target.checked })} />
                      <span>仅格式提示词</span>
                    </label>
                    <p className="regex-opt-hint">
                      都不勾=改存储源（落库正文）；仅显示=只改渲染；仅提示词=只改发送给 AI 的内容；两者可同时勾选（显示+提示词都改，不落库）。
                    </p>
                  </div>

                  <div className="field">
                    <label>试跑（输入样例文本，看替换结果）</label>
                    <textarea value={testText} onChange={(e) => setTestText(e.target.value)} rows={3} style={{ width: "100%", resize: "vertical" }} />
                    <button className="btn" onClick={runTest} style={{ marginTop: 6 }}>试跑</button>
                    {testOut != null && (
                      <div className="chat-box" style={{ marginTop: 8, whiteSpace: "pre-wrap", fontSize: 13 }}>{testOut || "（空）"}</div>
                    )}
                  </div>

                  <div>
                    <button className="btn danger" onClick={() => setConfirmDel(true)}>
                      <Trash2 size={14} style={{ marginRight: 4 }} /> 删除此正则
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
          <div className="modal-actions" style={{ marginTop: 12 }}>
            <button className="btn" onClick={onClose}>取消</button>
            <button className="btn primary" disabled={saving} onClick={persist}>{saving ? "保存中…" : "保存"}</button>
          </div>
        </div>
      </div>
      {confirmDel && (
        <ConfirmModal title="删除正则" message={`确定删除「${active?.scriptName || ""}」？`} confirmText="删除" danger onConfirm={del} onCancel={() => setConfirmDel(false)} />
      )}
    </>
  );
}
