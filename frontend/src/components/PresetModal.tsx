import { useEffect, useRef, useState } from "react";
import { X, Upload, Download, Trash2, Check, GripVertical } from "lucide-react";
import { ConfirmModal } from "./Modal";
import { downloadJson } from "../lib/download";
import {
  listPresets, importPreset, presetDetail, savePreset, deletePreset,
  type PresetSummary, type PresetData, type PresetPrompt, type ThinkingChain, type PresetConflict,
} from "../api/preset";

// 偏置预设管理（仅剧情模式）：导入 ST 预设、选激活、查看片段列表 + 手动开关（NSFW 规则 AI 易误解，保持手动）。
// 片段开关沿用 ST prompt_order.enabled；marker 片段标注、不可编辑内容（由卡字段/世界书填充）。

export function PresetModal({
  base, activeName, onSelectActive, onClose,
}: {
  base: string;
  activeName: string;
  onSelectActive: (name: string) => void;
  onClose: () => void;
}) {
  const [list, setList] = useState<PresetSummary[]>([]);
  const [selName, setSelName] = useState<string | null>(null);
  const [detail, setDetail] = useState<PresetData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState<{ file: File; conflict: PresetConflict } | null>(null);
  const [confirmDel, setConfirmDel] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dragIdx, setDragIdx] = useState<number | null>(null);   // 拖拽中的源下标
  const [overIdx, setOverIdx] = useState<number | null>(null);   // 悬停目标下标（画插入线）
  const fileRef = useRef<HTMLInputElement>(null);

  const reload = () => {
    if (!base) { setList([]); return; }
    listPresets(base).then((r) => setList(r.items)).catch((e) => setErr(String(e.message || e)));
  };
  useEffect(reload, [base]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !confirmDel && !pending) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, confirmDel, pending]);

  const open = (name: string) => {
    setSelName(name);
    presetDetail(base, name).then((r) => setDetail(r.preset)).catch((e) => setErr(String(e.message || e)));
  };

  const doImport = async (file: File, overwrite: boolean) => {
    setErr(null); setBusy(true);
    try {
      await importPreset(file, base, overwrite);
      setPending(null);
      reload();
    } catch (e) {
      const conflict = (e as { conflict?: PresetConflict }).conflict;
      if (conflict) setPending({ file, conflict });
      else setErr(String((e as Error).message || e));
    } finally { setBusy(false); }
  };

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) doImport(file, false);
    e.target.value = "";
  };

  // 片段开关：改 detail 里对应 order.enabled，保存回后端
  const orderList = detail?.prompt_order?.[0]?.order || [];
  const promptsById = new Map((detail?.prompts || []).map((p) => [p.identifier, p]));

  const toggle = (identifier: string) => {
    if (!detail) return;
    const next: PresetData = {
      ...detail,
      prompt_order: detail.prompt_order.map((po, i) =>
        i === 0 ? { ...po, order: po.order.map((o) => o.identifier === identifier ? { ...o, enabled: !o.enabled } : o) } : po),
    };
    setDetail(next);
  };

  // 拖拽排序：把 order[from] 移到 order[to]（改 prompt_order[0].order 的顺序，保存后落盘）。
  // 顺序直接决定 system 头拼接次序 + 相对 chatHistory 的位置 → 影响产出遵守度，故开放手动调序。
  const reorder = (from: number, to: number) => {
    if (!detail || from === to) return;
    const order = [...(detail.prompt_order[0]?.order || [])];
    const [moved] = order.splice(from, 1);
    order.splice(to, 0, moved);
    setDetail({
      ...detail,
      prompt_order: detail.prompt_order.map((po, i) => (i === 0 ? { ...po, order } : po)),
    });
  };

  // 编辑片段字段（名称/角色/内容/位置/触发器）：改 detail.prompts 里对应项，保存回后端落盘
  const updatePrompt = (identifier: string, patch: Partial<PresetPrompt>) => {
    if (!detail) return;
    setDetail({
      ...detail,
      prompts: detail.prompts.map((p) => (p.identifier === identifier ? { ...p, ...patch } : p)),
    });
  };

  // 思维链 CRUD：按真状态条件注入的推理链，驱动剧情推进质量（比 ST 变量字符串宏更准）。
  const chains = detail?.thinking_chains || [];
  const setChains = (next: ThinkingChain[]) => detail && setDetail({ ...detail, thinking_chains: next });
  const addChain = () => setChains([...chains, { name: "新思维链", content: "", position: "tail", when: {} }]);
  const updateChain = (i: number, patch: Partial<ThinkingChain>) =>
    setChains(chains.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const updateChainWhen = (i: number, patch: Partial<NonNullable<ThinkingChain["when"]>>) =>
    setChains(chains.map((c, j) => (j === i ? { ...c, when: { ...c.when, ...patch } } : c)));
  const removeChain = (i: number) => setChains(chains.filter((_, j) => j !== i));

  const persist = async () => {
    if (!detail || !selName) return;
    setBusy(true);
    try { await savePreset(base, selName, detail); reload(); }
    finally { setBusy(false); }
  };

  const del = () => {
    if (!selName) return;
    deletePreset(base, selName).then(() => {
      if (activeName === selName) onSelectActive("");
      setSelName(null); setDetail(null); setConfirmDel(false); reload();
    }).catch((e) => setErr(String(e.message || e)));
  };

  return (
    <>
      <div className="modal-mask" onClick={onClose}>
        <div className="modal" style={{ width: 960, maxWidth: "96vw", maxHeight: "90vh", display: "flex", flexDirection: "column" }} onClick={(e) => e.stopPropagation()}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>偏置预设</h3>
            <div style={{ display: "flex", gap: 8 }}>
              <input ref={fileRef} type="file" accept=".json" style={{ display: "none" }} onChange={onPick} />
              <button className="btn" disabled={!base} onClick={() => fileRef.current?.click()}>
                <Upload size={14} style={{ marginRight: 4 }} /> 导入
              </button>
              <button className="btn" onClick={onClose}><X size={16} /></button>
            </div>
          </div>
          {!base && <p style={{ color: "var(--text-muted)" }}>请先到「设置 → 路径 → 偏置预设文件夹」设置目录。</p>}
          {err && <p style={{ color: "var(--danger, #c0392b)" }}>{err}</p>}
          <div style={{ display: "flex", gap: 14, flex: 1, minHeight: 0 }}>
            {/* 左：预设列表 + 激活选择 */}
            <div style={{ width: 240, flexShrink: 0, overflow: "auto" }}>
              <button
                className={`nav-item ${activeName === "" ? "active" : ""}`}
                style={{ width: "100%", marginBottom: 6, textAlign: "left" }}
                onClick={() => onSelectActive("")}
              >
                {activeName === "" && <Check size={13} style={{ marginRight: 4 }} />} 不使用预设（内置扮演）
              </button>
              {list.map((p) => (
                <div key={p.file} style={{ display: "flex", alignItems: "center", marginBottom: 4 }}>
                  <button
                    className={`nav-item ${activeName === p.name ? "active" : ""}`}
                    style={{
                      flex: 1, minWidth: 0, textAlign: "left",
                      // 深色=已激活（单一来源）；正在查看的用描边区分，不再抢“激活”高亮
                      outline: selName === p.name ? "2px solid var(--accent)" : "none",
                      outlineOffset: -2,
                    }}
                    onClick={() => open(p.name)}
                  >
                    {activeName === p.name && <Check size={13} style={{ marginRight: 4, color: "var(--accent)" }} />}
                    {p.name}
                    <span style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: 6 }}>{p.enabled}/{p.prompts}</span>
                  </button>
                </div>
              ))}
            </div>
            {/* 右：片段列表 + 开关 */}
            <div style={{ flex: 1, minWidth: 0, overflow: "auto" }}>
              {!detail && <p style={{ color: "var(--text-muted)" }}>选择左侧预设查看/编辑片段；可改名称、角色、内容并勾选启用/停用，拖左上角把手调顺序（越靠后越接近生成点、遵守越强；排到「聊天历史」占位之后遵守最严），改完点「保存修改」落盘（占位片段由卡字段/世界书自动填充，不可编辑内容）。</p>}
              {detail && (
                <>
                  <div className="preset-action-bar">
                    <button className="btn primary" disabled={busy} onClick={() => selName && onSelectActive(selName)}>
                      设为激活
                    </button>
                    <button className="btn" disabled={busy} onClick={persist}>保存修改</button>
                    <button className="btn" onClick={() => selName && detail && downloadJson(detail, selName)}>
                      <Download size={14} style={{ marginRight: 4 }} /> 导出
                    </button>
                    <button className="btn danger" onClick={() => setConfirmDel(true)}>
                      <Trash2 size={14} style={{ marginRight: 4 }} /> 删除
                    </button>
                  </div>
                  {orderList.map((o, idx) => {
                    const p = promptsById.get(o.identifier);
                    if (!p) return null;
                    const isMarker = !!p.marker;
                    return (
                      <div
                        key={o.identifier}
                        className="chat-box"
                        draggable={dragIdx === idx}
                        onDragOver={(e) => { e.preventDefault(); if (overIdx !== idx) setOverIdx(idx); }}
                        onDrop={(e) => { e.preventDefault(); if (dragIdx !== null) reorder(dragIdx, idx); setDragIdx(null); setOverIdx(null); }}
                        onDragEnd={() => { setDragIdx(null); setOverIdx(null); }}
                        style={{
                          marginBottom: 8,
                          opacity: dragIdx === idx ? 0.4 : (o.enabled ? 1 : 0.55),
                          borderTop: overIdx === idx && dragIdx !== null && dragIdx !== idx ? "2px solid var(--accent)" : undefined,
                        }}
                      >
                        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                          {/* 左上角拖拽把手：按住它才能拖（draggable 只在按把手时开，避免影响输入框选字） */}
                          <span
                            title="拖拽调整顺序"
                            onMouseDown={() => setDragIdx(idx)}
                            onMouseUp={() => { if (overIdx === null) setDragIdx(null); }}
                            style={{ cursor: "grab", display: "inline-flex", color: "var(--text-muted)", flexShrink: 0, touchAction: "none" }}
                          >
                            <GripVertical size={16} />
                          </span>
                          <label style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--text-muted)", flexShrink: 0, cursor: "pointer" }}>
                            <input type="checkbox" checked={o.enabled} onChange={() => toggle(o.identifier)} /> 启用
                          </label>
                          {isMarker ? (
                            <>
                              <span style={{ fontWeight: 600, fontSize: 13, flex: 1, minWidth: 0 }}>{p.name || o.identifier}</span>
                              <span style={{ fontSize: 11, color: "var(--accent)", flexShrink: 0 }}>占位（自动填充）</span>
                            </>
                          ) : (
                            <>
                              <input
                                value={p.name || ""}
                                placeholder="片段名称"
                                onChange={(e) => updatePrompt(o.identifier, { name: e.target.value })}
                                style={{ flex: 1, minWidth: 0, fontWeight: 600, fontSize: 13, padding: "4px 8px", border: "1px solid var(--border)", borderRadius: 6 }}
                              />
                              <select
                                value={p.role || "system"}
                                onChange={(e) => updatePrompt(o.identifier, { role: e.target.value })}
                                style={{ fontSize: 12, padding: "4px 6px", width: 110, flexShrink: 0, border: "1px solid var(--border)", borderRadius: 6 }}
                              >
                                <option value="system">system</option>
                                <option value="user">user</option>
                                <option value="assistant">assistant</option>
                              </select>
                            </>
                          )}
                        </div>
                        {!isMarker && (
                          <>
                            <textarea
                              value={p.content || ""}
                              placeholder="片段内容（system/user/assistant 文本，支持 {{char}}/{{user}} 宏）"
                              onChange={(e) => updatePrompt(o.identifier, { content: e.target.value })}
                              rows={4}
                              style={{ width: "100%", marginTop: 8, fontSize: 12, padding: "6px 8px", border: "1px solid var(--border)", borderRadius: 6, resize: "vertical", whiteSpace: "pre-wrap", boxSizing: "border-box" }}
                            />
                            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6, flexWrap: "wrap" }}>
                              <label style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
                                位置
                                <select
                                  value={p.injection_position ?? 0}
                                  onChange={(e) => updatePrompt(o.identifier, { injection_position: Number(e.target.value) })}
                                  style={{ fontSize: 11, padding: "3px 5px", border: "1px solid var(--border)", borderRadius: 5 }}
                                >
                                  <option value={0}>相对（按顺序）</option>
                                  <option value={1}>聊天内 @深度</option>
                                </select>
                              </label>
                              {p.injection_position === 1 && (
                                <label style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
                                  深度
                                  <input
                                    type="number" min={0}
                                    value={p.injection_depth ?? 4}
                                    onChange={(e) => updatePrompt(o.identifier, { injection_depth: Number(e.target.value) })}
                                    style={{ width: 60, fontSize: 11, padding: "3px 5px", border: "1px solid var(--border)", borderRadius: 5 }}
                                  />
                                </label>
                              )}
                              <span style={{ fontSize: 10, color: "var(--text-muted)", opacity: 0.8 }}>
                                （位置/触发器当前保留但折叠注入，见提示）
                              </span>
                            </div>
                          </>
                        )}
                      </div>
                    );
                  })}
                  {/* 思维链编辑区：按真状态条件注入的推理链，驱动剧情推进质量 */}
                  <div style={{ marginTop: 16, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                      <span style={{ fontWeight: 600, fontSize: 13 }}>思维链（条件推理链）</span>
                      <button className="btn" onClick={addChain}>+ 新增链</button>
                    </div>
                    <p style={{ fontSize: 11, color: "var(--text-muted)", margin: "0 0 8px" }}>
                      按真状态条件命中则注入：tail=落历史后（遵守最严），head=随 system 头（框定框架）。条件留空=每轮都挂。
                    </p>
                    {chains.map((c, i) => (
                      <div key={i} className="chat-box" style={{ marginBottom: 8 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                          <input
                            value={c.name || ""} placeholder="链名称"
                            onChange={(e) => updateChain(i, { name: e.target.value })}
                            style={{ flex: 1, minWidth: 0, fontSize: 12, padding: "4px 6px", border: "1px solid var(--border)", borderRadius: 5 }}
                          />
                          <select
                            value={c.position || "tail"}
                            onChange={(e) => updateChain(i, { position: e.target.value as "tail" | "head" })}
                            style={{ fontSize: 11, padding: "4px 5px", border: "1px solid var(--border)", borderRadius: 5 }}
                          >
                            <option value="tail">尾部（遵守最严）</option>
                            <option value="head">头部（框定框架）</option>
                          </select>
                          <button className="btn danger" title="删除此链" onClick={() => removeChain(i)}><Trash2 size={13} /></button>
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
                          <label style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
                            场景
                            <select
                              value={c.when?.scene || ""}
                              onChange={(e) => updateChainWhen(i, { scene: e.target.value || undefined })}
                              style={{ fontSize: 11, padding: "3px 5px", border: "1px solid var(--border)", borderRadius: 5 }}
                            >
                              <option value="">不限</option>
                              <option value="dialogue">对话</option>
                              <option value="action">动作</option>
                              <option value="emotion">情感</option>
                              <option value="conflict">冲突</option>
                              <option value="nsfw">NSFW</option>
                              <option value="climax">高潮</option>
                            </select>
                          </label>
                          <label style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
                            好感&lt;
                            <input type="number" value={c.when?.affinity_lt ?? ""} placeholder="—"
                              onChange={(e) => updateChainWhen(i, { affinity_lt: e.target.value === "" ? undefined : Number(e.target.value) })}
                              style={{ width: 52, fontSize: 11, padding: "3px 5px", border: "1px solid var(--border)", borderRadius: 5 }}
                            />
                          </label>
                          <label style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
                            好感&gt;
                            <input type="number" value={c.when?.affinity_gt ?? ""} placeholder="—"
                              onChange={(e) => updateChainWhen(i, { affinity_gt: e.target.value === "" ? undefined : Number(e.target.value) })}
                              style={{ width: 52, fontSize: 11, padding: "3px 5px", border: "1px solid var(--border)", borderRadius: 5 }}
                            />
                          </label>
                        </div>
                        <textarea
                          value={c.content || ""} placeholder="推理链内容：先想什么、再想什么……（引导 AI 落笔前的推理）"
                          onChange={(e) => updateChain(i, { content: e.target.value })}
                          rows={3}
                          style={{ width: "100%", fontSize: 12, padding: "6px 8px", border: "1px solid var(--border)", borderRadius: 6, resize: "vertical", whiteSpace: "pre-wrap", boxSizing: "border-box" }}
                        />
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
      {pending && (
        <ConfirmModal
          title="预设已存在" message={`已存在同名预设「${pending.conflict.name}」，覆盖将替换原内容。是否覆盖？`}
          confirmText="覆盖" danger busy={busy}
          onConfirm={() => doImport(pending.file, true)} onCancel={() => setPending(null)}
        />
      )}
      {confirmDel && (
        <ConfirmModal title="删除预设" message={`确定删除「${selName}」？`} confirmText="删除" danger
          onConfirm={del} onCancel={() => setConfirmDel(false)} />
      )}
    </>
  );
}
