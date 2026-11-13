import { useCallback, useEffect, useState } from "react";
import { History } from "lucide-react";
import { fetchChatArchives, restoreChatArchive, type ChatArchiveEntry } from "../api/ai";
import { useOverlayRoute } from "../lib/overlayRoute";
import { ConfirmModal } from "./Modal";

// 「恢复对话」弹窗：列出本会话的删除前归档，挑一份整条覆盖当前会话。
//
// 归档由后端在**删除动作之前**写下（清缓存/压缩/清除对话都会先把当时的完整会话存一份），
// 所以这里的条目不是备份按钮的产物，而是「已经发生过一次删除」的证据。恢复同样是覆盖动作，
// 后端会先把当前状态再归档一份 ⇒ 压错了还能压回来（chat_archive.REASON_RESTORE）。
//
// 恢复成功必须走「以后端为真源」的重载（清墓碑），否则刚被删掉的消息会被墓碑过滤挡回去。
const REASONS: Record<string, string> = {
  clear: "清除对话",
  "clear-cache": "清除缓存",
  compact: "压缩会话",
  restore: "恢复前的当前会话",
};

/** 未登记的原因码原样显示：宁可露出一个陌生英文码，也不替用户猜它是哪个动作。 */
function reasonLabel(reason: string): string {
  return REASONS[reason] || reason;
}

function sizeLabel(bytes: number): string {
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

export function ChatArchiveModal({
  threadId, onRestored, onNotify, onClose,
}: {
  threadId: string;
  /** 恢复成功后以服务端为真源重读会话（ChatView 的导入式重载：先清墓碑）。 */
  onRestored: () => Promise<void>;
  onNotify: (message: string, kind?: "info" | "error" | "success") => void;
  onClose: () => void;
}) {
  useOverlayRoute("chat-archive", onClose);
  const [items, setItems] = useState<ChatArchiveEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [target, setTarget] = useState<ChatArchiveEntry | null>(null);
  const [restoring, setRestoring] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      setItems((await fetchChatArchives(threadId)).items || []);
    } catch (error) {
      setErr(`归档列表加载失败：${(error as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [threadId]);

  useEffect(() => { void load(); }, [load]);

  const restore = async () => {
    if (!target) return;
    setRestoring(true);
    setErr("");
    try {
      const result = await restoreChatArchive(threadId, target.name);
      await onRestored();
      onClose();
      onNotify(`已恢复到 ${target.created_at} 的会话（${result.messages} 条消息）`, "success");
    } catch (error) {
      // 后端 busy / 归档名非法 / 写入失败都在这儿现形；列表保持开着可换一份再试。
      setErr(`恢复失败：${(error as Error).message}`);
      setTarget(null);
    } finally {
      setRestoring(false);
    }
  };

  // 二次确认阶段只渲染确认弹窗：两层 modal-mask 叠着会让 Esc 一次关掉两个。
  if (target) {
    return (
      <ConfirmModal
        title="用这份归档覆盖当前会话？"
        message={(
          <>
            归档时间 {target.created_at}（{reasonLabel(target.reason)} · {target.message_count} 条消息）。
            <br />
            当前会话会被整条替换；替换之前，后端会先把当前会话也存一份，压错了还能再压回来。
            <br />
            资产库与知识库不受影响。
          </>
        )}
        confirmText={restoring ? "恢复中…" : "恢复这份"}
        danger
        busy={restoring}
        onConfirm={() => { void restore(); }}
        onCancel={() => setTarget(null)}
      />
    );
  }

  return (
    <div className="modal-mask">
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: 520, maxWidth: "92vw" }}>
        <h3 style={{ margin: "0 0 4px", display: "flex", alignItems: "center", gap: 6 }}>
          <History size={17} />恢复对话
        </h3>
        <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "0 0 10px" }}>
          清除缓存、压缩会话、清除对话都会先把当时的完整会话归档一份。挑一份恢复＝用它会话当时的样子覆盖当前内容。
        </p>
        {err && <p style={{ color: "var(--danger, #c0392b)", fontSize: 13, margin: "0 0 8px" }}>{err}</p>}

        <div style={{ maxHeight: "52vh", overflowY: "auto" }}>
          {items.map((item) => (
            <div
              key={item.name}
              style={{
                display: "flex", alignItems: "center", gap: 10, padding: "8px 2px",
                borderTop: "1px solid var(--border)",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13 }}>{item.created_at}</div>
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  {reasonLabel(item.reason)} · {item.message_count} 条 · {sizeLabel(item.bytes)}
                </div>
              </div>
              <button className="btn" disabled={loading || restoring} onClick={() => setTarget(item)}>
                恢复
              </button>
            </div>
          ))}
          {!loading && items.length === 0 && (
            <p style={{ fontSize: 12, color: "var(--text-muted)", padding: "10px 0" }}>
              这个会话还没有归档——只有发生过清除/压缩之后，这里才会出现可恢复的条目。
            </p>
          )}
        </div>

        <div className="modal-actions">
          <button className="btn" disabled={restoring} onClick={onClose}>关闭</button>
          <button className="btn" disabled={loading || restoring} onClick={() => { void load(); }}>刷新</button>
        </div>
      </div>
    </div>
  );
}
