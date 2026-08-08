import { useEffect, useRef, useState } from "react";
import { FileUp, FolderSearch, Workflow as WorkflowIcon, Trash2, Pencil, FileText, Eye, List, ChevronUp, ChevronDown, RotateCcw } from "lucide-react";
import { type Settings, activeChatModel } from "../stores/settings";
import { comfyStatus } from "../api/comfyui";
import { lockUrl, postToFrame, isLafMessageFromStrict } from "../lib/lafLock";
import { useWorkflowTemplates } from "../lib/useWorkflowTemplates";
import { DescribeModal, type DescribeValue } from "../components/DescribeModal";
import { ConfirmModal, AlertModal, PromptModal } from "../components/Modal";
import { PageShell } from "../components/layout/PageShell";
import {
  rawWorkflowByPath,
  createTemplate,
  updateTemplate,
  type ParsedNode,
  type ParsedField,
  type Template,
  type ExposedField,
  type ControlType,
} from "../api/workflows";
import {
  inferWorkflowFieldBinding, inferWorkflowFieldControl, replaceWorkflowNodeExposure,
} from "../lib/workflowTemplateExposure";

const CONTROL_LABELS: Record<ControlType, string> = {
  text: "单行文本",
  textarea: "多行文本",
  number: "数字",
  select: "下拉选择",
  image: "图片",
  seed: "随机种子",
  boolean: "开关",
};

// 截断超长默认值显示
function shortVal(v: unknown): string {
  const s = String(v);
  return s.length > 60 ? s.slice(0, 60) + "…" : s;
}

