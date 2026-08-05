import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { StateHint } from "../../components/layout/PageShell";
import { ConfirmModal } from "../../components/Modal";
import { useSettings } from "../../stores/settings";
import {
  comfyuiGitVersions, checkCoreRequirements, startTrackedComfySwitch,
  startTrackedComfyUpdate, type GitVersion, type CoreRequirements, type UpdateProgress,
} from "../../api/nodeManager";
import { useUpdateProgress } from "./useUpdateProgress";
import { UpdateProgressPanel } from "./UpdateProgressPanel";

// ComfyUI 本体：全量版本列表(读 git tag，带发布日期) + 正式版/开发版 + 切换。
// 正式版 = git tag(vX.Y.Z)；开发版 = nightly(最新 master)。切换后需重启生效。
export function ComfyUpdateTab({ url: _url }: { url: string }) {
  const { settings } = useSettings();
  const path = settings.comfyuiPath;
  const [versions, setVersions] = useState<GitVersion[]>([]);
  const [current, setCurrent] = useState("");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [channel, setChannel] = useState<"stable" | "dev">("stable");
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [pendingVer, setPendingVer] = useState("");  // 切换成功待重启的目标版本
  // 切完版本后核对本体依赖：只换代码不装依赖会留下「新代码 + 旧依赖」的半更新态
  const [coreDeps, setCoreDeps] = useState<CoreRequirements | null>(null);
  const upd = useUpdateProgress();
  const [awaitingCore, setAwaitingCore] = useState(false);

  const updateCurrentBranch = async (opts: { allowSensitive?: boolean; skipDeps?: boolean } = {}) => {
    if (!path) return;
    setBusy(""); setAwaitingCore(false);
    try {
      const r = await startTrackedComfyUpdate(path, {
        pythonExe: settings.comfyuiPython || "",
        proxy: settings.proxyEnabled ? settings.proxyUrl : "",
        allowSensitive: opts.allowSensitive, skipDeps: opts.skipDeps,
      });
      if (r.already_running) { setBusy("已有一个节点或 ComfyUI 维护任务在运行。"); return; }
      upd.track((p: UpdateProgress) => {
        if (p.pending_sensitive.length > 0) setAwaitingCore(true);
        if (p.finished && !p.error) load();
      });
    } catch (e) { setBusy(`更新失败：${(e as Error).message}`); }
  };

  const load = () => {
    if (!path) { setErr("未配置 ComfyUI 目录（设置 → 路径）"); setLoading(false); return; }
    setLoading(true); setErr("");
    comfyuiGitVersions(path)
      .then((r) => { setVersions(r.versions); setCurrent(r.current); })
      .catch((e) => setErr((e as Error).message))
      .finally(() => setLoading(false));
  };
  useEffect(load, [path]); // eslint-disable-line react-hooks/exhaustive-deps

  const doSwitch = async () => {
    setConfirm(false);
    setBusy("");
    const target = selected;
    try {
      const started = await startTrackedComfySwitch(
        path, target, settings.proxyEnabled ? settings.proxyUrl : "",
      );
      if (started.already_running) { setBusy("已有一个节点或 ComfyUI 维护任务在运行。"); return; }
      upd.track(async (progress: UpdateProgress) => {
        if (progress.error) return;
        try {
          const r = await comfyuiGitVersions(path);
          setVersions(r.versions);
          setCurrent(r.current);
          if (r.current === target) {
            setPendingVer(target);
            setBusy(`已切换到 ${target}，重启 ComfyUI 后生效（右上角「重启」）。`);
            // 代码换了但依赖没动，这里核对一次并把缺口摆出来
            checkCoreRequirements(
              path, settings.comfyuiPython || "",
              settings.proxyEnabled ? settings.proxyUrl : "",
            ).then(setCoreDeps).catch(() => setCoreDeps(null));
          } else {
            setBusy(`切换未完成：当前仍为 ${r.current}。请查看上方任务结果。`);
          }
        } catch (e) {
          setBusy(`版本复查失败：${(e as Error).message}。可点「刷新版本列表」确认。`);
        }
      });
    } catch (e) {
      setBusy(`切换失败：${(e as Error).message}`);
    }
  };

  // 正式版 = git tag 全量；开发版 = 单个 nightly 选项
  const stableRows = versions;
  const devRows: GitVersion[] = [{ version: "nightly", date: "最新开发分支" }];
  const rows = channel === "stable" ? stableRows : devRows;

  if (loading) return <StateHint>读取 ComfyUI 版本列表…</StateHint>;
  if (err) return <StateHint kind="error">{err}</StateHint>;

  return (
    <div>
      <p style={{ fontSize: 14, marginTop: 0 }}>
        当前版本：<strong style={{ color: "var(--accent)" }}>{current}</strong>
        <button className="btn" style={{ marginLeft: 12 }} onClick={load}>
          <RefreshCw size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />刷新版本列表
        </button>
        <button className="btn primary" style={{ marginLeft: 8 }} disabled={!!upd.prog?.running}
          onClick={() => void updateCurrentBranch()}>
          {upd.prog?.running ? "更新中…" : "更新当前分支到最新"}
        </button>
      </p>

      {upd.prog && <UpdateProgressPanel prog={upd.prog}
        onConfirmSensitive={awaitingCore ? () => void updateCurrentBranch({ allowSensitive: true }) : undefined}
        onSkipDeps={awaitingCore ? () => void updateCurrentBranch({ skipDeps: true }) : undefined} />}

      <div className="page-toolbar">
        <label style={{ fontSize: 13, cursor: "pointer" }}>
          <input type="radio" checked={channel === "stable"} onChange={() => { setChannel("stable"); setSelected(""); }} /> 正式版
        </label>
        <label style={{ fontSize: 13, cursor: "pointer" }}>
          <input type="radio" checked={channel === "dev"} onChange={() => { setChannel("dev"); setSelected(""); }} /> 开发版
        </label>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>共 {stableRows.length} 个正式版</span>
      </div>

      <div className="node-table" style={{ maxHeight: 420, overflow: "auto" }}>
        <div className="ver-row ver-row-head"><span>版本</span><span>发布日期</span></div>
        {rows.map((v) => (
          <button
            key={v.version}
            className={`ver-row ${selected === v.version ? "sel" : ""}`}
            onClick={() => setSelected(v.version)}
          >
            <span>
              {v.version}
              {v.version === pendingVer
                ? <span style={{ color: "var(--warning)", marginLeft: 8 }}>（待重启）</span>
                : v.version === current && <span style={{ color: "var(--success)", marginLeft: 8 }}>（当前版本）</span>}
            </span>
            <span style={{ color: "var(--text-muted)" }}>{v.date}</span>
          </button>
        ))}
      </div>

      <button
        className="btn primary"
        style={{ marginTop: 12 }}
        disabled={!selected || selected === current || !!upd.prog?.running}
        onClick={() => setConfirm(true)}
      >
        {upd.prog?.running ? "切换中…" : `更新到选中版本${selected ? `（${selected}）` : ""}`}
      </button>
      {coreDeps && !coreDeps.satisfied && (
        <div className="upd-warn" style={{ marginTop: 12 }}>
          <p><strong>依赖没跟上代码。</strong>{coreDeps.note}</p>
          {coreDeps.missing.length > 0 && (
            <ul>
              {coreDeps.missing.slice(0, 12).map((m) => (
                <li key={m.name}><code>{m.name}=={m.version}</code></li>
              ))}
              {coreDeps.missing.length > 12 && (
                <li>…另有 {coreDeps.missing.length - 12} 个</li>
              )}
            </ul>
          )}
          <p style={{ color: "var(--text-muted)" }}>
            这里只做核对、不自动安装 —— 这些是全环境共享的库，动了可能连带影响其他插件。
            要装请在 ComfyUI 目录下手动执行：
            <code style={{ marginLeft: 6 }}>python -m pip install -r requirements.txt</code>
          </p>
        </div>
      )}
      {coreDeps && coreDeps.satisfied && coreDeps.note && (
        <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>{coreDeps.note}</p>
      )}
      {busy && <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 12 }}>{busy}</p>}

      {confirm && (
        <ConfirmModal
          title="切换 ComfyUI 版本"
          message={`将把 ComfyUI 从 ${current} 切换到 ${selected}，可能影响插件兼容性。完成后需重启。确认？`}
          confirmText="切换"
          onConfirm={doSwitch}
          onCancel={() => setConfirm(false)}
        />
      )}
    </div>
  );
}
