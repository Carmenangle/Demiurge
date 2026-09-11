import type { UpdateProgress } from "../../api/nodeManager";
import { humanBytes, phaseLabel } from "./useUpdateProgress";

// 更新进度面板：进度条 + 已下载体积/速度 + 依赖清单（含每个文件体积）。
// 原先只有「处理中 x/y」，用户看不出到底在下什么、下了多少、有没有卡住。
export function UpdateProgressPanel({
  prog, onConfirmSensitive, onSkipDeps,
}: {
  prog: UpdateProgress;
  onConfirmSensitive?: () => void;
  onSkipDeps?: () => void;
}) {
  const pct = Math.max(0, Math.min(100, prog.percent));
  // 依赖阶段没有总量可依，用「已下多少个包」代替百分比，避免假装知道进度
  const indeterminate = prog.phase === "deps" || prog.phase === "deps-install"
    || prog.phase === "preflight";

  return (
    <div className="upd-panel">
      <div className="upd-head">
        <strong>{prog.subject ? `${prog.subject} · ` : ""}{phaseLabel(prog) || "处理中"}</strong>
        {prog.note && <span className="upd-note">{prog.note}</span>}
      </div>
      {prog.target_path && <div className="upd-target" title={prog.target_path}>目标：{prog.target_path}</div>}

      {!indeterminate && prog.phase !== "done" && (
        <>
          <div className="upd-bar">
            <div className="upd-bar-fill" style={{ width: `${pct}%` }} />
          </div>
          <div className="upd-stats">
            <span>{pct}%</span>
            {prog.objects_total > 0 && (
              <span>{prog.objects_done}/{prog.objects_total} 对象</span>
            )}
            {prog.received_bytes > 0 && <span>已下 {humanBytes(prog.received_bytes)}</span>}
            {prog.speed_bps > 0 && <span>{humanBytes(prog.speed_bps)}/s</span>}
          </div>
        </>
      )}

      {indeterminate && (
        <div className="upd-bar">
          <div className="upd-bar-fill upd-bar-pulse" />
        </div>
      )}

      {prog.deps.length > 0 && (
        <details className="upd-deps" open>
          <summary>
            依赖 {prog.deps.length} 个，共 {humanBytes(prog.deps_total_bytes)}
          </summary>
          <ul>
            {prog.deps.map((d, i) => (
              <li key={`${d.file}-${i}`}>
                <span className="upd-dep-file">{d.file}</span>
                <span className="upd-dep-size">
                  {humanBytes(d.bytes)}{d.cached ? "（缓存）" : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {prog.pending_sensitive.length > 0 && (
        <div className="upd-warn">
          <p>
            这次更新要改动 {prog.pending_sensitive.length} 个共享依赖。这类改动会影响
            其他插件（典型症状：量化模型/CLIP 突然加载不了），所以先停下来让你决定：
          </p>
          <ul>
            {prog.pending_sensitive.map((s) => <li key={s}><code>{s}</code></li>)}
          </ul>
          <div className="upd-warn-actions">
            {onSkipDeps && (
              <button className="btn" onClick={onSkipDeps}>只更代码，不动依赖</button>
            )}
            {onConfirmSensitive && (
              <button className="btn danger" onClick={onConfirmSensitive}>
                我知道风险，继续安装依赖
              </button>
            )}
          </div>
        </div>
      )}

      {prog.finished && prog.error && <p className="upd-error">{prog.error}</p>}
      {prog.finished && prog.message && !prog.error && (
        <p className={prog.changed ? "upd-ok" : "upd-nochange"}>{prog.message}</p>
      )}
      {prog.old && prog.new && (
        <p className="upd-commits">
          {prog.old} → {prog.new}
          {!prog.changed && prog.finished && "（未变）"}
        </p>
      )}
    </div>
  );
}
