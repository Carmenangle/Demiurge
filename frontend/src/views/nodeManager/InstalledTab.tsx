import { useEffect, useState } from "react";
import { RefreshCw, Trash2, ArrowUpCircle } from "lucide-react";
import { StateHint } from "../../components/layout/PageShell";
import { ConfirmModal } from "../../components/Modal";
import {
  listInstalled, checkUpdatesGit, updateNode, uninstallNode, startQueue,
  startTrackedUpdate, type NodePack, type UpdateProgress,
} from "../../api/nodeManager";
import { useQueueProgress } from "./useQueueProgress";
import { useUpdateProgress } from "./useUpdateProgress";
import { UpdateProgressPanel } from "./UpdateProgressPanel";
import { useSettings } from "../../stores/settings";

// 本进程是否已自动检查过更新：模块级，跨组件挂载/卸载持续，仅刷新页面/重启进程才重置。
// 保证「自动检查」只在进程首次进入插件页时跑一次，频繁切进切出不重复触发慢检查。
let autoCheckedThisSession = false;

// 上次检查更新的结果（模块级持久，跨挂载保留；仅刷新/重启进程才清）。键为包 id：
// updatable=各包是否有更新，failedIds=拉取失败的包，restartIds=已更新待重启的包。
// 再次进入插件页时用它覆盖新拉的列表，保持上次检查结果，不回退到 Manager 不可信的旧值。
let lastCheck: { updatable: Record<string, boolean>; failedIds: string[]; restartIds: string[] } | null = null;

