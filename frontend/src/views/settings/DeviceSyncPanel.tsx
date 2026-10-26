import { useEffect, useRef, useState } from "react";
import { AlertModal } from "../../components/Modal";
import {
  configBundleExportUrl, getConfigInventory, importConfigBundle, revealConfigBundle,
  saveConfigBundle, type ConfigImportCount, type ConfigImportResult, type ConfigInventory,
  type SavedConfigBundle,
} from "../../api/configTransfer";
import {
  loadMobileRemote, saveMobileRemote, testMobileRemote,
  type MobileRemoteConfig,
} from "../../api/mobileRemote";
import { MOBILE_PROFILE } from "../../lib/mobileProfile";

const ASSET_LABELS: Record<string, string> = {
  characters: "角色卡",
  worldbooks: "世界书",
  presets: "预设",
  workflows: "工作流",
  prompt_templates: "提示词模版",
  repos: "作品仓库",
  regex: "正则脚本",
  web_materials: "上网素材",
};

function sizeText(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function resultText(result: ConfigImportResult): string {
  const counts: Record<string, ConfigImportCount> = {
    ...result.assets,
    repos: result.repos,
    regex: result.regex,
    web_materials: result.web_materials,
  };
  const parts = Object.entries(counts)
    .filter(([, count]) => count.imported > 0 || count.skipped > 0)
    .map(([kind, count]) => {
      const label = ASSET_LABELS[kind] || kind;
      const skipped = count.skipped > 0 ? `（跳过同名 ${count.skipped}）` : "";
      return `${label} ${count.imported}${skipped}`;
    });
  const resources = parts.length > 0 ? `资源：${parts.join("，")}。` : "资源：无新增。";
  const settings = result.settings_updated.length > 0
    ? `已同步设置 ${result.settings_updated.length} 项。`
    : "设置：无变化。";
  const secrets = result.secrets_included
    ? "本次包内含密钥，已一并写入本机。"
    : "本次包内不含密钥，本机已有密钥保持不变。";
  return `${settings}${resources}${secrets}\n点击「刷新页面」后生效。`;
}

interface Notice {
  title: string;
  message: string;
  reload: boolean;
}

/** 手机端专用：联机同库（本机后端把 /api/* 反向代理到 PC，数据真源在电脑）。 */
function RemoteLinkSection({ onActiveChange }: { onActiveChange: (active: boolean) => void }) {
  const [config, setConfig] = useState<MobileRemoteConfig | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadMobileRemote()
      .then((cfg) => { setConfig(cfg); setBaseUrl(cfg.base_url); })
      .catch((reason) => setError(`读取联机配置失败：${(reason as Error).message}`));
  }, []);

  const active = Boolean(config?.active);
  useEffect(() => { onActiveChange(active); }, [active, onActiveChange]);

  const reload = () => window.location.reload();

  const onConnect = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const { config: next, probe } = await saveMobileRemote(baseUrl, token, true);
      setConfig(next);
      setToken("");
      if (probe?.reachable) {
        setNotice({
          title: "已连接电脑",
          message: `电脑可访问（延迟 ${probe.latency_ms ?? "-"} ms）。`
            + "开启后手机与电脑共用电脑上的设置、会话与作品；手机本机数据不会被改写。"
            + "点击「刷新页面」切到电脑数据。",
          reload: true,
        });
      } else {
        setNotice({
          title: "配置已保存，但暂时连不上电脑",
          message: `${probe?.detail || "探测失败"}。`
            + "地址与令牌已保存，可稍后用「测试连接」重试；"
            + "联机失败时随时可以回到本页断开，手机数据不受影响。",
          reload: false,
        });
      }
    } catch (reason) {
      setError(`保存联机配置失败：${(reason as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const onTest = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const probe = await testMobileRemote();
      setNotice(probe.reachable
        ? { title: "连接正常", message: `电脑可访问（延迟 ${probe.latency_ms ?? "-"} ms）。`, reload: false }
        : { title: "连不上电脑", message: probe.detail || "探测失败", reload: false });
    } catch (reason) {
      setError(`测试连接失败：${(reason as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const onDisconnect = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const { config: next } = await saveMobileRemote(baseUrl, "", false);
      setConfig(next);
      setNotice({
        title: "已断开联机",
        message: "手机回到使用本机数据。点击「刷新页面」生效。",
        reload: true,
      });
    } catch (reason) {
      setError(`断开联机失败：${(reason as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="settings-section">
        <h4>联机同库</h4>
        <p className="field-hint" style={{ margin: "0 0 14px", lineHeight: 1.7 }}>
          手机与电脑连同一 Wi‑Fi，就能直接使用电脑上的设置、会话与作品；数据真源在电脑，
          手机本机数据保持不变。先在电脑上运行 <code>start-lan.bat</code>，把窗口里打印的
          地址和令牌填到这里。
        </p>

        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <label className="field" style={{ flex: 1, minWidth: 220 }}>
            <span>电脑地址</span>
            <input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://192.168.1.6:8010"
              inputMode="url"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
            />
          </label>
          <label className="field" style={{ flex: 1, minWidth: 220 }}>
            <span>访问令牌</span>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={config?.token_set ? "已保存；留空保持不变" : "粘贴电脑启动脚本打印的令牌"}
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
            />
          </label>
        </div>

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 14 }}>
          <button className="btn primary" disabled={busy} onClick={onConnect}>
            {busy ? "处理中…" : active ? "保存并重新连接" : "保存并连接"}
          </button>
          <button className="btn" disabled={busy || !config?.active} onClick={onTest}>
            测试连接
          </button>
          <button className="btn" disabled={busy || !config?.enabled} onClick={onDisconnect}>
            断开联机
          </button>
        </div>

        <p className="field-hint" style={{ margin: "12px 0 0", lineHeight: 1.7 }}>
          当前状态：{active ? `联机中（${config?.base_url}）` : "使用手机本机数据"}。
          联机模式下电脑端 ComfyUI 画布/预览不可用（地址在手机上指向手机自己），生成任务本身仍由电脑执行
          （需先在电脑上启动 ComfyUI）；联机中「离线资源同步」隐藏，断开后回到本机导入/导出。
        </p>
      </div>

      {notice && (
        <AlertModal
          title={notice.title}
          message={notice.message}
          actions={notice.reload
            ? [{ label: "刷新页面", primary: true, onClick: reload }]
            : undefined}
          onClose={() => setNotice(null)}
        />
      )}
      {error && <AlertModal title="联机设置未完成" message={error} onClose={() => setError(null)} />}
    </>
  );
}

/** 离线资源同步（资源包导入/导出）＋ 手机端联机同库。 */
export function DeviceSyncPanel() {
  const [exportKeys, setExportKeys] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ConfigImportResult | null>(null);
  const [saved, setSaved] = useState<SavedConfigBundle | null>(null);
  const [exportNotice, setExportNotice] = useState<string | null>(null);
  const [remoteActive, setRemoteActive] = useState(false);
  const [inventory, setInventory] = useState<ConfigInventory | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let alive = true;
    getConfigInventory()
      .then((data) => { if (alive) setInventory(data); })
      .catch(() => { /* 清单只是辅助信息，失败不影响导入导出 */ });
    return () => { alive = false; };
  }, []);

  const onExport = async () => {
    setError(null);
    if (MOBILE_PROFILE) {
      // 必须在点击手势里直接触发：相对地址在 WebView DownloadListener 里 host
      // 为空会被静默丢弃，故这里用绝对同源 URL。
      const a = document.createElement("a");
      a.href = configBundleExportUrl(exportKeys);
      a.download = "";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setExportNotice(
        "已开始导出。文件由系统下载管理器保存到「下载」目录，完成后可在通知栏或文件管理器里找到。");
      return;
    }
    setExporting(true);
    try {
      setSaved(await saveConfigBundle(exportKeys));
    } catch (reason) {
      setError(`导出失败：${(reason as Error).message}`);
    } finally {
      setExporting(false);
    }
  };

  const onReveal = async () => {
    if (!saved) return;
    const path = saved.path;
    setSaved(null);
    try {
      const { ok } = await revealConfigBundle(path);
      if (!ok) setError("未能打开文件管理器，请手动到系统下载目录查看。");
    } catch (reason) {
      setError(`打开文件夹失败：${(reason as Error).message}`);
    }
  };

  const onImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";  // 允许重复选同一文件
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await importConfigBundle(file, overwrite));
    } catch (reason) {
      setError(`导入失败：${(reason as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {MOBILE_PROFILE && <RemoteLinkSection onActiveChange={setRemoteActive} />}

      {!remoteActive && <div className="settings-section">
        <h4>离线资源同步</h4>
        <p className="field-hint" style={{ margin: "0 0 10px", lineHeight: 1.7 }}>
          把本机的设置与资源打包成一个 zip，在另一台设备上导入即可离线使用。
          包内含：设置（模型、人设、提示词模版选择等）＋ 作品仓库 ＋ 角色卡 / 世界书 / 预设 /
          工作流 / 自定义提示词模版 ＋ 全局正则 ＋ 上网素材。设备路径与网络/代理设置不随包走，
          导入端保留自己的值。
        </p>
        <p className="field-hint" style={{ margin: "0 0 14px", lineHeight: 1.7 }}>
          作品仓库含会话记录与图片，包体积可能较大；导入时作品按新身份落地，不会覆盖本机已有作品。
          注意：这里导出的是<b>完整资源包</b>；设置页底部的「导出设置 / 导入设置」只处理设置
          JSON，不含任何素材。
        </p>

        <h4 style={{ marginTop: 0 }}>本机现有资源</h4>
        <div className="sync-inventory" data-testid="sync-inventory">
          {Object.entries(ASSET_LABELS).map(([kind, label]) => (
            <span key={kind} className="sync-inventory-item">
              {label}<b>{inventory ? (inventory.counts[kind] ?? 0) : "—"}</b>
            </span>
          ))}
        </div>
        <p className="field-hint" style={{ margin: "8px 0 14px", lineHeight: 1.6 }}>
          导入完成后刷新页面，这里的数字应与电脑端一致；设备路径与网络设置不随包走。
        </p>

        <label style={{ display: "flex", alignItems: "flex-start", gap: 10, marginBottom: 10, cursor: "pointer" }}>
          <input type="checkbox" checked={exportKeys} onChange={(e) => setExportKeys(e.target.checked)} />
          <span style={{ fontSize: 13, lineHeight: 1.6 }}>
            配置包内包含 API 密钥与下载令牌（明文）
            <span style={{ display: "block", color: "var(--text-muted)", fontSize: 12 }}>
              不勾选时密钥不写入包内，导入端需自行填写才能离线直连模型。
            </span>
          </span>
        </label>

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 14 }}>
          <button
            className="btn primary"
            disabled={exporting || busy}
            onClick={() => { void onExport(); }}
          >
            {exporting ? "导出中…" : "导出配置包"}
          </button>
          <button className="btn" disabled={busy || exporting} onClick={() => fileRef.current?.click()}>
            {busy ? "导入中…" : "导入配置包"}
          </button>
          <input ref={fileRef} type="file" accept=".zip,application/zip" hidden onChange={onImportFile} />
        </div>

        <label style={{ display: "flex", alignItems: "flex-start", gap: 10, marginTop: 14, cursor: "pointer" }}>
          <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
          <span style={{ fontSize: 13, lineHeight: 1.6 }}>
            覆盖本机同名素材、上网素材与同 ID 正则
            <span style={{ display: "block", color: "var(--text-muted)", fontSize: 12 }}>
              默认跳过同名文件与同 ID/同名正则，不会静默替换本机已有内容；作品仓库始终以新身份导入。
              设备路径与代理设置始终保留本机值。
            </span>
          </span>
        </label>
      </div>}

      {result && (
        <AlertModal
          title="配置包已导入"
          message={resultText(result)}
          actions={[{ label: "刷新页面", primary: true, onClick: () => window.location.reload() }]}
          onClose={() => window.location.reload()}
        />
      )}
      {saved && (
        <AlertModal
          title="配置包已导出"
          message={`已保存到：${saved.path}\n大小：${sizeText(saved.bytes)}`}
          actions={[
            { label: "打开所在文件夹", primary: true, onClick: () => { void onReveal(); } },
            { label: "知道了", onClick: () => setSaved(null) },
          ]}
          onClose={() => setSaved(null)}
        />
      )}
      {exportNotice && (
        <AlertModal
          title="已开始导出"
          message={exportNotice}
          onClose={() => setExportNotice(null)}
        />
      )}
      {error && <AlertModal title="配置包导出/导入失败" message={error} onClose={() => setError(null)} />}
    </>
  );
}
