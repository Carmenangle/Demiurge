import { useCallback, useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import {
  listNarrativeCi,
  NARRATIVE_CI_ARM_LABELS,
  NARRATIVE_CI_ARM_ORDER,
  NARRATIVE_CI_ARM_REASONS,
  NARRATIVE_CI_CODES,
  resolveNarrativeCi,
  type NarrativeCiArmStatus,
  type NarrativeCiArmsReport,
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

/** 判据参与台账（清单 #3／#4＋缺陷 4）：本轮没输入的判据**根本没跑**，必须写在空列表前面。
 *
 * 臂名白名单来自 `NARRATIVE_CI_ARM_ORDER`（后端加一臂就得在这里登记，否则界面看不见）。
 * 单独导出是为了让「界面说了什么」可测——只测 service 会漏掉「面板拿不到 arms」这一层。
 */
export function CiArmLedger({ arms, hasItems }: {
  arms: NarrativeCiArmsReport | null;
  hasItems: boolean;
}) {
  const rows: { key: string; label: string; arm: NarrativeCiArmStatus }[] = [];
  for (const key of NARRATIVE_CI_ARM_ORDER) {
    const arm = arms?.arms?.[key];
    if (arm) rows.push({ key, label: NARRATIVE_CI_ARM_LABELS[key] ?? key, arm });
  }
  const idleCount = rows.filter((row) => !row.arm.participated).length;

  return (
    <>
      {rows.length > 0 && (
        <div className="nci-arms">
          <div className="nci-arms-head">第 {arms?.turn ?? 0} 轮判据参与情况</div>
          {rows.map((row) => (
            <div
              key={row.key}
              className={`nci-arm ${row.arm.participated ? "nci-arm-on" : "nci-arm-idle"}`}
            >
              <span className="nci-arm-name">{row.label}</span>
              <span className="nci-arm-state">
                {row.arm.participated ? `已评估（输入 ${row.arm.count} 条）` : "本轮未参与"}
              </span>
              {!row.arm.participated && (
                <span className="nci-arm-reason">
                  {NARRATIVE_CI_ARM_REASONS[row.arm.reason] ?? row.arm.reason}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
      {!hasItems && (
        <div className="nci-empty">
          {idleCount > 0
            ? "暂无未处置的一致性诊断（上面标出的判据本轮没有输入，未参与＝没查，不等于查过没问题）。"
            : "暂无未处置的一致性诊断。"}
        </div>
      )}
    </>
  );
}

/** 非阻断 Narrative CI 诊断面板：不修改正文，只展示与处置。 */
export function NarrativeCiPanel({ outputDir, repoId }: NarrativeCiPanelProps) {
  const [items, setItems] = useState<NarrativeDiagnostic[]>([]);
  const [arms, setArms] = useState<NarrativeCiArmsReport | null>(null);
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
      setArms(result.arms ?? null);
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
        <span className="head-tool-text">诊断</span>
        {openCount > 0 && <span className="nci-count">{openCount}</span>}
      </button>

      {open && (
        <div className="nci-body">
          {error && <div className="nci-error">{error}</div>}
          {loading && <div className="nci-loading">加载中…</div>}
          {!loading && <CiArmLedger arms={arms} hasItems={items.length > 0} />}
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
