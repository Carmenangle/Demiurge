import { useEffect, useMemo, useState } from "react";
import { Save, Trash2 } from "lucide-react";
import {
  createPromptTemplate, deletePromptTemplate,
  IMAGE_KINDS, KIND_LABELS, SECTION_KEY_LABELS, SECTION_KEYS, updatePromptTemplate, VIDEO_KINDS,
  type PromptTemplate, type PromptTemplateKind,
} from "../api/ai/promptTemplates";
import {
  SYSTEM_CHAR_LIMIT, combinedSystemChars, emptyForm, formFromTemplate, formToBody,
  templateIsStale, templateKindGroup,
  type TemplateForm, type TemplateGroup,
} from "../lib/promptTemplates";
import { ConfirmModal } from "./Modal";
import { useMobileViewport } from "../lib/useMobileViewport";

interface Props {
  builtins: PromptTemplate[];
  customs: PromptTemplate[];
  onChanged: () => void | Promise<void>;   // CRUD 后由父组件重拉列表
  onClose: () => void;
  /** 打开时定位到某分组（设置页「生图/视频提示词模版」各自入口用） */
  initialGroup?: TemplateGroup;
  /** 打开即编辑这条（自定义模版） */
  initialTemplateId?: string;
  /** 打开即新建（该分组）：先选协议族，再在克隆种子上编辑 */
  createForGroup?: TemplateGroup;
}

const KIND_GROUP_LABEL: Record<TemplateGroup, string> = {
  image: "生图提示词模版",
  video: "视频提示词模版",
};

const GROUP_KINDS: Record<TemplateGroup, readonly PromptTemplateKind[]> = {
  image: IMAGE_KINDS,
  video: VIDEO_KINDS,
};

