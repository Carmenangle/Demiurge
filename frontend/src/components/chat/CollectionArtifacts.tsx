/** 智能编造交付产物卡（Claude 式）：主卡/世界书等文件以卡片展示。
 * 点击卡片 → 预览弹层（JSON/文本）+ 版本历史区块；下方「打开文件位置」与「下载」。
 * 路径来自 fabric done 时后端下发的绝对路径，操作时原样回传后端校验（产物域 jail）。
 *
 * 版本历史（2026-09-09）：每次智能编造完成自动存档（作品域下 `_versions/<序号>-<时间戳>/`）。
 * 预览弹层可查看任意版本内容、回档（回档前当前状态先自动存档，双向可逆）、删除中途版本。
 * **版本「查看」读的是版本目录里的副本**（`/artifacts/versions/file`，2026-09-10 B5）——
 * 早期用 `files[].path`（= 当前文件路径）走 `/preview`，改完之后看旧版本会显示当前内容。
 *
 * `repoId`（2026-09-10 B3/A）：后端产物域 = 作品域（仓库/小仓库文件夹）∪ 本作品拥有的
 * 卡目录（卡目录名可与作品文件夹名不同）。**每个产物请求都必须带上它**——2026-09-10 定案
 * 「缺值返回空结果」：不带时后端**不给任何域**（列表回空、单文件 403），产物卡不显示。
 * （旧的「不带就回退作品库根」已废弃——那会让 A 作品里看到 B 作品的产物与版本，跨作品串味。）
 */
import { useEffect, useMemo, useState } from "react";
import { Download, File, FileJson, FileText, FolderOpen, History, Loader2, RotateCcw, ScanText, Trash2, X } from "lucide-react";
import type { ArtifactMeta } from "../../types/chat";
import {
  artifactAssetUrl, artifactDownloadUrl, deleteVersion, listArtifacts, listVersions,
  openArtifactFolder, previewArtifact, previewVersionFile, restoreVersion, syncWorldbook,
  type ArtifactVersion, type ArtifactVersionFile,
} from "../../api/artifacts";
import { renderMarkdown } from "../../lib/renderMarkdown";
import { resolveDocImageUrls } from "../../lib/docAssets";

// 文件类型小工具：从路径取扩展名，驱动图标与类型徽标
function fileExt(path: string): string {
  const name = path.split(/[\\/]/).pop() || "";
  const m = /.([A-Za-z0-9]+)$/.exec(name);
  return m ? m[1].toLowerCase() : "";
}

/** 类型标注（对齐参考图「Document·DOCX」风格）：Card·JSON / WorldBook·JSON / File·JSON */
function typeLabel(art: ArtifactMeta): string {
  const kindName = art.kind === "card" ? "Card"
    : art.kind === "worldbook" ? "WorldBook"
    : art.kind === "doc" ? "Doc" : "File";
  const ext = fileExt(art.path);
  return ext ? `${kindName}·${ext.toUpperCase()}` : kindName;
}

/** 按扩展名映射 lucide 图标：json→FileJson，文本类→FileText，其余→File */
function fileTypeIcon(path: string) {
  const ext = fileExt(path);
  if (ext === "json") return FileJson;
  if (["md", "txt", "yml", "yaml", "log"].includes(ext)) return FileText;
  return File;
}

