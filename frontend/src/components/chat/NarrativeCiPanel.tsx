import { useCallback, useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import {
  listNarrativeCi,
  NARRATIVE_CI_CODES,
  resolveNarrativeCi,
  type NarrativeDiagnostic,
} from "../../api/narrative";

const SEVERITY_LABEL: Record<string, string> = {
  error: "错误",
  warning: "警告",
  info: "提示",
};

const SEVERITY_CLASS: Record<string, string> = {
  error: "nci-sev-error",
  warning: "nci-sev-warning",
  info: "nci-sev-info",
};

const STATUS_LABEL: Record<string, string> = {
  open: "待处置",
  fixed: "已修复",
  foreshadow: "伏笔",
  retcon: "设定变更",
  accepted: "已接受",
};

interface NarrativeCiPanelProps {
  outputDir: string;
  repoId: string;
}

/** 非阻断 Narrative CI 诊断面板：不修改正文，只展示与处置。 */
export function NarrativeCiPanel({ outputDir, repoId }: NarrativeCiPanelProps) {
  const [items, setItems] = useState<NarrativeDiagnostic[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!outputDir || !repoId) return;
    setLoading(true);
    setError("");
    try {
      const result = await listNarrativeCi(outputDir, repoId, "open");
      setItems(result.items ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [outputDir, repoId]);

  useEffect(() => {
    if (open) {
      void load();
    }
  }, [open, load]);

  const handleResolve = async (id: string, status: string) => {
    try {
      await resolveNarrativeCi(outputDir, repoId, id, status);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "处置失败");
    }
  };

  const openCount = items.filter((item) => item.status === "open").length;

  return (
    <div className="nci-panel">
      <button
        type="button"
        className={`btn icon-only nci-toggle ${open ? "nci-active" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="剧情一致性诊断（非阻断）：每回合检查正文矛盾/伏笔断裂/设定漂移，可标记已修复/伏笔/设定变更/接受"
      >
        <ShieldCheck size={15} />
        {openCount > 0 && <span className="nci-count">{openCount}</span>}
      </button>

      {open && (
        <div className="nci-body">
          {error && <div className="nci-error">{error}</div>}
          {loading && <div className="nci-loading">加载中…</div>}
          {!loading && items.length === 0 && (
            <div className="nci-empty">暂无未处置的一致性诊断。</div>
          )}
          {!loading &&
            items.map((item) => (
              <div key={item.id} className={`nci-item ${SEVERITY_CLASS[item.severity] ?? ""}`}>
                <div className="nci-item-head">
                  <span className="nci-sev">{SEVERITY_LABEL[item.severity] ?? item.severity}</span>
                  <span className="nci-code">{NARRATIVE_CI_CODES[item.code] ?? item.code}</span>
                  <span className="nci-turn">回合 {item.turn}</span>
                  <span className="nci-status">{STATUS_LABEL[item.status] ?? item.status}</span>
                </div>
                <div className="nci-message">{item.message}</div>
                {item.evidence && <div className="nci-evidence">{item.evidence}</div>}
                <div className="nci-actions">
                  <button type="button" onClick={() => void handleResolve(item.id, "fixed")}>
                    已修复
                  </button>
                  <button type="button" onClick={() => void handleResolve(item.id, "foreshadow")}>
                    标记伏笔
                  </button>
                  <button type="button" onClick={() => void handleResolve(item.id, "retcon")}>
                    设定变更
                  </button>
                  <button type="button" onClick={() => void handleResolve(item.id, "accepted")}>
                    接受
                  </button>
                </div>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