// 提示词模版工作台：内置默认模版只读（可克隆），我的模版可编辑/删除。
// 协议族（校验/归一化/兜底）内置不可改——本 UI 只开放数据层字段。
export function PromptTemplateModal({
  builtins, customs, onChanged, onClose,
  initialGroup = "image", initialTemplateId = "", createForGroup,
}: Props) {
  const [group, setGroup] = useState<TemplateGroup>(createForGroup || initialGroup);
  const [activeId, setActiveId] = useState<string | null>(() => {
    if (initialTemplateId) return initialTemplateId;
    const first = builtins.find((t) => templateKindGroup(t.kind) === (createForGroup || initialGroup));
    return first?.id ?? null;
  });
  const [form, setForm] = useState<TemplateForm | null>(() => {
    if (initialTemplateId) {
      const t = customs.find((item) => item.id === initialTemplateId);
      return t ? formFromTemplate(t) : null;
    }
    if (createForGroup) {
      const kind = GROUP_KINDS[createForGroup][0];
      const seed = builtins.find((b) => b.kind === kind);
      return { ...emptyForm(kind, seed), name: seed ? `${seed.name}·我的` : "" };
    }
    return null;
  });
  const [creatingKind, setCreatingKind] = useState<PromptTemplateKind | null>(() => (
    createForGroup ? GROUP_KINDS[createForGroup][0] : null
  ));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmUnlock, setConfirmUnlock] = useState(false);
  const isMobileViewport = useMobileViewport();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (confirmDelete || confirmUnlock) return;
      if (form) { setForm(null); setCreatingKind(null); return; }
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, form, confirmDelete, confirmUnlock]);

  const groupBuiltins = useMemo(
    () => builtins.filter((t) => templateKindGroup(t.kind) === group), [builtins, group]);
  const groupCustoms = useMemo(
    () => customs.filter((t) => templateKindGroup(t.kind) === group), [customs, group]);
  const active = useMemo(() => {
    if (!activeId) return null;
    return [...groupBuiltins, ...groupCustoms].find((t) => t.id === activeId) || null;
  }, [activeId, groupBuiltins, groupCustoms]);

  // 切换分组时默认选中第一个内置（让右侧不空）
  useEffect(() => {
    if (form) return;   // 编辑/新建中不打断
    if (active && templateKindGroup(active.kind) === group) return;
    setActiveId(groupBuiltins[0]?.id ?? groupCustoms[0]?.id ?? null);
    setError("");
  }, [group, groupBuiltins, groupCustoms, active, form]);

  const startEdit = (t: PromptTemplate) => {
    setCreatingKind(null);
    setActiveId(t.id);
    setForm(formFromTemplate(t));
  };

  // 「新增模版」= 在所选协议族的内置全文上起一份（红线锚文本天然齐全）
  const startCreate = (kind: PromptTemplateKind) => {
    const seed = builtins.find((b) => b.kind === kind);
    setCreatingKind(kind);
    setActiveId(seed?.id ?? null);
    setForm({ ...emptyForm(kind, seed), name: seed ? `${seed.name}·我的` : "" });
    setError("");
  };

  const startClone = async (t: PromptTemplate) => {
    setBusy(true);
    setError("");
    try {
      const created = await createPromptTemplate({
        kind: t.kind, name: `${t.name}·我的`, base_id: t.id,
      });
      await onChanged();
      setActiveId(created.id);
      setCreatingKind(null);
      setForm(formFromTemplate(created));
    } catch (e) {
      setError(e instanceof Error ? e.message : "克隆失败");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!form) return;
    if (!form.name.trim()) { setError("名称不能为空"); return; }
    if (templateKindGroup(form.kind) === "image"
        && combinedSystemChars(form) > SYSTEM_CHAR_LIMIT) {
      setError(`指令文本过长：system_text + extra_instructions 合计 ≤${SYSTEM_CHAR_LIMIT} 字符`);
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (creatingKind) {
        const seed = builtins.find((b) => b.kind === creatingKind);
        const created = await createPromptTemplate({
          ...formToBody(form), base_id: seed?.id || "",
        });
        await onChanged();
        setActiveId(created.id);
      } else if (active && !active.builtin) {
        await updatePromptTemplate(active.id, formToBody(form));
        await onChanged();
      } else {
        const created = await createPromptTemplate(formToBody(form));
        await onChanged();
        setActiveId(created.id);
      }
      setCreatingKind(null);
      setForm(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!active || active.builtin) return;
    setBusy(true);
    try {
      await deletePromptTemplate(active.id);
      await onChanged();
      setActiveId(groupBuiltins[0]?.id ?? null);
      setForm(null);
      setConfirmDelete(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    } finally {
      setBusy(false);
    }
  };

  const patch = (p: Partial<TemplateForm>) => setForm((f) => (f ? { ...f, ...p } : f));

  return (
    <>
    <div className="modal-mask">
      <div className="modal" style={{ width: 920, maxWidth: "95vw", maxHeight: "90vh", display: "flex", flexDirection: "column" }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-split-head">
          <h3 style={{ margin: 0 }}>提示词模版</h3>
          <button className="icon-btn" style={{ background: "transparent", color: "var(--text)" }} onClick={onClose} aria-label="关闭">×</button>
        </div>
        <p style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 0 }}>
          内置默认模版只读，点「新增模版」在它上面起一份再改。可定义：指令文本、追加段、质量/负面词、
          视频区块标题与顺序、运镜文案、节奏名与默认时长；输出结构、校验与兜底逻辑由内置协议族决定，不可改。
        </p>
        <div className="lora-mode-switch" role="group" aria-label="模版分组" style={{ marginBottom: 10 }}>
          {(["image", "video"] as const).map((g) => (
            <button
              key={g}
              type="button"
              className={`btn${group === g ? " primary" : ""}`}
              aria-pressed={group === g}
              onClick={() => { setGroup(g); setCreatingKind(null); setForm(null); }}
            >{KIND_GROUP_LABEL[g]}</button>
          ))}
        </div>
        <div className="modal-split-body">
          {/* 左：列表（内置组 + 我的组） */}
          <div className="modal-split-list">
            <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "4px 0" }}>内置默认（只读）</div>
            {groupBuiltins.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`model-switch-item ${t.id === activeId ? "active" : ""}`}
                style={{ textAlign: "left", justifyContent: "flex-start" }}
                onClick={() => { setActiveId(t.id); setCreatingKind(null); setForm(null); setError(""); }}
              >{KIND_LABELS[t.kind]}</button>
            ))}
            <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "10px 0 4px" }}>我的模版</div>
            {groupCustoms.length === 0 && (
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>还没有自定义模版</div>
            )}
            {groupCustoms.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`model-switch-item ${t.id === activeId ? "active" : ""}`}
                style={{ textAlign: "left", justifyContent: "flex-start" }}
                onClick={() => { setActiveId(t.id); setCreatingKind(null); setForm(null); setError(""); }}
              >
                {t.name || "未命名模版"}
                {templateIsStale(t, builtins) && <span style={{ color: "#c98a1a", marginLeft: 6 }}>有更新</span>}
              </button>
            ))}
          </div>

          {/* 右：详情 / 编辑器 */}
          <div className="modal-split-editor" style={{ display: "flex", flexDirection: "column", gap: 8, overflowY: "auto" }}>
            {!active && !form && <div style={{ color: "var(--text-muted)", fontSize: 13, margin: "auto" }}>选择{isMobileViewport ? "上方" : "左侧"}模版查看</div>}

            {!form && (
              <div style={{ display: "flex", gap: 8, marginBottom: 4 }}>
                <span style={{ flex: 1, fontSize: 12, color: "var(--text-muted)", alignSelf: "center" }}>
                  新增：在所选协议族的内置全文上起一份
                </span>
                {GROUP_KINDS[group].map((kind) => (
                  <button key={kind} type="button" className="btn" disabled={busy} onClick={() => startCreate(kind)}>
                    ＋ {KIND_LABELS[kind].split("（")[0]}
                  </button>
                ))}
              </div>
            )}

            {active && !form && (
              <>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <strong style={{ flex: 1 }}>{active.builtin ? KIND_LABELS[active.kind] : active.name}</strong>
                  <button className="btn" disabled={busy} onClick={() => startClone(active)}>
                    克隆这份，改成我的
                  </button>
                  {!active.builtin && (
                    <>
                      <button className="btn" onClick={() => startEdit(active)}>编辑</button>
                      <button className="btn danger" onClick={() => setConfirmDelete(true)}>
                        <Trash2 size={14} />
                      </button>
                    </>
                  )}
                </div>
                {active.builtin ? (
                  <BuiltinDetail tpl={active} />
                ) : (
                  <CustomSummary tpl={active} builtins={builtins} />
                )}
              </>
            )}

            {form && (
              <>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <strong style={{ flex: 1 }}>
                    {creatingKind ? "新增模版" : "编辑"}：{form.name || "未命名模版"}
                  </strong>
                  <button className="btn primary" disabled={busy} onClick={save}>
                    <Save size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />保存
                  </button>
                  <button className="btn" onClick={() => { setForm(null); setCreatingKind(null); }}>取消</button>
                </div>
                {creatingKind && (
                  <div className="lora-mode-switch" role="group" aria-label="协议族" style={{ marginBottom: 4 }}>
                    {GROUP_KINDS[group].map((kind) => (
                      <button
                        key={kind}
                        type="button"
                        className={`btn${form.kind === kind ? " primary" : ""}`}
                        aria-pressed={form.kind === kind}
                        onClick={() => startCreate(kind)}
                      >{KIND_LABELS[kind].split("（")[0]}</button>
                    ))}
                  </div>
                )}
                <label style={{ display: "block", fontSize: 13 }}>
                  名称
                  <input value={form.name} onChange={(e) => patch({ name: e.target.value })} style={{ width: "100%" }} />
                </label>
                {templateKindGroup(form.kind) === "image" ? (
                  <ImageForm form={form} patch={patch} />
                ) : (
                  <VideoForm form={form} patch={patch} />
                )}
                <RedlineLock form={form} patch={patch} onRequestUnlock={() => setConfirmUnlock(true)} />
              </>
            )}

            {error && <p style={{ color: "#c0392b", fontSize: 12 }}>{error}</p>}
          </div>
        </div>
      </div>
    </div>
    {confirmDelete && active && (
      <ConfirmModal
        title="删除模版"
        message={`确认删除「${active.name || "未命名模版"}」？已选它的作品预设会降级到对应内置模版，不会报错。`}
        confirmText="删除"
        danger
        onConfirm={remove}
        onCancel={() => setConfirmDelete(false)}
      />
    )}
    {confirmUnlock && (
      <ConfirmModal
        title="关闭红线条款锁"
        message="关闭后，指令文本里可以删改「多人分述 / 纯英文覆盖 / 事实边界 / 画面自洽」四段必保条款。这些条款来自真机事故固化，删改可能让成稿出现多人归属错乱、中文残留、场景幻觉等问题。确定关闭？"
        confirmText="仍要关闭"
        danger
        onConfirm={() => { patch({ redline_lock: false }); setConfirmUnlock(false); }}
        onCancel={() => setConfirmUnlock(false)}
      />
    )}
    </>
  );
}

