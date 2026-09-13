// Visual CI 诊断徽章与展开面板。
// 展示在插画图片下方，折叠时只显示 verdict 徽章，展开后显示详细诊断信息。
import {
  type VisualCiDiagnostic,
  visualCiVerdictClass,
  visualCiVerdictLabel,
  visualCiStatusLabel,
} from "../../api/visualCi";

/** Verdict 徽章颜色对应的 SVG 图标（小巧内联 SVG）。 */
function VerdictDot({ verdict }: { verdict: VisualCiDiagnostic["verdict"] }) {
  const color =
    verdict === "green" ? "#22c55e" :
    verdict === "amber" ? "#f59e0b" :
    verdict === "red"   ? "#ef4444" :
    "#94a3b8";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
      <svg width="10" height="10" viewBox="0 0 10 10">
        <circle cx="5" cy="5" r="4.5" fill={color} />
      </svg>
    </span>
  );
}

/** Verdict 大色块标签。 */
function VerdictBadge({ verdict }: { verdict: VisualCiDiagnostic["verdict"] }) {
  const cls = visualCiVerdictClass(verdict);
  const label = visualCiVerdictLabel(verdict);
  return (
    <span className={`vc-badge vc-badge-${cls}`}>
      <VerdictDot verdict={verdict} />
      {label}
    </span>
  );
}

/** 字段账本单行。 */
function FieldRow({ field, vlmSkipped }: { field: VisualCiDiagnostic["field_ledger"][0]; vlmSkipped?: boolean }) {
  const okIcon = field.vlm_ok === true ? "✅"
               : field.vlm_ok === false ? "❌"
               : field.covered ? "✔" : "○";
  const requiredStar = field.required ? " *" : "";
  return (
    <tr style={{ fontSize: 12 }}>
      <td style={{ color: "#94a3b8", paddingRight: 8, whiteSpace: "nowrap" }}>
        {okIcon}{requiredStar}
      </td>
      <td style={{ fontWeight: 500, paddingRight: 8 }}>{field.name}</td>
      <td style={{ color: field.covered ? "#22c55e" : "#64748b", paddingRight: 8 }}>
        {field.covered ? "covered" : "—"}
      </td>
      <td style={{ color: field.vlm_ok === true ? "#22c55e" : field.vlm_ok === false ? "#ef4444" : "#94a3b8" }}>
        {field.vlm_ok === null
         ? (vlmSkipped ? "未配置VLM" : "VLM未检")
         : field.vlm_ok ? "VLM通过"
         : "VLM失败"}
      </td>
    </tr>
  );
}