// 插件节点更新：已装插件表 + 检查更新 + 单个更新/卸载。
// 版本/日期列对齐图1启动器：nightly（git-HEAD 装的）显示短哈希+真实提交日期，非Git仓库明确标注。
export function InstalledTab({ url, restartedAt = 0 }: { url: string; restartedAt?: number }) {
  const { settings } = useSettings();
  const path = settings.comfyuiPath;
  const [items, setItems] = useState<NodePack[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState("");
  const [checking, setChecking] = useState(false);
  const [confirm, setConfirm] = useState<null | { act: "update" | "uninstall"; pack: NodePack }>(null);
  // 已更新待重启的包 id：更新后代码要重启 ComfyUI 才加载，此前状态显示「更新重启」，不误判最新/有更新
  const [pendingRestart, setPendingRestart] = useState<Set<string>>(new Set());
  // 检查更新时 git fetch 失败的包 id：状态显示「检查失败」，不假装"最新"
  const [checkFailed, setCheckFailed] = useState<Set<string>>(new Set());
  const { prog, track, setResult } = useQueueProgress(url);
  // 自建更新（git 直连 + 真实进度 + 结果校验）。Manager 队列只作非 git 包的兜底。
  const upd = useUpdateProgress();
  // 停在「等确认改动共享依赖」的包，供两个后续动作（继续装 / 只更代码）复用
  const [awaitingPack, setAwaitingPack] = useState<NodePack | null>(null);

  // 首次加载：显示全屏「读取中」占位
  const load = () => {
    setLoading(true);
    listInstalled(url, path).then((r) => setItems(r.items)).catch(() => setItems([])).finally(() => setLoading(false));
  };
  // 进页面：先快速拉列表显示；仅本进程首次进入时再自动跑一次检查更新（后台刷新真实 updatable）。
  // 之后再进不重复触发慢检查，想查手动点「检查全部更新」。
  useEffect(() => {
    setLoading(true);
    listInstalled(url, path)
      .then((r) => setItems(applyLastCheck(r.items)))  // 已检查过则用缓存覆盖，保持上次结果
      .catch(() => setItems([]))
      .finally(() => {
        setLoading(false);
        // 仅本进程首次进入且从未检查过时才自动跑；之后进入直接用缓存，不重复慢检查。
        if (!autoCheckedThisSession && !lastCheck) { autoCheckedThisSession = true; checkUpdates(); }
      });
  }, [url, path]); // eslint-disable-line react-hooks/exhaustive-deps

  // ComfyUI 重启完成后：清「更新重启」态 + 重新做真实 git 检查（此刻新代码已加载，比对结果才准确）。
  // 治「更新+重启后状态不实时、仍用早期检查记录」——restartedAt 变化即触发。
  useEffect(() => {
    if (!restartedAt) return;  // 初始 0 不触发
    setPendingRestart(new Set());
    if (lastCheck) lastCheck.restartIds = [];
    checkUpdates();
  }, [restartedAt]); // eslint-disable-line react-hooks/exhaustive-deps

  // 用上次检查缓存覆盖列表的 updatable，并恢复 failed/restart 标记（重挂载后保持结果）
  const applyLastCheck = (list: NodePack[]) => {
    if (!lastCheck) return list;
    const { updatable, failedIds, restartIds } = lastCheck;
    setCheckFailed(new Set(failedIds));
    setPendingRestart(new Set(restartIds));
    return list.map((p) => (p.id in updatable ? { ...p, updatable: updatable[p.id] } : { ...p, updatable: false }));
  };

  // 检查更新：走自建 git fetch（带设置里的代理），绕开 Manager 原生检查（不走代理、常超时）。
  // 先拉列表拿到每个包的本地目录名，再用 check-updates-git 的结果按目录名覆盖 updatable。
  const checkUpdates = async () => {
    if (!path) { setBusy("未配置 ComfyUI 目录（设置 → 路径），无法自建检查更新。"); return; }
    setChecking(true);
    setBusy("正在对所有插件做 git 远程比对（带代理，可能耗时 1-3 分钟，请稍候）…");
    const proxy = settings.proxyEnabled ? settings.proxyUrl : "";
    try {
      const [list, chk] = await Promise.all([
        listInstalled(url, path),
        checkUpdatesGit(path, proxy),
      ]);
      // updatable 完全以自建检查为准，不再保留 Manager 的旧值（Manager getlist(skip_update) 没做
      // 远程检查，旧 updatable 不可信——正是「非Git却还挂更新按钮」的根因）。
      // 检查到的按结果；fetch 失败的记入 checkFailed（状态显「检查失败」，不假装最新）。
      const upd = chk.updatable || {};
      const failedDirs = new Set((chk.failed || []).map((d) => d.toLowerCase()));
      const failedIds = new Set<string>();
      const merged = list.items.map((p) => {
        const key = (p.dir || "").toLowerCase();
        if (key && key in upd) return { ...p, updatable: upd[key] };   // 检查到：以结果为准
        if (key && failedDirs.has(key)) { failedIds.add(p.id); return { ...p, updatable: false }; }
        return { ...p, updatable: false };  // 非git/未覆盖：一律无更新，清掉不可信的旧值
      });
      setItems(merged);
      setCheckFailed(failedIds);
      setPendingRestart(new Set());  // 重新检查=以最新远程为准，清掉待重启标记
      // 写入模块级缓存：下次进入插件页覆盖回去，保持本次结果
      lastCheck = {
        updatable: Object.fromEntries(merged.map((p) => [p.id, !!p.updatable])),
        failedIds: [...failedIds],
        restartIds: [],
      };
      const failN = failedIds.size;
      setBusy(failN > 0
        ? `检查完成：已比对 ${chk.checked} 个 git 包，其中 ${failN} 个拉取失败（网络/无远程），可重试。`
        : `检查完成：已比对 ${chk.checked} 个 git 包。`);
    } catch (e) {
      setBusy(`检查失败：${(e as Error).message}`);
    } finally {
      setChecking(false);
    }
  };

  const runAction = async () => {
    if (!confirm) return;
    const { act, pack } = confirm;
    setConfirm(null);
    setBusy("");
    // 只要是 git 装的就走自建更新：git pull 能核对 HEAD，Manager 队列不报每个任务成败。
    // 不再限定 nightly —— 你环境里 55 个 git 包有 54 个带上游追踪分支，都能这么更。
    const canGit = act === "update" && pack.is_git === true && !!path;
    try {
      if (canGit) {
        await runTrackedUpdate(pack);
        return;
      }
      if (act === "update") await updateNode(url, pack);
      else await uninstallNode(url, pack);
      await startQueue(url);
      const label = act === "update" ? "更新" : "卸载";
      // 非 git 包只能走 Manager 队列，而它不报每个任务成败 —— 所以这里的文案必须
      // 说「已提交」而不是「已更新」，并提示用户自行核对，不能替 Manager 打包票。
      track(`${label}「${pack.title}」`, act === "update"
        ? () => {
            markRestart(pack.id);
            setResult(`「${pack.title}」的更新已提交给 ComfyUI-Manager。`
              + "它不回报单个任务的成败，所以结果需要重启后自行确认（这个插件不是 git 安装的，无法核对提交号）。");
          }
        : () => { removePack(pack.id); setResult(`「${pack.title}」已卸载，重启 ComfyUI 后生效。`); });
    } catch (e) {
      setBusy(`操作失败：${(e as Error).message}`);
    }
  };

  // 自建更新：起后台任务 + 轮询进度 + 按 changed 判定真实结果
  const runTrackedUpdate = async (
    pack: NodePack, opts: { allowSensitive?: boolean; skipDeps?: boolean } = {},
  ) => {
    setBusy("");
    setAwaitingPack(null);
    const proxy = settings.proxyEnabled ? settings.proxyUrl : "";
    const r = await startTrackedUpdate(path, pack, {
      proxy, pythonExe: settings.comfyuiPython || "",
      allowSensitive: opts.allowSensitive, skipDeps: opts.skipDeps,
    });
    if (r.already_running) {
      setBusy("已有一个更新任务在跑，请等它结束。");
      return;
    }
    upd.track((p: UpdateProgress) => {
      if (p.pending_sensitive.length > 0) {
        setAwaitingPack(pack);       // 停在确认态，两个按钮接着走
        return;
      }
      // 只有后端核对 HEAD 确认变了才标「待重启」；没变就把 updatable 归零但不谎报更新
      if (p.changed) markRestart(pack.id);
      else if (!p.error) markLatest(pack.id);
    });
  };

  // 就地更新单个包状态，不重拉整表（重拉会丢失自建检查覆盖的 updatable）；同步写缓存保持跨挂载
  const markRestart = (id: string) => {
    setPendingRestart((s) => new Set(s).add(id));
    setItems((arr) => arr.map((p) => (p.id === id ? { ...p, updatable: false } : p)));
    if (lastCheck) { lastCheck.updatable[id] = false; if (!lastCheck.restartIds.includes(id)) lastCheck.restartIds.push(id); }
  };
  const markLatest = (id: string) => {
    setItems((arr) => arr.map((p) => (p.id === id ? { ...p, updatable: false } : p)));
    if (lastCheck) lastCheck.updatable[id] = false;
  };
  const removePack = (id: string) => {
    setItems((arr) => arr.filter((p) => p.id !== id));
    if (lastCheck) { delete lastCheck.updatable[id]; lastCheck.restartIds = lastCheck.restartIds.filter((x) => x !== id); }
  };

  const kw = q.trim().toLowerCase();
  const shown = kw ? items.filter((p) => (p.title + p.id + p.repository).toLowerCase().includes(kw)) : items;

  if (loading) return <StateHint>读取已装插件…</StateHint>;

  return (
    <div>
      <div className="page-toolbar">
        <input placeholder="搜索插件名…" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 280 }} />
        <button className="btn" onClick={checkUpdates} disabled={checking}>
          <RefreshCw size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          {checking ? "检查中…" : "检查全部更新"}
        </button>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>共 {items.length} 个已装插件</span>
      </div>
      {upd.prog && (
        <UpdateProgressPanel
          prog={upd.prog}
          onConfirmSensitive={awaitingPack
            ? () => void runTrackedUpdate(awaitingPack, { allowSensitive: true })
            : undefined}
          onSkipDeps={awaitingPack
            ? () => void runTrackedUpdate(awaitingPack, { skipDeps: true })
            : undefined}
        />
      )}
      {prog.text && (
        <div className="build-result ok" style={{ marginBottom: 10 }}>
          {prog.active && <span className="bot-spinner" style={{ marginRight: 8 }} />}
          {prog.text}
        </div>
      )}
      {busy && <p style={{ fontSize: 13, color: "var(--text-muted)" }}>{busy}</p>}

      <div className="node-table">
        <div className="node-row node-row-head">
          <span>插件名</span><span>版本</span><span>更新日期</span><span>状态</span><span>操作</span>
        </div>
        {shown.map((p) => {
          const nonGit = p.is_git === false; // 磁盘上确实不是 git 仓库
          // 版本列：nightly（git-HEAD 装的）显示短哈希更有信息量；非Git明确标注；否则原样
          const verText = nonGit ? "非Git仓库" : (p.version === "nightly" && p.commit ? p.commit : (p.version || "—"));
          const dateText = p.git_date || (typeof p.last_update === "string" && p.last_update ? p.last_update.slice(0, 10) : "—");
          return (
          <div className="node-row" key={p.id}>
            <span className="node-name" title={p.repository}>
              <a href={p.repository} target="_blank" rel="noreferrer">{p.title}</a>
            </span>
            <span title={p.version === "nightly" && p.commit ? "开发版（git HEAD）" : ""}>{verText}</span>
            <span>{dateText}</span>
            {(() => {
              const failed = checkFailed.has(p.id);
              const restarting = pendingRestart.has(p.id);
              const cls = nonGit ? "node-state-ok"
                : restarting ? "node-state-upd"
                : failed ? "node-state-warn"
                : p.updatable ? "node-state-upd" : "node-state-ok";
              const txt = nonGit ? "无匹配"
                : restarting ? "更新重启"
                : failed ? "检查失败"
                : p.updatable ? "有更新" : "最新";
              return <span className={cls}>{txt}</span>;
            })()}
            <span className="node-ops">
              {p.updatable && !pendingRestart.has(p.id) && !nonGit && !checkFailed.has(p.id) && (
                <button className="icon-btn" title="更新" onClick={() => setConfirm({ act: "update", pack: p })}>
                  <ArrowUpCircle size={15} />
                </button>
              )}
              <button className="icon-btn" title="卸载" onClick={() => setConfirm({ act: "uninstall", pack: p })}>
                <Trash2 size={15} />
              </button>
            </span>
          </div>
          );
        })}
        {shown.length === 0 && <StateHint>没有匹配的插件。</StateHint>}
      </div>

      {confirm && (
        <ConfirmModal
          title={confirm.act === "update" ? "更新插件" : "卸载插件"}
          message={`确认${confirm.act === "update" ? "更新" : "卸载"}「${confirm.pack.title}」？操作后需重启 ComfyUI 生效。`}
          confirmText={confirm.act === "update" ? "更新" : "卸载"}
          danger={confirm.act === "uninstall"}
          onConfirm={runAction}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  );
}