// ── 内置详情（只读） ─────────────────────────────────────────────────────────

function BuiltinDetail({ tpl }: { tpl: PromptTemplate }) {
  return (
    <>
      <p className="bind-hint" style={{ marginTop: 0 }}>
        这是内置默认模版（内容哈希 {tpl.content_hash}）。生图/视频编译时若没选自定义模版，就用它。
      </p>
      {templateKindGroup(tpl.kind) === "image" ? (
        <>
          <div style={{ fontSize: 13 }}>输出结构：{KIND_LABELS[tpl.kind]}</div>
          <label style={{ display: "block", fontSize: 13 }}>
            生成指令全文（system，只读）
            <textarea value={tpl.system_text} readOnly rows={16} style={{ width: "100%", resize: "vertical" }} />
          </label>
          {tpl.kind === "anima_tags" && (
            <>
              <label style={{ display: "block", fontSize: 13 }}>
                固定质量词
                <textarea value={tpl.quality_tags} readOnly rows={3} style={{ width: "100%" }} />
              </label>
              <label style={{ display: "block", fontSize: 13 }}>
                固定负面词
                <textarea value={tpl.negative_tags} readOnly rows={4} style={{ width: "100%" }} />
              </label>
            </>
          )}
        </>
      ) : (
        <TemplateStructureView tpl={tpl} />
      )}
    </>
  );
}