/** 机械账本展示。 */
function MechanicalPanel({ m }: { m: VisualCiDiagnostic["mechanical"] }) {
  const items = [
    ["Checkpoint", m.checkpoint || "—"],
    ["Seed", m.seed?.toString() ?? "—"],
    ["Size", m.width && m.height ? `${m.width}×${m.height}` : "—"],
    ["Sampler", m.sampler || "—"],
    ["Steps", m.steps?.toString() ?? "—"],
    ["CFG", m.cfg?.toString() ?? "—"],
  ] as [string, string][];
  if (m.loras?.length) {
    items.push([
      "LoRAs",
      m.loras.map((l: { name: string; weight: number }) => `${l.name}×${l.weight}`).join(", ") || "—",
    ]);
  }
  return (
    <div className="vc-panel">
      <div className="vc-section-title">⚙ 机械事实</div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <tbody>
          {items.map(([k, v]) => (
            <tr key={k}>
              <td style={{ color: "#94a3b8", paddingRight: 12, whiteSpace: "nowrap", width: "40%" }}>
                {k}
              </td>
              <td style={{ color: "#e2e8f0", wordBreak: "break-all" }}>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** VLM 评估结果。 */
function VlmPanel({ v }: { v: VisualCiDiagnostic["vlm"] }) {
  if (!v.model && !v.summary) {
    return (
      <div className="vc-panel">
        <div className="vc-section-title">🖼 VLM 评估</div>
        <div style={{ color: "#64748b", fontSize: 12 }}>未配置 VLM，跳过语义检查</div>
      </div>
    );
  }
  const dimRows = Object.entries(v.dimensions || {});
  return (
    <div className="vc-panel">
      <div className="vc-section-title">
        🖼 VLM 评估
        {v.model && <span style={{ color: "#64748b", marginLeft: 6, fontSize: 11 }}>({v.model})</span>}
      </div>
      {dimRows.length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <tbody>
            {dimRows.map(([k, ok]) => (
              <tr key={k}>
                <td style={{ paddingRight: 8 }}>{ok ? "✅" : "❌"}</td>
                <td>{k}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {v.summary && (
        <div style={{ marginTop: 6, fontSize: 12, color: "#94a3b8", lineHeight: 1.5 }}>
          {v.summary}
        </div>
      )}
    </div>
  );
}

/** 相似度指示器。 */
function SimilarityPanel({ sim }: { sim: number }) {
  const pct = Math.round((sim || 0) * 100);
  const color = pct >= 80 ? "#22c55e" : pct >= 50 ? "#f59e0b" : "#ef4444";
  return (
    <div className="vc-panel">
      <div className="vc-section-title">🔗 角色相似度</div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{
          flex: 1, height: 6, background: "#1e293b", borderRadius: 3, overflow: "hidden",
        }}>
          <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 3, transition: "width .3s" }} />
        </div>
        <span style={{ color, fontSize: 12, fontWeight: 600, minWidth: 36, textAlign: "right" }}>
          {pct}%
        </span>
      </div>
    </div>
  );
}

/** 主徽章组件：折叠/展开诊断面板。 */
export function VisualCiBadge({
  diagnostic,
  loading,
  error,
  expanded,
  onToggleExpanded,
  onRetry,
}: {
  diagnostic: VisualCiDiagnostic | null;
  loading: boolean;
  error: string | null;
  expanded?: boolean;
  onToggleExpanded?: () => void;
  onRetry?: () => void;
}) {
  const isExpanded = !!expanded;
  const toggle = onToggleExpanded ?? (() => {});

  // 完全无数据时不渲染
  if (!diagnostic && !loading && !error) return null;

  // loading 状态
  if (loading && !diagnostic) {
    return (
      <div className="vc-badge-row" style={{ cursor: "default" }}>
        <span className="vc-badge vc-badge-loading">
          <span className="vc-spinner" />
          诊断中…
        </span>
      </div>
    );
  }

  // 错误状态（无诊断数据）
  if (error && !diagnostic) {
    return (
      <div className="vc-badge-row">
        <span className="vc-badge vc-badge-error" title={error}>
          ⚠ 诊断出错
        </span>
      </div>
    );
  }

  const diag = diagnostic!;

  return (
    <div className="vc-badge-row">
      {/* 折叠行：始终显示徽章 + 展开按钮 */}
      <button
        className="vc-expand-toggle"
        onClick={toggle}
        title={isExpanded ? "收起诊断详情" : "查看诊断详情"}
        style={{ background: "none", border: "none", cursor: "pointer", color: "#64748b" }}
      >
        <VerdictBadge verdict={diag.verdict} />
        <span style={{ marginLeft: 4, fontSize: 11, color: "#64748b" }}>
          {isExpanded ? "▲" : "▼"}
        </span>
      </button>

      {/* 展开面板 */}
      {isExpanded && (
        <div className="vc-panel vc-panel-expanded">
          {/* 头部操作行 */}
          <div className="vc-panel-header">
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 11, color: "#64748b" }}>
                {visualCiStatusLabel(diag.status)}
              </span>
              {diag.retry_count > 0 && (
                <span style={{ fontSize: 11, color: "#94a3b8" }}>
                  已重试 {diag.retry_count} 次
                </span>
              )}
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              {diag.verdict === "red" && onRetry && (
                <button
                  className="vc-action-btn vc-action-retry"
                  onClick={onRetry}
                  disabled={diag.status === "retry"}
                >
                  申请重试
                </button>
              )}
              <button
                className="vc-action-btn"
                onClick={toggle}
              >
                收起
              </button>
            </div>
          </div>

          {/* 字段账本（始终展示） */}
          {diag.field_ledger?.length > 0 && (
            <div className="vc-panel">
              <div className="vc-section-title">📋 字段账本</div>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <tbody>
                  {diag.field_ledger.map((f: VisualCiDiagnostic["field_ledger"][0]) => (
                    <FieldRow key={f.name} field={f} vlmSkipped={!!diag.evidence?.vlm_skip} />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* 机械账本 */}
          <MechanicalPanel m={diag.mechanical} />

          {/* VLM 评估 */}
          <VlmPanel v={diag.vlm} />

          {/* 相似度（有参考图时） */}
          {diag.similarity > 0 && (
            <SimilarityPanel sim={diag.similarity} />
          )}

          {/* 时间戳 */}
          {diag.created_at && (
            <div style={{ fontSize: 11, color: "#475569", textAlign: "right", marginTop: 4 }}>
              {new Date(diag.created_at).toLocaleString("zh-CN", { hour12: false })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
