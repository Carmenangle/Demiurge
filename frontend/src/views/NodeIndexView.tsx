import { useEffect, useRef, useState } from "react";
import { Bot, RefreshCw, Pencil } from "lucide-react";
import { PageShell, StateHint } from "../components/layout/PageShell";
import { ConfirmModal } from "../components/Modal";
import { resolvedEmbedModel, useSettings } from "../stores/settings";
import {
  nodeStats, syncNodes, syncProgress,
  listNodePacks, getNodePack, updateNodePackContent, type NodePackItem,
} from "../api/ai";
import { formatNodeSyncResult } from "../lib/nodeSync";

// 节点知识库：展示已索引的节点包/节点数，提供同步（增量/全量）。
// 同步只读取节点自带的 description/display_name/category 拼文本入库嵌入检索，不调用大模型，不耗对话 token。
export function NodeIndexView() {
  const { settings } = useSettings();
  const [stats, setStats] = useState<{ packs: number; nodes: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [msg, setMsg] = useState("");
  const [confirmFull, setConfirmFull] = useState(false);
  const [prog, setProg] = useState<{ done: number; total: number; current: string } | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  // 包列表 + 用途编辑
  const [packs, setPacks] = useState<NodePackItem[]>([]);
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<{ id: string; title: string; content: string } | null>(null);
  const [saving, setSaving] = useState(false);

  const loadStats = () => {
    setLoading(true);
    nodeStats(resolvedEmbedModel(settings))
      .then(setStats)
      .catch(() => setStats(null))
      .finally(() => setLoading(false));
    listNodePacks(resolvedEmbedModel(settings)).then((r) => setPacks(r.packs)).catch(() => setPacks([]));
  };

  useEffect(loadStats, []); // eslint-disable-line react-hooks/exhaustive-deps

  // 点编辑：拉单包完整内容（含用途正文）再开弹窗
  const openEdit = async (id: string, title: string) => {
    try {
      const p = await getNodePack(resolvedEmbedModel(settings), id);
      setEditing({ id, title, content: p.content });
    } catch (e) {
      setMsg(`读取失败：${(e as Error).message}`);
    }
  };
  const saveEdit = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      await updateNodePackContent(resolvedEmbedModel(settings), editing.id, editing.content);
      setEditing(null);
      setMsg(`已更新「${editing.title}」的用途描述。`);
    } catch (e) {
      setMsg(`保存失败：${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };
  useEffect(() => () => { if (timer.current) clearInterval(timer.current); }, []); // 卸载停轮询

  const stopPoll = () => { if (timer.current) { clearInterval(timer.current); timer.current = null; } };

  const doSync = async (full: boolean) => {
    setConfirmFull(false);
    setSyncing(true);
    setProg(null);
    setMsg(full ? "启动全量重建…" : "启动增量同步…");
    try {
      await syncNodes(resolvedEmbedModel(settings), settings.comfyuiUrl, full);
      // 启动成功，轮询进度直到结束
      stopPoll();
      timer.current = setInterval(async () => {
        try {
          const p = await syncProgress();
          setProg({ done: p.done, total: p.total, current: p.current });
          if (p.finished || !p.running) {
            stopPoll();
            setSyncing(false);
            setProg(null);
            if (p.error) setMsg(`同步失败：${p.error}`);
            else setMsg(formatNodeSyncResult(p));
            loadStats();
          }
        } catch { /* 单次轮询失败忽略，继续 */ }
      }, 1000);
    } catch (e) {
      stopPoll();
      setSyncing(false);
      setMsg(`同步失败：${(e as Error).message}（确认 ComfyUI 已启动、嵌入模型已配置）`);
    }
  };

  return (
    <PageShell
      title="节点知识库"
      actions={
        <>
          <button className="btn" disabled={syncing} onClick={() => doSync(false)}>
            <RefreshCw size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
            {syncing ? "同步中…" : "增量同步"}
          </button>
          <button className="btn" disabled={syncing} onClick={() => setConfirmFull(true)}>
            全量重建
          </button>
        </>
      }
    >
      <p style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 0 }}>
        AI 搭工作流时从这里检索可用节点。同步会扫描 ComfyUI 已装节点，收录每个节点自带的说明入库（不调用大模型、不耗 token）。
        装/卸节点后建议增量同步以更新可用节点集。
      </p>

      {loading ? (
        <StateHint>读取知识库状态…</StateHint>
      ) : (
        <div className="stat-cards">
          <div className="stat-card">
            <Bot size={20} />
            <div className="stat-num">{stats?.packs ?? 0}</div>
            <div className="stat-label">节点包</div>
          </div>
          <div className="stat-card">
            <div className="stat-num">{stats?.nodes ?? 0}</div>
            <div className="stat-label">节点总数</div>
          </div>
        </div>
      )}

      {syncing && prog && prog.total > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 13, color: "var(--text)", marginBottom: 6 }}>
            正在建立索引 {prog.done}/{prog.total}
            {prog.current && <span style={{ color: "var(--text-muted)" }}>　当前：{prog.current}</span>}
          </div>
          <div style={{ height: 8, borderRadius: 4, background: "var(--surface-2)", overflow: "hidden" }}>
            <div style={{
              height: "100%", width: `${Math.round((prog.done / prog.total) * 100)}%`,
              background: "var(--accent)", transition: "width .3s",
            }} />
          </div>
        </div>
      )}
      {msg && <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 12 }}>{msg}</p>}

      {(stats?.packs ?? 0) === 0 && !loading ? (
        <p style={{ fontSize: 13, color: "var(--warning)", marginTop: 8 }}>
          知识库为空，先点「增量同步」建立节点索引，AI 才能据此搭工作流。
        </p>
      ) : packs.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div className="page-toolbar" style={{ marginBottom: 8 }}>
            <input placeholder="搜索节点包名…" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 280 }} />
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>共 {packs.length} 个包，可点「编辑」修订 AI 检索用的用途描述</span>
          </div>
          <div className="node-table" style={{ maxHeight: 460, overflow: "auto" }}>
            <div className="node-row node-row-head"><span>节点包</span><span>节点数</span><span>来源模块</span><span>操作</span></div>
            {packs
              .filter((p) => !q.trim() || (p.title + p.python_module).toLowerCase().includes(q.trim().toLowerCase()))
              .map((p) => (
                <div className="node-row" key={p.id}>
                  <span className="node-name">{p.title}</span>
                  <span>{p.node_count}</span>
                  <span style={{ color: "var(--text-muted)", fontSize: 12 }}>{p.python_module}</span>
                  <span className="node-ops">
                    <button className="icon-btn" title="编辑用途描述" onClick={() => openEdit(p.id, p.title)}>
                      <Pencil size={15} />
                    </button>
                  </span>
                </div>
              ))}
          </div>
        </div>
      )}

      {confirmFull && (
        <ConfirmModal
          title="全量重建节点知识库"
          message="将忽略已有索引，重新扫描全部已装节点并逐包重建（重新嵌入）。节点多时耗时较久，但不调用大模型、不耗对话 token。确认继续？"
          confirmText="全量重建"
          onConfirm={() => doSync(true)}
          onCancel={() => setConfirmFull(false)}
        />
      )}

      {editing && (
        <div className="modal-mask" onClick={() => !saving && setEditing(null)}>
          <div className="modal" style={{ maxWidth: 720, width: "90%" }} onClick={(e) => e.stopPropagation()}>
            <h3>编辑用途描述 — {editing.title}</h3>
            <p style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 0 }}>
              这段文本是 AI 搭工作流时语义检索用的。改完保存会重新嵌入。谨慎删节点行，否则 AI 可能选不到对应节点。
            </p>
            <textarea
              value={editing.content}
              onChange={(e) => setEditing({ ...editing, content: e.target.value })}
              rows={16}
              style={{ width: "100%", fontFamily: "monospace", fontSize: 13, resize: "vertical" }}
            />
            <div className="modal-actions">
              <button className="btn" disabled={saving} onClick={() => setEditing(null)}>取消</button>
              <button className="btn primary" disabled={saving} onClick={saveEdit}>{saving ? "保存中…" : "保存"}</button>
            </div>
          </div>
        </div>
      )}
    </PageShell>
  );
}