function formatBytes(bytes?: number): string {
  if (!bytes || bytes <= 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

function fileBasename(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

/** 产物标题 = 作品名（产物所在目录名，如「玫瑰与繁花」），而非磁盘文件名 card/world。
 * UUID 形态的快照目录（如 8fed7f23-…）回退用后端下发的可读名。 */
function artifactTitle(art: ArtifactMeta): string {
  const parts = art.path.split(/[\\/]/).filter(Boolean);
  // 文档交付（docs/*.md）：标题用文档名（文件名去扩展名），而不是父目录名「docs」
  if (art.kind === "doc") {
    const filename = parts[parts.length - 1] || "";
    return filename.replace(/\.[^.]+$/, "") || art.name || "文档";
  }
  const dir = parts.length >= 2 ? parts[parts.length - 2] : "";
  if (dir && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(dir)) {
    return dir;
  }
  return art.name || dir || "交付产物";
}

/** 时间显示：ISO → 月-日 时:分（版本列表紧凑用法） */
function fmtTime(ts: string): string {
  if (!ts) return "";
  return ts.replace("T", " ").slice(0, 16);
}

/** 单张产物卡片（对齐参考图：白色文档图标 + 标题 + 类型标注 + 底部操作行）。
 * 整卡点击打开预览；底部小图标直接触发对应操作，不冒泡。 */
function ArtifactTile({
  art, outputDir, repoId, onOpen,
}: {
  art: ArtifactMeta;
  outputDir?: string;
  repoId?: string;
  onOpen: (art: ArtifactMeta) => void;
}) {
  const TypeIcon = fileTypeIcon(art.path);
  const download = (event: React.MouseEvent) => {
    event.stopPropagation();
    if (!outputDir) return;
    const url = artifactDownloadUrl(outputDir, art.path, repoId);
    const a = document.createElement("a");
    a.href = url;
    a.download = fileBasename(art.path);
    document.body.appendChild(a);
    a.click();
    a.remove();
  };
  const openFolder = async (event: React.MouseEvent) => {
    event.stopPropagation();
    if (!outputDir) return;
    try {
      await openArtifactFolder(outputDir, art.path, true, repoId);
    } catch (e) {
      window.alert(`打开文件位置失败：${(e as Error).message || "未知原因"}`);
    }
  };
  return (
    <div className="artifact-tile" title={`点击预览：${art.path}`} role="button" tabIndex={0}
      onClick={() => onOpen(art)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(art); } }}>
      <span className="artifact-tile-main">
        <span className="artifact-tile-ico">
          <TypeIcon size={18} className={`artifact-tile-icon artifact-tile-icon-${art.kind}`} />
        </span>
        <span className="artifact-tile-text">
          <span className="artifact-tile-name" title={`${art.name}（${art.path}）`}>{artifactTitle(art)}</span>
          <span className="artifact-tile-meta">
            <span className="artifact-tile-kind">{art.kind === "card" ? "角色主卡"
              : art.kind === "worldbook" ? "世界书"
              : art.kind === "doc" ? "文档" : "文件"}</span>
            <span className="artifact-tile-ext">{typeLabel(art)}</span>
            {art.size ? <span className="artifact-tile-size">{formatBytes(art.size)}</span> : null}
          </span>
        </span>
      </span>
      <span className="artifact-tile-actions">
        <button type="button" className="artifact-tile-action" title="预览内容" onClick={(e) => { e.stopPropagation(); onOpen(art); }}>
          <ScanText size={13} /> 预览
        </button>
        <button type="button" className="artifact-tile-action" title="在资源管理器中定位" disabled={!outputDir} onClick={openFolder}>
          <FolderOpen size={13} /> 打开位置
        </button>
        <button type="button" className="artifact-tile-action" title="下载文件" disabled={!outputDir} onClick={download}>
          <Download size={13} /> 下载
        </button>
      </span>
    </div>
  );
}

/** 预览弹层：文件名与信息 + JSON 预览体 + 版本历史区块 + 底部操作。 */
function ArtifactPreviewModal({
  art, outputDir, repoId, onClose,
}: {
  art: ArtifactMeta;
  outputDir?: string;
  repoId?: string;
  onClose: () => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState("");
  // 预览 kind 由后端 `artifact_kind` 单一属主给出（card/worldbook/doc/file）
  const [kind, setKind] = useState("");
  const [opening, setOpening] = useState(false);
  const [notice, setNotice] = useState("");
  // 当前预览来源：默认当前产物（art.path）；点历史版本后切到**版本副本**（versionView，B5）
  const [viewPath, setViewPath] = useState(art.path);
  // 版本副本预览目标（2026-09-10 B5）：非空 = 正文来自版本目录里的副本。
  // 不能复用 files[].path 走 /preview——那是「当前」文件路径，改完后看旧版本会显示当前内容。
  const [versionView, setVersionView] = useState<
    (Pick<ArtifactVersionFile, "rel" | "path"> & { versionId: string; seq: number }) | null
  >(null);
  // null=尚未拉取版本列表；[]=无历史（不渲染版本区块）
  const [versions, setVersions] = useState<ArtifactVersion[] | null>(null);
  const [versionsError, setVersionsError] = useState("");
  const [acting, setActing] = useState(false);

  useEffect(() => {
    let alive = true;
    if (!outputDir) {
      setError("未配置仓库文件夹（outputDir），无法读取文件内容");
      return;
    }
    // 版本副本走 `/versions/file`（读版本目录里的副本）；当前产物走 `/preview`（活文件）。
    const req = versionView
      ? previewVersionFile(outputDir, versionView.versionId, versionView.rel, repoId)
      : previewArtifact(outputDir, viewPath, repoId);
    req
      .then((res) => {
        if (alive) { setText(res.text); setTruncated(res.truncated); setKind(res.kind || ""); setError(""); }
      })
      .catch((e) => { if (alive) setError((e as Error).message || "读取产物失败"); });
    return () => { alive = false; };
  }, [viewPath, versionView, outputDir, repoId]);

  // 版本历史：打开弹层即拉取（无历史则不发区块）
  useEffect(() => {
    let alive = true;
    if (!outputDir || versions !== null) return;
    listVersions(outputDir, repoId)
      .then((res) => { if (alive) setVersions(res.versions || []); })
      .catch((e) => { if (alive) { setVersions([]); setVersionsError((e as Error).message || "版本历史加载失败"); } });
    return () => { alive = false; };
  }, [outputDir, repoId, versions]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // JSON 产物美化显示：解析失败按原文展示（无需报错，预览尽力而为）
  const pretty = useMemo(() => {
    if (text == null) return "";
    try { return JSON.stringify(JSON.parse(text), null, 2); } catch { return text; }
  }, [text]);
  // 展示用路径：看版本副本时正文来自版本目录，但扩展名/图标/相对插图换算仍按该文件的
  // **等价活路径**（outputDir + rel）算——版本副本与活文件同构，图片一律取当前素材。
  const shownPath = versionView ? versionView.path : viewPath;
  const TypeIcon = fileTypeIcon(shownPath);

  // 文档交付（.md）走 Markdown 渲染（2026-09-10 链路③）：相对图片路径
  // （doc.attach_material 回传的 assets/x.png）先解析成作品域素材 URL 再渲染，
  // 否则插在图里的素材一律裂图。其余产物仍按纯文本 <pre> 展示。
  const isDoc = kind === "doc" || fileExt(shownPath) === "md";
  const docHtml = useMemo(() => {
    if (!isDoc || text == null) return "";
    const source = outputDir
      ? resolveDocImageUrls(text, {
          docPath: shownPath,
          toUrl: (absolutePath) => artifactAssetUrl(outputDir, absolutePath, repoId),
        })
      : text;
    return renderMarkdown(source);
  }, [isDoc, text, shownPath, outputDir, repoId]);

  const refreshVersions = () => setVersions(null); // 置空触发重新拉取（useEffect 依赖 versions）

  const download = () => {
    if (!outputDir || versionView) return; // 版本副本不支持下载（download 只会拿到当前文件）
    const url = artifactDownloadUrl(outputDir, viewPath, repoId);
    const a = document.createElement("a");
    a.href = url;
    a.download = fileBasename(viewPath);
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const openFolder = async () => {
    if (!outputDir || opening || versionView) return;
    setOpening(true);
    try {
      const res = await openArtifactFolder(outputDir, viewPath, true, repoId);
      setNotice(res.ok ? "已在资源管理器中定位该文件" : "打开文件位置失败（非本机环境或文件已消失）");
    } catch (e) {
      setNotice((e as Error).message || "打开文件位置失败");
    } finally {
      setOpening(false);
    }
  };

  /** 版本查看目标：优先 card.json，其次 worldbook.json，兜底第一个文件 */
  const pickViewFile = (v: ArtifactVersion): ArtifactVersionFile | undefined => {
    const byName = (n: string) => v.files.find((f) => f.name === n);
    return byName("card.json") || byName("worldbook.json") || v.files[0];
  };

  const previewVersion = (v: ArtifactVersion) => {
    const f = pickViewFile(v);
    if (!f) return;
    setVersionView({ versionId: v.version_id, seq: v.seq, rel: f.rel, path: f.path });
    setNotice(`正在查看版本 #${v.seq} 的存档副本（${fmtTime(v.ts)}）；回档后它才会变成当前产物`);
  };

  const backToCurrent = () => {
    setVersionView(null);
    setViewPath(art.path);
    setNotice("正在查看当前产物");
  };

  const restore = async (v: ArtifactVersion) => {
    if (!outputDir || acting) return;
    if (!window.confirm(`回档到版本 #${v.seq}（${fmtTime(v.ts)}）？
当前产物会先自动存档（不会丢失），该版本的文件将覆盖回写到作品目录。`)) return;
    setActing(true);
    try {
      const res = await restoreVersion(outputDir, v.version_id, repoId);
      // 回档后活文件 = 该版本 → 回到「当前产物」视图，避免停在副本上造成二次误解
      setVersionView(null);
      setViewPath(art.path);
      setNotice(`已回档到 #${v.seq}：${res.restored.length} 个产物文件已恢复为当前版本`);
      refreshVersions();
    } catch (e) {
      setNotice((e as Error).message || "回档失败");
    } finally {
      setActing(false);
    }
  };

  const remove = async (v: ArtifactVersion) => {
    if (!outputDir || acting) return;
    if (!window.confirm(`删除版本 #${v.seq}（${fmtTime(v.ts)}）？
该版本文件将永久移除，无法恢复（当前产物不受影响）。`)) return;
    setActing(true);
    try {
      await deleteVersion(outputDir, v.version_id, repoId);
      setNotice(`已删除版本 #${v.seq}`);
      if (versionView?.versionId === v.version_id) {
        backToCurrent(); // 正在查看被删版本 → 回到当前产物
      }
      refreshVersions();
    } catch (e) {
      setNotice((e as Error).message || "删除失败");
    } finally {
      setActing(false);
    }
  };

  const showVersions = versions !== null && versions.length > 0;

  return (
    <div className="modal-mask artifact-modal-mask" onClick={onClose}>
      <div className="artifact-modal" onClick={(e) => e.stopPropagation()}>
        <div className="artifact-modal-head">
          <TypeIcon size={16} className={`artifact-tile-icon artifact-tile-icon-${art.kind}`} />
          <div className="artifact-modal-title">
            <strong>{artifactTitle(art)}</strong>
            <span className="artifact-modal-path" title={shownPath}>{shownPath}</span>
            {versionView && (
              <span className="artifact-modal-badge">版本 #{versionView.seq} 存档副本</span>
            )}
          </div>
          <span className="artifact-tile-ext artifact-modal-ext">{typeLabel(art)}</span>
          <button type="button" className="artifact-modal-close" title="关闭预览" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="artifact-modal-body">
          {error ? (<div className="artifact-modal-error">{error}</div>)
            : text == null ? (<div className="artifact-modal-loading"><Loader2 size={16} className="spin" /> 读取中…</div>)
            : isDoc ? (<>
                <div className="artifact-modal-markdown" dangerouslySetInnerHTML={{ __html: docHtml }} />
                {truncated && <div className="artifact-modal-truncated">文件较大，仅预览开头部分；如需全文请使用「下载」。</div>}
              </>)
            : (<><pre className="artifact-modal-pre">{pretty}</pre>
                {truncated && <div className="artifact-modal-truncated">文件较大，仅预览开头部分；如需全文请使用「下载」。</div>}
              </>)}
        </div>
        {showVersions && (
          <div className="artifact-modal-versions">
            <div className="artifact-versions-label">
              <History size={13} /> 版本历史
              <span className="artifact-versions-hint">每次智能编造「完成」自动存档；回档前当前状态也会先存一档</span>
            </div>
            <div className="artifact-versions-list">
              {/* 当前产物固定行：最新落盘结果；查看历史版本后可一键切回 */}
              <div className={`artifact-version-item${versionView ? "" : " active"}`}>
                <span className="artifact-version-id current">当前</span>
                <span className="artifact-version-time">最新落盘</span>
                <span className="artifact-version-summary">当前产物（最新一次完成的结果，可随时回档/继续修改）</span>
                <span className="artifact-version-meta" />
                <span className="artifact-version-actions">
                  <button type="button" className="artifact-version-btn" disabled={acting || !versionView}
                    onClick={backToCurrent}
                    title="回到当前产物">查看</button>
                </span>
              </div>
              {versions.map((v) => {
                const isViewing = versionView?.versionId === v.version_id;
                return (
                  <div key={v.version_id} className={`artifact-version-item${isViewing ? " active" : ""}`}>
                    <span className="artifact-version-id">#{v.seq}</span>
                    <span className="artifact-version-time">{fmtTime(v.ts)}</span>
                    <span className="artifact-version-summary" title={v.summary}>
                      {v.summary || (v.trigger === "pre_restore" ? "回档前自动存档" : "智能编造完成")}
                    </span>
                    <span className="artifact-version-meta">{v.files.length} 文件 · {formatBytes(v.total_size)}</span>
                    <span className="artifact-version-actions">
                      <button type="button" className="artifact-version-btn" disabled={acting || isViewing}
                        onClick={() => previewVersion(v)} title="查看该版本的存档副本（不是当前文件）">查看</button>
                      <button type="button" className="artifact-version-btn primary" disabled={acting}
                        onClick={() => restore(v)} title="回档（当前状态会先自动存档）">
                        <RotateCcw size={11} /> 回档
                      </button>
                      <button type="button" className="artifact-version-btn danger" disabled={acting}
                        onClick={() => remove(v)} title="删除该版本（仅删除快照，当前产物不受影响）">
                        <Trash2 size={11} /> 删除
                      </button>
                    </span>
                  </div>
                );
              })}
            </div>
            {versionsError && <div className="artifact-versions-error">{versionsError}</div>}
          </div>
        )}
        <div className="artifact-modal-foot">
          <span className="artifact-modal-notice">{notice}</span>
          <span className="artifact-modal-actions">
            {/* 看版本副本时禁用「打开位置/下载」：这两个动作只有活文件端点，作用到当前文件会误导 */}
            <button type="button" className="btn" disabled={opening || !!versionView} onClick={openFolder}
              title={versionView ? "版本副本仅支持查看；回档后可作为当前产物打开/下载" : undefined}>
              <FolderOpen size={14} /> 打开文件位置
            </button>
            <button type="button" className="btn primary" disabled={!outputDir || !!versionView} onClick={download}
              title={versionView ? "版本副本仅支持查看；回档后可作为当前产物打开/下载" : undefined}>
              <Download size={14} /> 下载
            </button>
          </span>
        </div>
      </div>
    </div>
  );
}

/** 产物卡容器：一行多卡片，点击任一张打开预览。
 * repoId：当前作品 id——后端据此收窄产物域；**缺值后端回空结果**（2026-09-10 定案），
 * 故调用方必须显式传当前作品 id，否则产物卡不显示（宁缺不串味）。
 * autoLoad：历史消息补卡——仅「最终交付」消息（文本非「需要批准/执行未完成」中间态）
 * 且无实时 artifacts 时，按作品目录惰性拉取当前产物（快照里没有产物信息，靠它回填）。
 * ChatMessages 负责传入收紧后的 autoLoad；本组件只负责惰性拉取一次（缓存空结果）。
 */
export function CollectionArtifacts({
  artifacts = [], outputDir, repoId = "", autoLoad = false,
}: {
  artifacts?: ArtifactMeta[];
  outputDir?: string;
  repoId?: string;
  autoLoad?: boolean;
}) {
  const [selected, setSelected] = useState<ArtifactMeta | null>(null);
  // null=尚未惰性拉取；[]=拉取过且无产物（避免重复请求）
  const [lazyItems, setLazyItems] = useState<ArtifactMeta[] | null>(null);
  useEffect(() => {
    if (artifacts.length > 0 || lazyItems !== null || !autoLoad || !outputDir) return;
    let alive = true;
    listArtifacts(outputDir, repoId)
      .then((res) => { if (alive) setLazyItems(res.items || []); })
      .catch(() => { if (alive) setLazyItems([]); });
    return () => { alive = false; };
  }, [artifacts.length, autoLoad, outputDir, repoId, lazyItems]);
  const items = artifacts.length > 0 ? artifacts : (lazyItems || []);
  const [syncing, setSyncing] = useState(false);
  if (!items.length) return null;
  const hasWorldbook = items.some((a) => a.kind === "worldbook");
  const syncNow = async () => {
    if (!outputDir || syncing) return;
    const wbItem = items.find((a) => a.kind === "worldbook") || items[0];
    if (!wbItem) return;
    if (!window.confirm(`把卡目录世界书同步到资产库（worlds/<卡名>.json，旧版自动备份）？`)) return;
    setSyncing(true);
    try {
      const res = await syncWorldbook(outputDir, wbItem.path, repoId);
      window.alert(`已同步 ${res.entries} 条到资产库：${res.dest}`);
    } catch (e) {
      window.alert(`同步失败：${(e as Error).message || "未知原因"}`);
    } finally {
      setSyncing(false);
    }
  };
  return (
    <>
      <div className="artifact-cards">
        <span className="artifact-cards-head">
          <span className="artifact-cards-label">交付产物</span>
          {hasWorldbook && (
            <button type="button" className="artifact-sync-btn" disabled={syncing || !outputDir}
              title="把卡目录 worldbook.json 同步到独立世界书（资产库 worlds/ 下）" onClick={syncNow}>
              <Download size={12} /> {syncing ? "同步中…" : "同步到资产库"}
            </button>
          )}
        </span>
        <div className="artifact-cards-row">
          {items.map((art, i) => (
            <ArtifactTile key={`${art.path}-${i}`} art={art} outputDir={outputDir} repoId={repoId}
              onOpen={setSelected} />
          ))}
        </div>
      </div>
      {selected && outputDir && (
        <ArtifactPreviewModal art={selected} outputDir={outputDir} repoId={repoId}
          onClose={() => setSelected(null)} />
      )}
    </>
  );
}