export function WorkflowTemplates({ settings }: { settings: Settings }) {
  const {
    files, parsed, setParsed, fileName, sourcePath, editingTemplate, templates,
    error, busy, describeTarget, setDescribeTarget, deleting, setDeleting,
    nodeSyncing, building, showBuild, setShowBuild, alertMsg, setAlertMsg,
    onSyncNodes, onBuild, onScan, onOpenScanned, onPickFile, onEditTemplate,
    onEditDescribe, saveDescribe, onSaved, onDeleteTemplate,
  } = useWorkflowTemplates(settings);

  return (
    <PageShell
      title="工作流模板"
      actions={
        <>
          <button className="btn" onClick={onSyncNodes} disabled={nodeSyncing}
            title="扫描 ComfyUI 已装节点建立知识库，供 AI 搭工作流检索">
            <WorkflowIcon size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
            {nodeSyncing ? "同步中…" : "同步节点库"}
          </button>
          <button className="btn" onClick={() => setShowBuild(true)} disabled={building || !settings.workflowDir}
            title="用自然语言描述需求，AI 检索节点自动搭建工作流并存到默认路径">
            <WorkflowIcon size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
            {building ? "搭建中…" : "AI 搭工作流"}
          </button>
          <button className="btn" onClick={onScan} disabled={!settings.workflowDir || busy}>
            <FolderSearch size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
            扫描默认目录
          </button>
          <label className="btn primary" style={{ cursor: "pointer" }}>
            <FileUp size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
            选择文件导入
            <input type="file" accept=".json" hidden onChange={onPickFile} />
          </label>
        </>
      }
    >

      {!settings.workflowDir && (
        <p style={{ color: "var(--text-muted)" }}>
          未设置工作流默认读取路径。可在「设置 → 路径」中配置后扫描该目录，或直接「选择文件导入」。
        </p>
      )}

      {error && <p style={{ color: "#d23b3b" }}>{error}</p>}

      {!parsed && (
        <>
          {files.length > 0 && (
            <div className="list" style={{ marginTop: 12 }}>
              {files.map((f) => (
                <div className="row" key={f.path}>
                  <div>
                    <strong>{f.name}</strong>
                    <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 12 }}>{f.rel}</p>
                  </div>
                  <button className="btn" onClick={() => onOpenScanned(f)} disabled={busy}>
                    新建模板
                  </button>
                </div>
              ))}
            </div>
          )}

          <h2 style={{ marginTop: 28, fontSize: 16 }}>已保存模板</h2>
          {templates.length === 0 ? (
            <div style={{ marginTop: 8, color: "var(--text-muted)" }}>
              <WorkflowIcon size={18} style={{ verticalAlign: "-3px", marginRight: 6 }} />
              还没有模板。扫描目录或导入文件，勾选要暴露的参数后保存。
            </div>
          ) : (
            <div className="list" style={{ marginTop: 8 }}>
              {templates.map((t) => (
                <div className="row" key={t.id}>
                  <div>
                    <strong>{t.name}</strong>
                    <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 12 }}>
                      {(t.node_order?.length ?? 0)} 个节点
                      {t.exposed.length > 0 ? ` · ${t.exposed.length} 个暴露字段` : ""}
                      {t.source_path ? ` · ${t.source_path}` : "（无原始路径）"}
                    </p>
                  </div>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button className="icon-btn" title="编辑" onClick={() => onEditTemplate(t)}>
                      <Pencil size={15} />
                    </button>
                    <button className="icon-btn" title="编写能力描述" onClick={() => onEditDescribe(t)}>
                      <FileText size={15} />
                    </button>
                    <button className="icon-btn" title="删除" onClick={() => setDeleting(t)}>
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {parsed && (
        <NodeEditor
          fileName={fileName}
          sourcePath={sourcePath}
          nodes={parsed}
          template={editingTemplate}
          comfyUrl={settings.comfyuiUrl}
          chat={activeChatModel(settings)}
          onBack={() => setParsed(null)}
          onSaved={onSaved}
        />
      )}

      {describeTarget && (
        <DescribeModal
          workflowName={describeTarget.template.name}
          nodes={describeTarget.nodes.map((n) => ({ id: n.id, type: n.class_type, title: n.title }))}
          chat={activeChatModel(settings)}
          comfyUrl={settings.comfyuiUrl}
          sourcePath={describeTarget.template.source_path}
          initial={{
            description: describeTarget.template.description || "",
            input_node_ids: describeTarget.template.input_node_ids || [],
            output_node_ids: describeTarget.template.output_node_ids || [],
            primary_output_node_id: describeTarget.template.primary_output_node_id || "",
          }}
          onConfirm={saveDescribe}
          onCancel={() => setDescribeTarget(null)}
        />
      )}
      {deleting && (
        <ConfirmModal
          title="删除模板"
          message={`确认删除模板「${deleting.name}」？`}
          confirmText="删除"
          danger
          onConfirm={() => { const t = deleting; setDeleting(null); onDeleteTemplate(t); }}
          onCancel={() => setDeleting(null)}
        />
      )}
      {showBuild && (
        <PromptModal
          title="AI 搭工作流：描述你要的功能"
          defaultValue=""
          confirmText="开始搭建"
          onConfirm={onBuild}
          onCancel={() => setShowBuild(false)}
        />
      )}
      {alertMsg && (
        <AlertModal title={alertMsg.title} message={alertMsg.message} onClose={() => setAlertMsg(null)} />
      )}
    </PageShell>
  );
}

const fieldKey = (nodeId: string, field: string) => `${nodeId}.${field}`;

function NodeEditor({
  fileName,
  sourcePath,
  nodes,
  template,
  comfyUrl,
  chat,
  onBack,
  onSaved,
}: {
  fileName: string;
  sourcePath: string;
  nodes: ParsedNode[];
  template: Template | null;
  comfyUrl: string;
  chat: { baseUrl: string; apiKey: string; modelName: string; proxyUrl?: string };
  onBack: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(template?.name || fileName.replace(/\.json$/i, ""));
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [showDescribe, setShowDescribe] = useState(false);
  // 视图模式：list=参数清单（可勾选暴露），comfy=嵌真实 ComfyUI 画布预览
  const [viewMode, setViewMode] = useState<"list" | "comfy">("list");
  const [comfyHint, setComfyHint] = useState("");
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  // 递增即强制 iframe 卸载重建。ComfyUI 的画布是页面级单例，另开的 ComfyUI 标签会把它
  // 抢走，导致本页画布空白（左下角仍显示节点数）。这种情况只有真正重挂 iframe 才能抢回来，
  // 发 ping_ready 之类的软消息无效。
  const [reloadKey, setReloadKey] = useState(0);
  // 跨标签争用自愈：另开的 ComfyUI 标签会话恢复可能把本页画布覆盖成别的整图（现象：左下角节点数
  // 远大于本模板，如 N:209 而模板仅 37）。对齐 WorkflowCard 的做法——载图后过会话恢复窗口校验画布，
  // 不对就【软重发 load】（laf_lock 内部 clear+keepOnly+closeExtraWorkflows 重载本图并清多余标签），
  // 不整帧重挂（重挂=全量重载整页，慢）。用尽重试才回退手动「重新载入画布」。
  // 判定不用「节点数相等」：ComfyUI 载图后部分节点会被隐藏/未实例化（reroute、折叠组等），
  // serialize 回传的活动图节点数会【少于】原始 JSON，精确相等会永远误判。改用【id 子集】判定——
  // 画布上出现了本模板没有的节点=被别的工作流抢占；只是变少（本模板子集）则属正常隐藏。
  const rawRef = useRef<unknown>(null);            // 已取的原始工作流，供软重发复用（免重复请求）
  const expectedIdsRef = useRef<Set<string>>(new Set()); // 本模板全部节点 id（隐藏前），判外来节点用
  const retryRef = useRef(0);
  const verifyRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const MAX_RELOAD_RETRY = 6;
  // 画布模式下长按选中的节点，按选择顺序排列；供后续 AI 对话按此顺序提供节点
  const [picked, setPicked] = useState<{ id: string; title: string }[]>(
    () => (template?.node_order || []).map((id) => ({ id, title: `#${id}` })),
  );

  // key -> 暴露配置
  const [exposed, setExposed] = useState<Map<string, ExposedField>>(() => {
    const m = new Map<string, ExposedField>();
    // 编辑旧模板时先回填，再按已选节点的原始定义重建，清除 lora_weight 等人工语义别名。
    if (template) {
      for (const ef of template.exposed) m.set(fieldKey(ef.node_id, ef.field), ef);
      let fields = [...m.values()];
      for (const id of template.node_order || []) {
        const node = nodes.find((item) => item.id === id);
        if (node) fields = replaceWorkflowNodeExposure(fields, node);
      }
      return new Map(fields.map((field) => [fieldKey(field.node_id, field.field), field]));
    }
    return m;
  });

  const toggle = (n: ParsedNode, f: ParsedField) => {
    const key = fieldKey(n.id, f.name);
    setExposed((prev) => {
      const next = new Map(prev);
      if (next.has(key)) next.delete(key);
      else {
        const binding = inferWorkflowFieldBinding(n, f);
        next.set(key, {
          node_id: n.id,
          field: f.name,
          label: f.name,
          control: inferWorkflowFieldControl(f),
          semantic: f.name,
          binding: binding || undefined,
          default: f.value,
        });
      }
      return next;
    });
  };

  const patch = (key: string, p: Partial<ExposedField>) =>
    setExposed((prev) => {
      const cur = prev.get(key);
      if (!cur) return prev;
      const next = new Map(prev);
      next.set(key, { ...cur, ...p });
      return next;
    });

  const onSave = async () => {
    // 先弹能力描述弹窗（方案 C），确定后才真正保存
    setShowDescribe(true);
  };

  const doSave = async (d: DescribeValue) => {
    setShowDescribe(false);
    setErr("");
    setSaving(true);
    const payload = {
      name,
      source_path: sourcePath,
      exposed: [...exposed.values()],
      node_order: picked.map((p) => p.id),
      description: d.description,
      input_node_ids: d.input_node_ids,
      output_node_ids: d.output_node_ids,
      primary_output_node_id: d.primary_output_node_id || "",
    };
    try {
      if (template) await updateTemplate(template.id, payload);
      else await createTemplate(payload);
      onSaved();
    } catch (e) {
      setErr(`保存失败：${(e as Error).message}`);
      setSaving(false);
    }
  };

  // 切换到 ComfyUI 画布模式：先确认 ComfyUI 在跑
  const toggleComfyMode = async () => {
    if (viewMode === "comfy") {
      setViewMode("list");
      return;
    }
    setComfyHint("");
    try {
      const s = await comfyStatus(comfyUrl);
      if (!s.running) {
        setComfyHint("ComfyUI 未启动。请在对话页「ComfyUI 节点面板」启动，或运行 start-dev。");
        return;
      }
      setViewMode("comfy");
    } catch {
      setComfyHint("无法连接 ComfyUI，请确认已启动。");
    }
  };

  // 画布模式：iframe 内扩展 ready 后，把整张工作流发去载入（exposedIds 空=显示全部节点）
  useEffect(() => {
    if (viewMode !== "comfy") return;
    retryRef.current = 0;
    const post = (type: string, payload?: unknown) =>
      postToFrame(iframeRef.current?.contentWindow, type, payload, comfyUrl);
    const sendLoad = (wf: unknown) => post("load", { workflow: wf, exposedIds: [] });
    // 过会话恢复窗口再校验：首验 2.2s 覆盖恢复窗口，软重发后复验缩到 900ms（已过窗口，加快收敛）。
    const scheduleVerify = (delay: number) => {
      verifyRef.current = setTimeout(() => post("request_graph"), delay);
    };
    const onMsg = async (ev: MessageEvent) => {
      if (!isLafMessageFromStrict(ev, iframeRef.current?.contentWindow, comfyUrl)) return;
      const d = ev.data;
      if (d.type === "ready") {
        try {
          const r = await rawWorkflowByPath(sourcePath);
          rawRef.current = r.workflow;
          const n = (r.workflow as { nodes?: { id?: unknown }[] } | null)?.nodes;
          expectedIdsRef.current = new Set(
            Array.isArray(n) ? n.map((x) => String(x?.id)) : [],
          );
          sendLoad(r.workflow);
        } catch (e) {
          setComfyHint(`载入失败：${(e as Error).message}`);
        }
      } else if (d.type === "loaded") {
        // 画布模式以原节点定义为准：旧模板里手工改过的 semantic/label/default 全部恢复默认。
        setExposed((prev) => {
          let fields = [...prev.values()];
          for (const selected of picked) {
            const node = nodes.find((item) => item.id === selected.id);
            if (node) fields = replaceWorkflowNodeExposure(fields, node);
          }
          return new Map(fields.map((field) => [fieldKey(field.node_id, field.field), field]));
        });
        // 载图后回填已选节点的高亮（重新进入画布时保持选择状态）
        for (const p of picked) post("reselect", { id: p.id });
        scheduleVerify(2200); // 载图后校验画布是否被别的标签抢占成别的工作流
      } else if (d.type === "graph") {
        // 校验：画布节点是否都属于本模板（id 子集）。出现本模板没有的 id=被别的工作流抢占，软重发重试。
        // 隐藏节点使活动图节点数少于原始 JSON 属正常，不据数目相等判定（见上方 ref 注释）。
        const expected = expectedIdsRef.current;
        const liveNodes = (d.payload?.workflow?.nodes ?? []) as { id?: unknown }[];
        const foreign = liveNodes.filter((x) => !expected.has(String(x?.id)));
        // 期望集为空（原始非 UI 格式取不到 id）跳过校验；活动图为空视为尚未载入，继续重试
        if (expected.size === 0 || (foreign.length === 0 && liveNodes.length > 0)) {
          setComfyHint("");
        } else if (retryRef.current < MAX_RELOAD_RETRY) {
          retryRef.current += 1;
          if (rawRef.current) sendLoad(rawRef.current);
          scheduleVerify(900);
        } else {
          setComfyHint(`画布被其他 ComfyUI 标签抢占（出现 ${foreign.length} 个非本模板节点）。请关掉其他 ComfyUI 标签后点「重新载入画布」。`);
        }
      } else if (d.type === "node_selected") {
        const id = String(d.payload.id);
        setPicked((prev) =>
          prev.some((p) => p.id === id)
            ? prev
            : [...prev, { id, title: d.payload.title || `#${id}` }],
        );
        const node = nodes.find((item) => item.id === id);
        if (node) {
          setExposed((prev) => {
            const fields = replaceWorkflowNodeExposure([...prev.values()], node);
            return new Map(fields.map((field) => [fieldKey(field.node_id, field.field), field]));
          });
        }
      } else if (d.type === "node_title") {
        // 重新进入画布时扩展回传真实标题，替换占位的 #id
        const id = String(d.payload.id);
        setPicked((prev) =>
          prev.map((p) => (p.id === id ? { ...p, title: d.payload.title || p.title } : p)),
        );
      }
    };
    window.addEventListener("message", onMsg);
    // ready 竞态兜底：扩展在我们挂上监听前就发了 ready 的话，这里补问一次。
    // 对齐 WorkflowCard.tsx / AIBuildView.tsx 的既有做法。
    const ping = setTimeout(
      () => postToFrame(iframeRef.current?.contentWindow, "ping_ready", undefined, comfyUrl),
      1500,
    );
    return () => {
      clearTimeout(ping);
      if (verifyRef.current) clearTimeout(verifyRef.current);
      window.removeEventListener("message", onMsg);
    };
  }, [viewMode, sourcePath, picked, comfyUrl, reloadKey]);

  // 从右侧列表删除一个选中节点（同步取消画布高亮）
  const removePicked = (id: string) => {
    setPicked((prev) => prev.filter((p) => p.id !== id));
    setExposed((prev) => {
      const next = new Map(prev);
      for (const [key, field] of next) {
        if (field.node_id === id) next.delete(key);
      }
      return next;
    });
    postToFrame(iframeRef.current?.contentWindow, "deselect", { id }, comfyUrl);
  };

  // 列表排序：上移/下移
  const movePicked = (idx: number, dir: -1 | 1) => {
    setPicked((prev) => {
      const next = [...prev];
      const j = idx + dir;
      if (j < 0 || j >= next.length) return prev;
      [next[idx], next[j]] = [next[j], next[idx]];
      return next;
    });
  };

  return (
    <div style={{ marginTop: 16 }}>
      <div className="template-editor-summary">
        <button className="back-btn" onClick={onBack}>
          ← 返回列表
        </button>
        <span style={{ color: "var(--text-muted)" }}>
          {fileName}　{nodes.length} 个节点，已选 {picked.length} 个节点
          {exposed.size > 0 ? `，已暴露 ${exposed.size} 个字段` : ""}。
        </span>
      </div>

      <div className="template-editor-toolbar">
        <div className="field" style={{ maxWidth: 360, flex: 1, margin: 0 }}>
          <label>模板名称</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="模板名称" />
        </div>
        <button className="btn" onClick={toggleComfyMode} title="在真实 ComfyUI 画布中查看节点">
          {viewMode === "comfy" ? (
            <>
              <List size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
              参数清单模式
            </>
          ) : (
            <>
              <Eye size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
              ComfyUI 界面模式
            </>
          )}
        </button>
        {viewMode === "comfy" && (
          <button
            className="btn"
            onClick={() => { setComfyHint(""); setReloadKey((k) => k + 1); }}
            title="画布空白或节点没载入时点这里，重新挂载画布并重载工作流"
          >
            <RotateCcw size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
            重新载入画布
          </button>
        )}
      </div>
      {comfyHint && <p style={{ color: "#c98a1a", fontSize: 13, marginTop: 6 }}>{comfyHint}</p>}
      {viewMode === "comfy" && (
        <p style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 6 }}>
          提示：另开的 ComfyUI 标签会抢占画布，载入成别的工作流。本页会自动检测并重载纠正（部分节点载入后被隐藏属正常，不影响判定）；若仍不对，关掉其他 ComfyUI 标签后点上方「重新载入画布」。
        </p>
      )}

      {viewMode === "comfy" && (
        <div className="template-canvas-layout">
          <div className="lock-canvas" style={{ height: 600, flex: 1 }}>
            <iframe
              key={`${sourcePath}::${reloadKey}`}
              ref={iframeRef}
              src={lockUrl(comfyUrl)}
              title="ComfyUI 画布预览"
              className="lock-frame"
            />
          </div>
          <div className="picked-panel">
            <div className="picked-head">
              已选节点 <span style={{ color: "var(--text-muted)" }}>({picked.length})</span>
            </div>
            <p className="picked-tip">在画布上长按节点选择；会自动带入原节点全部可编辑参数，列表顺序即 AI 对话提供节点的顺序。</p>
            {picked.length === 0 ? (
              <div className="picked-empty">长按画布中的节点加入</div>
            ) : (
              <div className="picked-list">
                {picked.map((p, i) => (
                  <div className="picked-item" key={p.id}>
                    <span className="picked-idx">{i + 1}</span>
                    <span className="picked-name" title={p.title}>
                      {p.title} <span style={{ color: "var(--text-muted)" }}>#{p.id}</span>
                    </span>
                    <button className="icon-btn" title="上移" disabled={i === 0} onClick={() => movePicked(i, -1)}>
                      <ChevronUp size={14} />
                    </button>
                    <button
                      className="icon-btn"
                      title="下移"
                      disabled={i === picked.length - 1}
                      onClick={() => movePicked(i, 1)}
                    >
                      <ChevronDown size={14} />
                    </button>
                    <button className="icon-btn" title="移除" onClick={() => removePicked(p.id)}>
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {viewMode === "list" && nodes.map((n) => (
        <div className="image-model-card" key={n.id} style={{ opacity: n.bypassed ? 0.55 : 1 }}>
          <div className="row-head">
            <strong>
              {n.title || n.class_type}{" "}
              <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>
                #{n.id} · {n.class_type}
              </span>
            </strong>
            {n.bypassed && (
              <span style={{ color: "#c98a1a", fontSize: 12 }}>已绕过 / 静音</span>
            )}
          </div>
          {n.fields.map((f) => {
            const key = fieldKey(n.id, f.name);
            const cfg = exposed.get(key);
            return (
              <div key={key} style={{ padding: "6px 0", opacity: f.linked ? 0.45 : 1 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <input
                    type="checkbox"
                    disabled={f.linked}
                    checked={!!cfg}
                    onChange={() => toggle(n, f)}
                    style={{ width: "auto", margin: 0 }}
                  />
                  <span style={{ flex: 1 }}>{f.name}</span>
                  {f.linked ? (
                    <span style={{ color: "var(--text-muted)", fontSize: 12 }}>连线（不可暴露）</span>
                  ) : f.value === null || f.value === "" ? (
                    <span style={{ color: "var(--text-muted)", fontSize: 12 }}>空值</span>
                  ) : (
                    <span
                      style={{ color: "var(--text-muted)", fontSize: 12, maxWidth: 380, textAlign: "right" }}
                      title={String(f.value)}
                    >
                      默认 {shortVal(f.value)}
                    </span>
                  )}
                </label>

                {cfg && (
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "1fr 1fr",
                      gap: 8,
                      margin: "8px 0 4px 24px",
                    }}
                  >
                    <div className="field" style={{ margin: 0 }}>
                      <label>显示标签</label>
                      <input
                        value={cfg.label}
                        onChange={(e) => patch(key, { label: e.target.value })}
                        placeholder="展示给用户的名称"
                      />
                    </div>
                    <div className="field" style={{ margin: 0 }}>
                      <label>控件类型</label>
                      <select
                        value={cfg.control}
                        onChange={(e) => patch(key, { control: e.target.value as ControlType })}
                      >
                        {(Object.keys(CONTROL_LABELS) as ControlType[]).map((c) => (
                          <option key={c} value={c}>
                            {CONTROL_LABELS[c]}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ))}

      {err && <p style={{ color: "#d23b3b" }}>{err}</p>}

      {/* 右下角固定保存按钮，不随滚动移动；左移避开 AI 客服悬浮球 */}
      <button
        className="btn primary template-save-fab"
        onClick={onSave}
        disabled={saving || !name.trim()}
      >
        {saving ? "保存中…" : template ? "更新模板" : "保存模板"}
      </button>

      {showDescribe && (
        <DescribeModal
          workflowName={name}
          nodes={nodes.map((n) => ({ id: n.id, type: n.class_type, title: n.title }))}
          chat={chat}
          comfyUrl={comfyUrl}
          sourcePath={sourcePath}
          initial={{
            description: template?.description || "",
            input_node_ids: template?.input_node_ids || [],
            output_node_ids: template?.output_node_ids || [],
            primary_output_node_id: template?.primary_output_node_id || "",
          }}
          onConfirm={doSave}
          onCancel={() => setShowDescribe(false)}
        />
      )}
    </div>
  );
}
