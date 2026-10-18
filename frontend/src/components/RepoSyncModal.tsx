import { useEffect, useState } from "react";
import { RefreshCw, Wrench } from "lucide-react";
import {
  repoSyncReport, repoSyncFix, type RepoSyncReport, type Embed,
} from "../api/ai";

/** 仓库同步弹窗（P3 ⑤，2026-10-18）：先只读体检、再一键修复。
 *  对账「注册表 / 磁盘 / 索引」三方——补录磁盘有索引无的文件、清理索引有磁盘无的裂图/孤儿。
 *  修复保守：不删磁盘文件、不覆盖已有索引、软删（回收站）过的文件不重复补录。 */
export function RepoSyncModal({
  outputDir, embed, onClose, onFixed,
}: {
  outputDir: string;
  embed: Embed;
  onClose: () => void;
  onFixed?: () => void;
}) {
  const [report, setReport] = useState<RepoSyncReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [fixing, setFixing] = useState(false);
  const [err, setErr] = useState("");

  const load = async () => {
    setLoading(true);
    setErr("");
    try {
      setReport(await repoSyncReport(outputDir, embed));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(); /* 挂载即体检 */ }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  const doFix = async () => {
    setFixing(true);
    setErr("");
    try {
      const r = await repoSyncFix(outputDir, embed);
      onFixed?.();
      await load();  // 修复后重跑体检，回显最新状态
      // 结果并到错误位展示（绿字效果用 err 反串不优雅，这里直接 toast 更佳，但弹窗内先用文字）
      setErr(`已修复：补录 ${r.indexed} 个文件、清理 ${r.pruned} 条失效记录。`);
    } catch (e) {
      setErr(`修复失败：${(e as Error).message}`);
    } finally {
      setFixing(false);
    }
  };

  const drift = (report?.repos || []).filter(
    (r) => r.unindexed.length > 0 || r.missing.length > 0 || !r.folder_exists,
  );
  const deadRepos = drift.filter((r) => !r.folder_exists);
  const hasDrift = (report?.total_unindexed || 0) + (report?.total_missing || 0)
    + (report?.orphan_index_repos.length || 0) > 0;

  return (
    <div className="modal-mask">
      <div className="modal" style={{ maxWidth: 640, width: "92vw", maxHeight: "86vh", display: "flex", flexDirection: "column" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          <RefreshCw size={16} />
          <h3 style={{ margin: 0, fontSize: 15 }}>仓库同步体检</h3>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>对账「注册表 / 磁盘 / 索引」三方</span>
        </div>

        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", marginTop: 12, fontSize: 13 }}>
          {loading && <div style={{ color: "var(--text-muted)" }}>正在扫描磁盘与索引…</div>}
          {!loading && report && (
            <>
              <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 12 }}>
                <Stat n={report.total_unindexed} label="待补录（磁盘有/索引无）" />
                <Stat n={report.total_missing} label="裂图（索引有/磁盘无）" />
                <Stat n={report.orphan_index_repos.length} label="孤儿索引仓库" />
                <Stat n={deadRepos.length} label="文件夹已没的注册仓库" />
              </div>

              {!hasDrift && <div style={{ color: "var(--text-muted)" }}>三方一致，没有发现漂移。</div>}

              {drift.length > 0 && (
                <div style={{ marginTop: 4 }}>
                  <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 6 }}>有漂移的仓库：</div>
                  {drift.slice(0, 40).map((r) => (
                    <div key={r.repo_id} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", borderBottom: "1px solid var(--border)" }}>
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 260 }}>
                        {r.name}{!r.folder_exists && <span style={{ color: "var(--danger, #c00)" }}>（文件夹已删）</span>}
                      </span>
                      <span style={{ color: "var(--text-muted)", whiteSpace: "nowrap" }}>
                        盘{r.disk}/索引{r.indexed}
                        {r.unindexed.length > 0 && <b style={{ color: "var(--accent, #06c)" }}> +补录{r.unindexed.length}</b>}
                        {r.missing.length > 0 && <b style={{ color: "var(--danger, #c00)" }}> 裂图{r.missing.length}</b>}
                      </span>
                    </div>
                  ))}
                  {drift.length > 40 && <div style={{ fontSize: 12, color: "var(--text-muted)" }}>…等共 {drift.length} 个</div>}
                </div>
              )}

              {report.orphan_index_repos.length > 0 && (
                <div style={{ marginTop: 8, fontSize: 12, color: "var(--text-muted)" }}>
                  孤儿索引仓库：{report.orphan_index_repos.join("、")}
                </div>
              )}

              <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-muted)", lineHeight: 1.6 }}>
                修复会很保守：只「补录从没进过索引的文件」＋「清理磁盘已没了的裂图/孤儿记录」；
                <b>不删你的磁盘文件、不覆盖已有索引、回收站里软删过的文件不会重复补录</b>。
              </div>
            </>
          )}
          {err && <div style={{ marginTop: 10, fontSize: 12.5, color: err.startsWith("已修复") ? "var(--accent, #06c)" : "var(--danger, #c00)" }}>{err}</div>}
        </div>

        <div className="modal-actions" style={{ flexShrink: 0 }}>
          <button className="btn" onClick={onClose} style={{ marginRight: "auto" }}>关闭</button>
          <button className="btn" onClick={() => void load()} disabled={loading || fixing}>重新体检</button>
          <button className="btn primary" disabled={fixing || loading || !hasDrift} onClick={() => void doFix()}>
            <Wrench size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />
            {fixing ? "修复中…" : "一键修复"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Stat({ n, label }: { n: number; label: string }) {
  return (
    <div style={{ minWidth: 120 }}>
      <div style={{ fontSize: 20, fontWeight: 600, color: n > 0 ? "var(--accent, #06c)" : "var(--text-muted)" }}>{n}</div>
      <div style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{label}</div>
    </div>
  );
}