function CustomSummary({ tpl, builtins }: { tpl: PromptTemplate; builtins: PromptTemplate[] }) {
  const stale = templateIsStale(tpl, builtins);
  return (
    <>
      <p className="bind-hint" style={{ marginTop: 0 }}>
        我的模版 · {KIND_LABELS[tpl.kind]}
        {tpl.base_id && <>（克隆自 {tpl.base_id}，基准哈希 {tpl.base_hash || "无"}）</>}
      </p>
      {stale && (
        <p style={{ color: "#c98a1a", fontSize: 12 }}>
          内置默认模版已更新（基准哈希对不上）。点「编辑」对照新版调整，或删除后重新克隆。
        </p>
      )}
      {tpl.redline_lock === false && (
        <p style={{ color: "#c98a1a", fontSize: 12 }}>
          红线条款锁已关闭（{tpl.redline_lock_off_at ? new Date(tpl.redline_lock_off_at * 1000).toLocaleString() : "时间未知"}）——必保条款可被删改，请注意成稿质量。
        </p>
      )}
      {templateKindGroup(tpl.kind) === "image" ? (
        <>
          <label style={{ display: "block", fontSize: 13 }}>
            生成指令全文（system）
            <textarea value={tpl.system_text} readOnly rows={10} style={{ width: "100%" }} />
          </label>
          <label style={{ display: "block", fontSize: 13 }}>
            追加指令（拼在 system 后）
            <textarea value={tpl.extra_instructions} readOnly rows={5} style={{ width: "100%" }} />
          </label>
          {tpl.kind === "anima_tags" && (
            <>
              <label style={{ display: "block", fontSize: 13 }}>
                固定质量词
                <textarea value={tpl.quality_tags} readOnly rows={2} style={{ width: "100%" }} />
              </label>
              <label style={{ display: "block", fontSize: 13 }}>
                固定负面词
                <textarea value={tpl.negative_tags} readOnly rows={3} style={{ width: "100%" }} />
              </label>
            </>
          )}
        </>
      ) : (
        <TemplateStructureView tpl={tpl} />
      )}
    </>
  );
}

