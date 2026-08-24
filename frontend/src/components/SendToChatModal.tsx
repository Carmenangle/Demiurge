// 选目标仓库弹框：把资产/画布节点内容发送到指定作品的对话（chatAppend 落盘，不调模型）。
// 用于「发送至对话框」（仅图片）与「发送至对话」（完整配方文本+图片）。
// 「同时创建画布节点」默认开启：把图片作为 generation 入库目标作品（index-generation 幂等），
// 目标作品的画布随即出现对应节点；对话消息与画布节点同步创建。
import { useEffect, useState } from "react";
import { useRepos } from "../stores/repos";
import { chatAppend, indexGeneration } from "../api/ai";
import { resolvedEmbedModel, useSettings } from "../stores/settings";

export interface SendPayload {
  text: string;        // 完整配方文本（发送至对话）或 ""（发送至对话框仅图片）
  images?: string[];   // 图片 URL 列表
  prompt?: string;     // 纯提示词（创建画布节点时作为 generation 的 prompt）
}

export function SendToChatModal({
  title = "发送至对话框", payload, onDone, onCancel,
}: {
  title?: string;
  payload: SendPayload;
  onDone: (repoId: string) => void;
  onCancel: () => void;
}) {
  const { repos } = useRepos();
  const { settings } = useSettings();
  const embed = resolvedEmbedModel(settings);
  const [repoId, setRepoId] = useState("");
  const [createNode, setCreateNode] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const confirm = async () => {
    if (!repoId || sending) return;
    setSending(true);
    setError(null);
    try {
      // 1. 消息写入目标作品对话（时间顺序真源）
      await chatAppend(repoId, "user", payload.text, payload.images || []);
      // 2. 同时创建画布节点：每张图作为 generation 入库目标 repo（幂等，同图不重复）
      if (createNode) {
        const images = payload.images || [];
        if (images.length) {
          try {
            for (const url of images) {
              await indexGeneration(repoId, { prompt: payload.prompt || payload.text, image_url: url }, embed);
            }
            window.dispatchEvent(new CustomEvent("laf-generation-saved", { detail: repoId }));
          } catch (nodeErr) {
            setError(`消息已发送，但画布节点创建失败：${nodeErr instanceof Error ? nodeErr.message : String(nodeErr)}`);
            setSending(false);
            return;
          }
        }
      }
      onDone(repoId);
    } catch (e) {
      setError(`发送失败：${e instanceof Error ? e.message : String(e)}`);
      setSending(false);
    }
  };

  return (
    <div className="modal-mask" onClick={onCancel}>
      <div className="modal" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        <p style={{ fontSize: 13, color: "var(--text-muted)", margin: "4px 0 10px" }}>
          选择目标作品，内容会以 user 消息写入该作品的对话（刷新后保留）。
        </p>
        <select
          value={repoId}
          onChange={(e) => setRepoId(e.target.value)}
          style={{ width: "100%", marginBottom: 10 }}
          autoFocus
        >
          <option value="">选择作品…</option>
          {repos.map((r) => (
            <option key={r.id} value={r.id}>
              {r.parentId ? `↳ ${r.name}` : r.name}
            </option>
          ))}
        </select>
        {payload.images?.length ? (
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, marginBottom: 10 }}>
            <input type="checkbox" checked={createNode} onChange={(e) => setCreateNode(e.target.checked)} />
            同时在目标作品画布创建节点（图片入库为 generation）
          </label>
        ) : null}
        {error && <p style={{ color: "var(--danger, #e5484d)", fontSize: 13, margin: "4px 0 8px" }}>{error}</p>}
        <div className="modal-actions">
          <button className="btn" onClick={onCancel}>取消</button>
          <button className="btn primary" disabled={!repoId || sending} onClick={confirm}>
            {sending ? "发送中…" : "发送"}
          </button>
        </div>
      </div>
    </div>
  );
}
