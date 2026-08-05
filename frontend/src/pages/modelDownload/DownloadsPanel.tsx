import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { listDownloads, type DownloadTask } from "../../api/models";

// 跨 tab 共享的下载进度面板：常驻模型下载页顶部，轮询所有下载任务。
// 任一 tab 发起的下载都汇总到这里，可看进度与最终成败（解决“看不到进度/不知成没成”）。
const humanBytes = (value?: number) => {
  const n = value || 0;
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
};
const phaseText: Record<string, string> = {
  queued: "等待开始", resolving: "解析下载地址", downloading: "下载中",
  saving: "保存文件", extracting: "解压工作流", done: "完成", failed: "失败",
};

export function DownloadsPanel() {
  const [tasks, setTasks] = useState<DownloadTask[]>([]);
  const [open, setOpen] = useState(true);

  useEffect(() => {
    let live = true;
    const tick = async () => {
      try {
        const r = await listDownloads();
        if (live) setTasks(r.items);
      } catch { /* 抖动忽略 */ }
    };
    tick();
    const id = setInterval(tick, 1500); // 固定轮询：有任务在跑时进度可见，空闲时开销也很小
    return () => { live = false; clearInterval(id); };
  }, []);

  if (tasks.length === 0) return null;

  const running = tasks.filter((t) => t.status === "pending" || t.status === "downloading").length;

  return (
    <div className="dl-panel">
      <button className="dl-panel-head" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        <span style={{ fontWeight: 600 }}>下载任务</span>
        {running > 0 && (
          <span style={{ color: "var(--accent, #4a90d9)", fontSize: 12, display: "inline-flex", alignItems: "center", gap: 4 }}>
            <Loader2 size={13} className="spin" /> {running} 个进行中
          </span>
        )}
        <span style={{ color: "var(--text-muted)", fontSize: 12 }}>共 {tasks.length} 个</span>
      </button>

      {open && (
        <div className="dl-panel-body">
          {tasks.map((t) => {
            const pct = t.total ? Math.floor((t.downloaded || 0) / t.total * 100) : 0;
            return (
              <div key={t.id} className="dl-item">
                <div className="dl-item-top">
                  <span className="dl-item-name" title={t.filename || t.name}>
                    {t.name || t.filename || t.id}
                    {t.filename && t.name && t.filename !== t.name && (
                      <span style={{ color: "var(--text-muted)", marginLeft: 6, fontSize: 11 }}>{t.filename}</span>
                    )}
                  </span>
                  <span className="dl-item-state">
                    {t.status === "downloading" && (t.total ? `${pct}%` : humanBytes(t.downloaded))}
                    {t.status === "pending" && <span style={{ color: "var(--text-muted)" }}>准备中…</span>}
                    {t.status === "done" && <span style={{ color: "#3c9a5f" }}>✓ 完成</span>}
                    {t.status === "error" && <span style={{ color: "#d9534f" }}>✗ 失败</span>}
                  </span>
                </div>
                {t.status === "downloading" && (
                  <>
                    <div className={`dl-bar ${!t.total ? "indeterminate" : ""}`}>
                      <div className="dl-bar-fill" style={t.total ? { width: `${pct}%` } : undefined} />
                    </div>
                    <div className="dl-item-sub">
                      <span>{phaseText[t.phase || "downloading"] || t.phase}</span>
                      <span>{humanBytes(t.downloaded)} / {t.total ? humanBytes(t.total) : "未知大小"}</span>
                      {!!t.speed_bps && <span>{humanBytes(t.speed_bps)}/s</span>}
                    </div>
                  </>
                )}
                {t.status === "done" && t.saved_files && t.saved_files.length > 0 && (
                  <div className="dl-saved">
                    {t.saved_files.map((file) => <span key={file} title={file}>已保存：{file}</span>)}
                  </div>
                )}
                {t.status === "error" && <div className="dl-item-err">{t.error}</div>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