/** 视频模版结构预览（内置/自定义共用；设置页也直接展示这份）。 */
export function TemplateStructureView({ tpl }: { tpl: PromptTemplate }) {
  const order = tpl.section_order?.length ? tpl.section_order : [...SECTION_KEYS];
  const bands = Object.entries(tpl.camera_band_overrides || {});
  return (
    <div style={{ fontSize: 13 }}>
      <div style={{ marginBottom: 4 }}>区块结构（顺序 + 标题）：</div>
      <ol style={{ margin: "0 0 8px", paddingLeft: 20 }}>
        {order.map((key) => (
          <li key={key}>
            {SECTION_KEY_LABELS[key as keyof typeof SECTION_KEY_LABELS] || key}
            {tpl.section_titles?.[key] ? ` → 「${tpl.section_titles[key]}」` : ""}
          </li>
        ))}
      </ol>
      {bands.length > 0 && (
        <div style={{ marginBottom: 4 }}>
          运镜覆盖：
          {bands.map(([band, text]) => (
            <div key={band} style={{ marginLeft: 12 }}>档位 {band}：{text}</div>
          ))}
        </div>
      )}
      {(tpl.beat_labels || []).length > 0 && (
        <div style={{ marginBottom: 4 }}>节奏名：{(tpl.beat_labels || []).join(" / ")}</div>
      )}
      {tpl.default_duration ? <div style={{ marginBottom: 4 }}>默认时长：{tpl.default_duration} 秒</div> : null}
      {tpl.negative_extra ? <div style={{ marginBottom: 4 }}>负面追加：{tpl.negative_extra}</div> : null}
      {tpl.meta_note ? <div>元信息备注：{tpl.meta_note}</div> : null}
    </div>
  );
}

// ── 表单 ────────────────────────────────────────────────────────────────────

function ImageForm({ form, patch }: { form: TemplateForm; patch: (p: Partial<TemplateForm>) => void }) {
  const total = combinedSystemChars(form);
  return (
    <>
      <label style={{ display: "block", fontSize: 13 }}>
        生成指令全文（system）
        <span style={{ float: "right", color: total > SYSTEM_CHAR_LIMIT ? "#c0392b" : "var(--text-muted)" }}>
          {total} / {SYSTEM_CHAR_LIMIT}
        </span>
        <textarea
          value={form.system_text}
          onChange={(e) => patch({ system_text: e.target.value })}
          rows={12}
          style={{ width: "100%", resize: "vertical" }}
          placeholder="克隆内置模版后会带全文；也可整篇重写（红线条款锁默认开启，必保段落不能删）"
        />
      </label>
      <label style={{ display: "block", fontSize: 13 }}>
        追加指令（拼在 system 后）
        <textarea
          value={form.extra_instructions}
          onChange={(e) => patch({ extra_instructions: e.target.value })}
          rows={4}
          style={{ width: "100%", resize: "vertical" }}
          placeholder="只追加你的私人要求，不动内置指令"
        />
      </label>
      {form.kind === "anima_tags" && (
        <>
          <label style={{ display: "block", fontSize: 13 }}>
            固定质量词（≤2000 字符）
            <textarea value={form.quality_tags} onChange={(e) => patch({ quality_tags: e.target.value })} rows={3} style={{ width: "100%" }} />
          </label>
          <label style={{ display: "block", fontSize: 13 }}>
            固定负面词（≤2000 字符）
            <textarea value={form.negative_tags} onChange={(e) => patch({ negative_tags: e.target.value })} rows={4} style={{ width: "100%" }} />
          </label>
        </>
      )}
    </>
  );
}

