import { useEffect, useState } from "react";
import { Power, RotateCw, RefreshCw } from "lucide-react";
import { PageShell, StateHint } from "../components/layout/PageShell";
import { ConfirmModal } from "../components/Modal";
import { useSettings } from "../stores/settings";
import { comfyStatus, stopComfy, restartComfy } from "../api/comfyui";
import { InstalledTab } from "./nodeManager/InstalledTab";
import { MarketTab } from "./nodeManager/MarketTab";
import { WorkflowScanTab } from "./nodeManager/WorkflowScanTab";
import { ComfyUpdateTab } from "./nodeManager/ComfyUpdateTab";

type Tab = "comfy" | "installed" | "market" | "scan";
const TABS: { key: Tab; label: string }[] = [
  { key: "comfy", label: "ComfyUI 更新" },
  { key: "installed", label: "插件节点更新" },
  { key: "market", label: "官方插件市场" },
  { key: "scan", label: "工作流识别安装" },
];

export function NodeManagerView({ initialSearch = "", onSearchConsumed }: {
  initialSearch?: string; onSearchConsumed?: () => void;
} = {}) {
  const { settings } = useSettings();
  const url = settings.comfyuiUrl;
  // 从 AI 搭工作流「去安装」跳来时带搜索词：自动切到市场 tab
  const [tab, setTab] = useState<Tab>(initialSearch ? "market" : "installed");
  const [running, setRunning] = useState<boolean | null>(null);
  const [busy, setBusy] = useState("");
  const [confirm, setConfirm] = useState<null | "stop" | "restart">(null);
  const [restartedAt, setRestartedAt] = useState(0);  // ComfyUI 重启完成时间戳，通知 InstalledTab 重新检查更新（治更新+重启后状态不实时）

  const checkStatus = () => {
    comfyStatus(url).then((s) => setRunning(s.running)).catch(() => setRunning(false));
  };
  useEffect(checkStatus, [url]); // eslint-disable-line react-hooks/exhaustive-deps

  // 轮询状态直到 running（整合包首次加载一堆自定义节点要 30-60s，一次性检查会误判“未启动”）
  const pollUntilUp = async (label: string, timeoutMs = 120000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
      await new Promise((r) => setTimeout(r, 2500));
      let up = false;
      try { up = (await comfyStatus(url)).running; } catch { up = false; }
      if (up) { setRunning(true); setBusy(`${label}完成，ComfyUI 已在运行。`); setRestartedAt(Date.now()); return; }
      const sec = Math.round((Date.now() - t0) / 1000);
      setBusy(`${label}中，ComfyUI 正在初始化（首次较慢，已 ${sec}s）…`);
    }
    checkStatus();
    setBusy(`${label}已提交，但 ComfyUI 超过 2 分钟仍未就绪。可点刷新再确认，或查看 comfyui.log。`);
  };

  const doStop = async () => {
    setConfirm(null);
    setBusy("正在关闭 ComfyUI…");
    try {
      const r = await stopComfy(url, settings.comfyuiPath);
      setBusy(r.message);
    } catch (e) {
      setBusy(`关闭失败：${(e as Error).message}`);
    }
    setTimeout(checkStatus, 1500);
  };

  const doRestart = async () => {
    setConfirm(null);
    if (!settings.comfyuiPath) { setBusy("未配置 ComfyUI 目录，无法自动启动（设置 → 路径）。"); return; }
    // 关闭态点这里等价于“启动”，运行态则是“重启”
    const label = running ? "重启" : "启动";
    setBusy(`正在${label} ComfyUI（先关后起，首次较慢）…`);
    try {
      await restartComfy(settings.comfyuiPath, url, settings.comfyuiPython);
    } catch (e) {
      setBusy(`${label}失败：${(e as Error).message}`);
      return;
    }
    await pollUntilUp(label);
  };

  return (
    <PageShell
      title="节点管理"
      actions={
        <>
          <span style={{ fontSize: 12, color: running ? "var(--success)" : "var(--text-muted)", marginRight: 4 }}>
            {running === null ? "检测中…" : running ? "● ComfyUI 运行中" : "○ ComfyUI 未运行"}
          </span>
          <button className="btn" onClick={checkStatus} title="刷新状态">
            <RefreshCw size={15} />
          </button>
          <button className="btn" disabled={!running} onClick={() => setConfirm("stop")}>
            <Power size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />关闭
          </button>
          <button className="btn" onClick={() => setConfirm("restart")}>
            <RotateCw size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />{running ? "重启" : "启动"}
          </button>
        </>
      }
      toolbar={
        <div className="node-tabs">
          {TABS.map((t) => (
            <button key={t.key} className={`node-tab ${tab === t.key ? "active" : ""}`} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>
      }
    >
      <p style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 0 }}>
        装/更新/卸载插件后，多数需要关闭并重启 ComfyUI 才能生效。可用右上角按钮操作。
      </p>
      {busy && <p style={{ fontSize: 13, color: "var(--text-muted)" }}>{busy}</p>}

      {running === false && tab !== "comfy" ? (
        <StateHint kind="error">ComfyUI 未运行，节点管理需要 ComfyUI 在线。请先启动或重启。</StateHint>
      ) : (
        <>
          {tab === "comfy" && <ComfyUpdateTab url={url} />}
          {tab === "installed" && <InstalledTab url={url} restartedAt={restartedAt} />}
          {tab === "market" && <MarketTab url={url} initialSearch={initialSearch} onSearchConsumed={onSearchConsumed} />}
          {tab === "scan" && <WorkflowScanTab url={url} />}
        </>
      )}

      {confirm === "stop" && (
        <ConfirmModal
          title="关闭 ComfyUI"
          message="将结束 ComfyUI 进程，正在进行的生图会中断。确认关闭？"
          confirmText="关闭" danger
          onConfirm={doStop} onCancel={() => setConfirm(null)}
        />
      )}
      {confirm === "restart" && (
        <ConfirmModal
          title={running ? "重启 ComfyUI" : "启动 ComfyUI"}
          message={running
            ? "将先关闭再重新启动 ComfyUI（用于让新装的插件/依赖生效），进行中的生图会中断。确认重启？"
            : "将启动 ComfyUI（首次加载自定义节点较慢，可能需 30-60 秒）。确认启动？"}
          confirmText={running ? "重启" : "启动"} danger={!!running}
          onConfirm={doRestart} onCancel={() => setConfirm(null)}
        />
      )}
    </PageShell>
  );
}
