import { useEffect, useMemo, useState } from "react";
import { Download, Star } from "lucide-react";
import { StateHint } from "../../components/layout/PageShell";
import { ConfirmModal } from "../../components/Modal";
import { Pager } from "../../components/Pager";
import { listMarket, startTrackedInstall, type NodePack, type UpdateProgress } from "../../api/nodeManager";
import { useUpdateProgress } from "./useUpdateProgress";
import { UpdateProgressPanel } from "./UpdateProgressPanel";
import { useSettings } from "../../stores/settings";

// 官方插件市场：全量包(几千条)，搜索 + 分页 + 安装。未装的才显示安装按钮。
const PAGE_SIZE = 30;

export function MarketTab({ url, initialSearch = "", onSearchConsumed }: {
  url: string; initialSearch?: string; onSearchConsumed?: () => void;
}) {
  const [items, setItems] = useState<NodePack[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState(initialSearch);  // 从 AI 搭工作流跳来时预填搜索词
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState("");
  const [confirm, setConfirm] = useState<NodePack | null>(null);
  const [gitUrl, setGitUrl] = useState("");
  const { settings } = useSettings();
  const upd = useUpdateProgress();
  const [awaitingRepo, setAwaitingRepo] = useState("");

  useEffect(() => {
    setLoading(true);
    listMarket(url).then((r) => setItems(r.items)).catch(() => setItems([])).finally(() => setLoading(false));
  }, [url]);

  // 消费一次性搜索词（跳来时预填后即清空来源，避免离开再回来又被强填）
  useEffect(() => {
    if (initialSearch) onSearchConsumed?.();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const runInstall = async (repository: string, opts: { allowSensitive?: boolean; skipDeps?: boolean } = {}) => {
    if (!settings.comfyuiPath) { setBusy("未配置 ComfyUI 目录（设置 → 路径）。"); return; }
    const r = await startTrackedInstall(settings.comfyuiPath, repository, {
      pythonExe: settings.comfyuiPython || "",
      proxy: settings.proxyEnabled ? settings.proxyUrl : "",
      allowSensitive: opts.allowSensitive, skipDeps: opts.skipDeps,
    });
    if (r.already_running) { setBusy("已有一个节点或 ComfyUI 维护任务在运行。"); return; }
    setAwaitingRepo("");
    upd.track((p: UpdateProgress) => {
      if (p.pending_sensitive.length > 0) setAwaitingRepo(repository);
    });
  };

  const doInstallGit = async () => {
    const g = gitUrl.trim();
    if (!g) return;
    setBusy("");
    try {
      await runInstall(g);
      setGitUrl("");
    } catch (e) {
      setBusy(`链接安装失败：${(e as Error).message}`);
    }
  };

  const kw = q.trim().toLowerCase();
  const filtered = useMemo(() => {
    const base = kw ? items.filter((p) => (p.title + p.id + p.description + p.author).toLowerCase().includes(kw)) : items;
    // 按热度(stars)降序，未安装的排前面便于发现
    return [...base].sort((a, b) => (b.stars || 0) - (a.stars || 0));
  }, [items, kw]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const curPage = Math.min(page, pageCount);
  const shown = filtered.slice((curPage - 1) * PAGE_SIZE, curPage * PAGE_SIZE);

  const doInstall = async () => {
    if (!confirm) return;
    const pack = confirm;
    setConfirm(null);
    setBusy("");
    try {
      if (!pack.repository) throw new Error("该市场条目没有可用的 Git 仓库地址");
      await runInstall(pack.repository);
    } catch (e) {
      setBusy(`安装失败：${(e as Error).message}`);
    }
  };

  const isInstalled = (p: NodePack) => p.state === "enabled" || p.state === "disabled";

  if (loading) return <StateHint>读取插件市场（数千条，稍候）…</StateHint>;

  return (
    <div>
      <div className="page-toolbar">
        <input placeholder="搜索插件（名称/作者/描述）…" value={q}
          onChange={(e) => { setQ(e.target.value); setPage(1); }} style={{ maxWidth: 320 }} />
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{filtered.length} / {items.length} 个插件</span>
      </div>
      {upd.prog && <UpdateProgressPanel prog={upd.prog}
        onConfirmSensitive={awaitingRepo ? () => void runInstall(awaitingRepo, { allowSensitive: true }) : undefined}
        onSkipDeps={awaitingRepo ? () => void runInstall(awaitingRepo, { skipDeps: true }) : undefined} />}
      {busy && <p style={{ fontSize: 13, color: "var(--text-muted)" }}>{busy}</p>}

      <div className="market-grid">
        {shown.map((p) => (
          <div className="market-card" key={p.id}>
            <div className="market-card-head">
              <a href={p.repository} target="_blank" rel="noreferrer" className="market-title" title={p.repository}>{p.title}</a>
              {p.stars > 0 && <span className="market-stars"><Star size={11} /> {p.stars}</span>}
            </div>
            <p className="market-desc">{p.description || "（无描述）"}</p>
            <div className="market-card-foot">
              <span className="market-author">{p.author}</span>
              {isInstalled(p)
                ? <span className="node-state-ok">已安装</span>
                : <button className="btn" onClick={() => setConfirm(p)}>
                    <Download size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />安装
                  </button>}
            </div>
          </div>
        ))}
      </div>
      <Pager page={curPage} pageCount={pageCount} onPage={setPage} />

      <div className="git-install-bar">
        <input
          placeholder="输入自定义 GitHub 链接安装插件（自动装 requirements.txt 依赖）"
          value={gitUrl}
          onChange={(e) => setGitUrl(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") doInstallGit(); }}
          style={{ flex: 1 }}
        />
        <button className="btn primary" disabled={!gitUrl.trim()} onClick={doInstallGit}>
          链接安装
        </button>
      </div>

      {confirm && (
        <ConfirmModal
          title="安装插件"
          message={`确认安装「${confirm.title}」？将从 ${confirm.repository || "源"} 下载，完成后需重启 ComfyUI 生效。`}
          confirmText="安装"
          onConfirm={doInstall}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  );
}