function VideoForm({ form, patch }: { form: TemplateForm; patch: (p: Partial<TemplateForm>) => void }) {
  const order = form.section_order.length ? form.section_order : [...SECTION_KEYS];
  const move = (idx: number, dir: -1 | 1) => {
    const next = [...order];
    const target = idx + dir;
    if (target < 0 || target >= next.length) return;
    [next[idx], next[target]] = [next[target], next[idx]];
    patch({ section_order: next });
  };
  return (
    <>
      <div style={{ fontSize: 13 }}>
        区块顺序（六段语义固定，只改排列与标题）：
        <ol style={{ margin: "6px 0", paddingLeft: 20 }}>
          {order.map((key, idx) => (
            <li key={key} style={{ marginBottom: 4 }}>
              <span style={{ display: "inline-block", minWidth: 130 }}>
                {SECTION_KEY_LABELS[key as keyof typeof SECTION_KEY_LABELS] || key}
              </span>
              <input
                value={form.section_titles[key] || ""}
                onChange={(e) => patch({
                  section_titles: { ...form.section_titles, [key]: e.target.value },
                })}
                placeholder="默认标题"
                style={{ width: 180, marginRight: 6 }}
              />
              <button type="button" className="btn" onClick={() => move(idx, -1)} aria-label="上移">↑</button>
              <button type="button" className="btn" onClick={() => move(idx, 1)} aria-label="下移">↓</button>
            </li>
          ))}
        </ol>
      </div>
      <fieldset style={{ border: "1px solid var(--border, #ddd)", borderRadius: 6, padding: 8 }}>
        <legend style={{ fontSize: 12 }}>运镜文案覆盖（按 motion 档位；缺档回落内置）</legend>
        {["0", "1", "2", "3"].map((band) => (
          <label key={band} style={{ display: "block", fontSize: 13, marginBottom: 4 }}>
            档位 {band}
            <input
              value={form.camera_band_overrides[band] || ""}
              onChange={(e) => patch({
                camera_band_overrides: { ...form.camera_band_overrides, [band]: e.target.value },
              })}
              placeholder="留空=用内置"
              style={{ width: "100%" }}
            />
          </label>
        ))}
      </fieldset>
      <div style={{ fontSize: 13 }}>
        节奏名（首尾帧三段式）：
        <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
          {(form.beat_labels.length ? form.beat_labels : ["", "", ""]).map((label, i) => (
            <input
              key={i}
              value={label}
              onChange={(e) => {
                const next = form.beat_labels.length ? [...form.beat_labels] : ["", "", ""];
                next[i] = e.target.value;
                patch({ beat_labels: next });
              }}
              placeholder={["开场定格", "剧情演变", "收尾定格"][i]}
              style={{ flex: 1 }}
            />
          ))}
        </div>
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <label style={{ fontSize: 13 }}>
          默认时长（秒，0=不覆盖，2–20）
          <input
            type="number"
            min={0}
            value={form.default_duration}
            onChange={(e) => patch({ default_duration: Number(e.target.value) || 0 })}
            style={{ width: 120 }}
          />
        </label>
      </div>
      <label style={{ display: "block", fontSize: 13 }}>
        负面追加词（≤1000 字符）
        <input value={form.negative_extra} onChange={(e) => patch({ negative_extra: e.target.value })} style={{ width: "100%" }} />
      </label>
      <label style={{ display: "block", fontSize: 13 }}>
        元信息备注（≤500 字符）
        <input value={form.meta_note} onChange={(e) => patch({ meta_note: e.target.value })} style={{ width: "100%" }} />
      </label>
    </>
  );
}

function RedlineLock({ form, patch, onRequestUnlock }: {
  form: TemplateForm; patch: (p: Partial<TemplateForm>) => void; onRequestUnlock: () => void;
}) {
  if (templateKindGroup(form.kind) !== "image") return null;
  return (
    <div style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 8 }}>
      <input
        id="redline-lock"
        type="checkbox"
        checked={form.redline_lock}
        onChange={(e) => (e.target.checked ? patch({ redline_lock: true }) : onRequestUnlock())}
      />
      <label htmlFor="redline-lock">
        红线条款锁：指令文本必须保留「多人分述 / 纯英文覆盖 / 事实边界 / 画面自洽」四段
      </label>
    </div>
  );
}
