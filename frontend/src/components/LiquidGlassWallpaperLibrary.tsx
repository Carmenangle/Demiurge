import { useEffect, useMemo, useRef, useState } from "react";
import {
  Check, ChevronLeft, ChevronRight, Eye, EyeOff, FolderOpen, Gamepad2,
  Images, Loader2, Play, ScanSearch, Search, X,
} from "lucide-react";
import { localViewUrl } from "../api/comfyui";
import {
  scanWallpapers, scanWorkshopWallpapers, wallpaperThumbUrl,
  type WallpaperLibraryItem, type WallpaperLibraryKind, type WallpaperLibraryPage,
  type WallpaperWorkshopItem, type WallpaperWorkshopPage,
} from "../api/userState";
import {
  liquidGlassWallpaperKind,
  type LiquidGlassSettings, type LiquidGlassWallpaper,
} from "../stores/settings";

const PAGE_SIZE = 21;
// 工坊预览图普遍只有 160~200px；低于该边长视为「低清预览」，全屏会明显发糊
const LOW_RES_EDGE = 480;

type LibraryItem = WallpaperLibraryItem | WallpaperWorkshopItem;
type LibrarySource = "folder" | "workshop";

function previewEdge(item: LibraryItem): number {
  if (!("preview_width" in item)) return 0;
  return Math.min(item.preview_width || 0, item.preview_height || 0);
}

/** 页面内实时渲染的判据＝工坊条目自带 `scene.pkg`；此时 preview 只是列表封面，不参与画质评价。 */
function rendersLive(item: LibraryItem): boolean {
  return "scene_pkg" in item && item.scene_pkg === true;
}

function isLowResPreview(item: LibraryItem): boolean {
  if (rendersLive(item)) return false;
  if (!("preview_only" in item) || !item.preview_only) return false;
  const edge = previewEdge(item);
  return edge > 0 && edge < LOW_RES_EDGE;
}

function displayName(item: { name: string; path: string; title?: string }): string {
  if (item.title?.trim()) return item.title.trim();
  return item.name.split("/").pop() || item.path.split(/[\\/]/).pop() || "壁纸";
}

function weTypeLabel(type: string): string {
  const map: Record<string, string> = {
    scene: "场景壁纸",
    video: "视频壁纸",
    web: "网页壁纸",
    application: "应用壁纸",
  };
  return map[type] || "创意工坊壁纸";
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

function LibraryCard(props: {
  item: LibraryItem;
  folder: string;
  applied: boolean;
  hiddenView: boolean;
  onApply: () => void;
  onToggleHidden: () => void;
}) {
  const { item, folder, applied, hiddenView } = props;
  const [hover, setHover] = useState(false);
  const [liveReady, setLiveReady] = useState(false);
  const [liveFailed, setLiveFailed] = useState(false);
  const workshop = "preview_only" in item ? item : null;
  const lowRes = isLowResPreview(item);
  const preview = workshop?.preview || "";
  // 工坊封面（常常是动图）优先于视频抽帧，列表里也能直接看到动态
  const thumb = preview ? localViewUrl(preview) : wallpaperThumbUrl(folder, item.path);
  const live = hover && item.kind === "video" && !liveFailed;

  return (
    <div
      className={`lg-lib-card${applied ? " applied" : ""}`}
      role="button"
      tabIndex={0}
      title={lowRes
        ? `${item.path}\n预览图仅 ${workshop?.preview_width ?? 0}×${workshop?.preview_height ?? 0}，浏览器无法渲染该场景/网页，全屏会发糊`
        : item.path}
      onClick={props.onApply}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          props.onApply();
        }
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => { setHover(false); setLiveReady(false); }}
    >
      <div className="lg-lib-card__media">
        <img className="lg-lib-card__thumb" src={thumb} alt="" loading="lazy" />
        {live && (
          <video
            className={`lg-lib-card__live${liveReady ? " ready" : ""}`}
            src={localViewUrl(item.path)}
            poster={thumb}
            autoPlay
            muted
            loop
            playsInline
            preload="none"
            onCanPlay={() => setLiveReady(true)}
            onError={() => setLiveFailed(true)}
          />
        )}
        <span className="lg-lib-card__badges">
          {workshop && rendersLive(item) ? (
            <span
              className="lg-lib-card__badge lg-lib-card__badge--live"
              title="作品带 scene.pkg，应用后在页面内按图层实时渲染（预览图只当封面）"
            >
              实时渲染
            </span>
          ) : workshop?.preview_only ? (
            <>
              <span className="lg-lib-card__badge">静态预览</span>
              {lowRes && (
                <span
                  className="lg-lib-card__badge lg-lib-card__badge--warn"
                  title={`作品预览图仅 ${workshop.preview_width}×${workshop.preview_height}，浏览器无法渲染该场景/网页`}
                >
                  低清
                </span>
              )}
            </>
          ) : (
            <>
              {item.kind === "video" && (
                <span className="lg-lib-card__badge"><Play size={10} />视频</span>
              )}
              {item.kind === "image" && item.animated && (
                <span className="lg-lib-card__badge">动态</span>
              )}
            </>
          )}
        </span>
        {applied && (
          <span className="lg-lib-card__applied"><Check size={12} />使用中</span>
        )}
        <button
          type="button"
          className="lg-lib-card__hide"
          title={hiddenView ? "取消隐藏" : "隐藏此壁纸（不删源文件）"}
          aria-label={`${hiddenView ? "取消隐藏" : "隐藏"} ${displayName(item)}`}
          onClick={(e) => { e.stopPropagation(); props.onToggleHidden(); }}
        >
          {hiddenView ? <Eye size={14} /> : <EyeOff size={14} />}
        </button>
      </div>
      <div className="lg-lib-card__meta">
        <strong>{displayName(item)}</strong>
        <small>
          {workshop
            ? `${weTypeLabel(workshop.we_type)}${rendersLive(item) ? " · 实时渲染" : workshop.preview_only ? " · 静态预览" : " · 悬停播放"}${lowRes ? " · 低清" : ""}`
            : item.kind === "video" ? "视频 · 悬停播放" : item.animated ? "动态图片" : "图片"}
          {" · "}{formatSize(item.size)}
        </small>
      </div>
    </div>
  );
}

export function LiquidGlassWallpaperLibrary({
  lg,
  update,
  onClose,
}: {
  lg: LiquidGlassSettings;
  update: (patch: Partial<LiquidGlassSettings>) => void;
  onClose: () => void;
}) {
  const [folder, setFolder] = useState(lg.wallpaperDir);
  const [source, setSource] = useState<LibrarySource>(lg.wallpaperDir.trim() ? "folder" : "workshop");
  const [kind, setKind] = useState<WallpaperLibraryKind>("all");
  const [tab, setTab] = useState<"normal" | "hidden">("normal");
  const [queryDraft, setQueryDraft] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [nonce, setNonce] = useState(0);
  const [data, setData] = useState<WallpaperLibraryPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [workshopData, setWorkshopData] = useState<WallpaperWorkshopPage | null>(null);
  const [workshopLoading, setWorkshopLoading] = useState(false);
  const [workshopError, setWorkshopError] = useState("");
  const seqRef = useRef(0);
  const workshopSeqRef = useRef(0);

  const hiddenKey = useMemo(() => lg.wallpaperHidden.join("\n"), [lg.wallpaperHidden]);
  const activePath = lg.wallpapers.find((w) => w.id === lg.activeWallpaperId)?.path || "";
  const trimmedDir = lg.wallpaperDir.trim();
  const pageData = source === "folder" ? data : workshopData;
  const activeLoading = source === "folder" ? loading : workshopLoading;
  const activeError = source === "folder" ? error : workshopError;

  // 名称搜索防抖：停止输入 260ms 后才请求，换关键词回到第一页
  useEffect(() => {
    const next = queryDraft.trim();
    if (next === query) return;
    const timer = window.setTimeout(() => {
      setQuery(next);
      setPage(1);
    }, 260);
    return () => window.clearTimeout(timer);
  }, [queryDraft, query]);

  const clearQuery = () => {
    setQueryDraft("");
    setQuery("");
    setPage(1);
  };

  useEffect(() => {
    if (source !== "folder") return;
    if (!trimmedDir) {
      setData(null);
      setError("");
      setLoading(false);
      return;
    }
    const seq = ++seqRef.current;
    setLoading(true);
    setError("");
    scanWallpapers({
      folder: trimmedDir,
      kind,
      page,
      pageSize: PAGE_SIZE,
      hidden: hiddenKey ? hiddenKey.split("\n") : [],
      hiddenOnly: tab === "hidden",
      query,
    })
      .then((res) => {
        if (seq === seqRef.current) setData(res);
      })
      .catch((err) => {
        if (seq !== seqRef.current) return;
        setData(null);
        setError(err instanceof Error ? err.message : "扫描失败，请检查后端是否在线");
      })
      .finally(() => {
        if (seq === seqRef.current) setLoading(false);
      });
  }, [source, trimmedDir, kind, page, tab, hiddenKey, nonce, query]);

  useEffect(() => {
    if (source !== "workshop") return;
    const seq = ++workshopSeqRef.current;
    setWorkshopLoading(true);
    setWorkshopError("");
    scanWorkshopWallpapers({
      kind,
      page,
      pageSize: PAGE_SIZE,
      hidden: hiddenKey ? hiddenKey.split("\n") : [],
      hiddenOnly: tab === "hidden",
      query,
    })
      .then((res) => {
        if (seq === workshopSeqRef.current) setWorkshopData(res);
      })
      .catch((err) => {
        if (seq !== workshopSeqRef.current) return;
        setWorkshopData(null);
        setWorkshopError(err instanceof Error ? err.message : "扫描创意工坊失败，请检查后端是否在线");
      })
      .finally(() => {
        if (seq === workshopSeqRef.current) setWorkshopLoading(false);
      });
  }, [source, kind, page, tab, hiddenKey, nonce, query]);

  const applyFolder = () => {
    const raw = folder.trim();
    if (!raw) {
      setError("请先填写壁纸存放地址");
      return;
    }
    setPage(1);
    setTab("normal");
    update({ wallpaperDir: raw });
    setNonce((n) => n + 1);
  };

  const applyItem = (item: LibraryItem) => {
    const existing = lg.wallpapers.find((w) => w.path === item.path);
    const workshop = "preview_only" in item ? item : null;
    const lowRes = isLowResPreview(item);
    // 能实时渲染的作品不挂「静态预览」后缀：应用后画布按图层绘制，preview 只是封面
    const suffix = workshop?.preview_only
      ? rendersLive(item) ? "（实时渲染）" : lowRes ? "（低清预览）" : "（静态预览）"
      : "";
    const name = workshop ? `${workshop.title}${suffix}` : displayName(item);
    const entry: LiquidGlassWallpaper = {
      ...(existing || {
        id: crypto.randomUUID(),
        path: item.path,
        kind: liquidGlassWallpaperKind(item.path),
      }),
      name,
      ...(workshop?.preview ? { preview: workshop.preview } : {}),
      ...(lowRes ? { lowRes: true } : {}),
      ...(workshop ? { workshopId: workshop.workshop_id } : {}),
      ...(workshop?.scene_pkg ? { scenePkg: true } : {}),
    };
    if (!lowRes) delete entry.lowRes;
    if (!workshop) delete entry.workshopId;
    if (!workshop?.scene_pkg) delete entry.scenePkg;
    update({
      wallpapers: existing
        ? lg.wallpapers.map((w) => (w.path === item.path ? entry : w))
        : [...lg.wallpapers, entry],
      activeWallpaperId: entry.id,
      wallpaperPaused: false,
    });
  };

  const toggleHidden = (item: LibraryItem) => {
    const next = new Set(lg.wallpaperHidden);
    if (next.has(item.path)) next.delete(item.path);
    else next.add(item.path);
    update({ wallpaperHidden: Array.from(next) });
  };

  const switchTab = (next: "normal" | "hidden") => {
    setTab(next);
    setPage(1);
  };

  const switchSource = (next: LibrarySource) => {
    setSource(next);
    setTab("normal");
    setPage(1);
  };

  const queryLabel = query ? `「${query}」匹配 ` : "";
  const summary = source === "workshop"
    ? tab === "hidden"
      ? `${queryLabel}${workshopData?.total ?? 0} 个已隐藏的创意工坊壁纸 · 点击眼睛图标恢复`
      : `${queryLabel}${workshopData?.total ?? 0} 个 Wallpaper Engine 壁纸 · 点击卡片即应用`
    : tab === "hidden"
      ? `${queryLabel}${data?.total ?? 0} 个已隐藏壁纸 · 点击眼睛图标恢复`
      : `${queryLabel}${data?.total ?? 0} 个可播放壁纸 · 点击卡片即应用`;

  return (
    <div className="lg-lib-scrim" onClick={onClose}>
      <section
        className="lg-lib"
        role="dialog"
        aria-modal="true"
        aria-label="液态玻璃壁纸库"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="lg-lib__head">
          <div>
            <h3><Images size={17} />壁纸库</h3>
            <p>视频卡片优先用作品封面（动图会动），悬停播放视频；带 scene.pkg 的场景壁纸在页面内实时渲染，网页作品只能显示预览图</p>
          </div>
          <button type="button" className="lg-icon-btn" onClick={onClose} aria-label="关闭壁纸库">
            <X size={18} />
          </button>
        </header>

        <div className="lg-lib__sources" role="tablist" aria-label="壁纸来源">
          <button
            type="button"
            role="tab"
            aria-selected={source === "folder"}
            className={source === "folder" ? "active" : ""}
            onClick={() => switchSource("folder")}
          >
            <FolderOpen size={13} />本地文件夹
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={source === "workshop"}
            className={source === "workshop" ? "active" : ""}
            onClick={() => switchSource("workshop")}
          >
            <Gamepad2 size={13} />Wallpaper Engine{workshopData ? ` (${workshopData.total})` : ""}
          </button>
        </div>

        {source === "folder" ? (
          <div className="lg-lib__folder">
            <label htmlFor="lg-lib-folder"><FolderOpen size={13} />存放地址</label>
            <div className="lg-lib__folder-row">
              <input
                id="lg-lib-folder"
                value={folder}
                placeholder="例如 D:\\Wallpaper Engine\\projects\\myproject"
                spellCheck={false}
                onChange={(e) => setFolder(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    applyFolder();
                  }
                }}
              />
              <button
                type="button"
                className="lg-btn lg-btn--primary"
                onClick={applyFolder}
                disabled={loading}
              >
                {loading ? <Loader2 size={15} className="spin" /> : <ScanSearch size={15} />}
                扫描
              </button>
            </div>
          </div>
        ) : (
          <div className="lg-lib__workshop">
            {workshopData ? (
              workshopData.found ? (
                <>
                  <p><Gamepad2 size={14} />已自动识别 Steam 库中的创意工坊壁纸</p>
                  <code className="lg-path" title={workshopData.roots.join("\n")}>
                    {workshopData.roots[0]}
                    {workshopData.roots.length > 1 ? ` 等 ${workshopData.roots.length} 个库` : ""}
                  </code>
                </>
              ) : (
                <p><Gamepad2 size={14} />未检测到 Wallpaper Engine 创意工坊</p>
              )
            ) : (
              <p><Loader2 size={14} className="spin" />正在检测 Steam 库…</p>
            )}
            <small>带 <code>scene.pkg</code> 的场景壁纸标「实时渲染」：应用后在页面内按图层实时绘制，预览图只当封面。标「静态预览」＝浏览器渲染不了该场景 / 网页，应用的是作品自带预览图；再标「低清」表示那张预览图分辨率过低（全屏会糊，原版效果需在 Wallpaper Engine 查看）。</small>
          </div>
        )}

        <div className="lg-lib__bar">
          <div className="lg-lib__tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "normal"}
              className={tab === "normal" ? "active" : ""}
              onClick={() => switchTab("normal")}
            >
              正常列表 ({pageData?.visible_total ?? 0})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "hidden"}
              className={tab === "hidden" ? "active" : ""}
              onClick={() => switchTab("hidden")}
            >
              已隐藏 ({pageData?.hidden_total ?? 0})
            </button>
          </div>
          <div className="lg-lib__filters">
            {([
              ["all", "全部"],
              ["video", "视频"],
              ["image", "图片"],
            ] as [WallpaperLibraryKind, string][]).map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={kind === value ? "active" : ""}
                onClick={() => { setKind(value); setPage(1); }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="lg-lib__search">
          <Search size={14} aria-hidden="true" />
          <input
            id="lg-lib-search"
            value={queryDraft}
            placeholder={source === "workshop" ? "搜索作品名称 / 创意工坊 id…" : "搜索壁纸名称…"}
            aria-label="搜索壁纸名称"
            spellCheck={false}
            onChange={(e) => setQueryDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.stopPropagation();
                clearQuery();
              }
            }}
          />
          {queryDraft && (
            <button
              type="button"
              className="lg-lib__search-clear"
              aria-label="清空搜索"
              onClick={clearQuery}
            >
              <X size={13} />
            </button>
          )}
        </div>

        <p className="lg-lib__summary">
          {summary}
          {source === "folder" && data?.truncated && (
            <span className="lg-lib__warn">文件夹过大，仅扫描前 {data.total} 个</span>
          )}
        </p>

        {activeError ? (
          <div className="lg-lib__state">
            <strong className="lg-lib__error">扫描失败</strong>
            <span>{activeError}</span>
            <span>
              {source === "folder"
                ? "请确认地址存在且后端已启动，然后重新扫描。"
                : "请确认后端已启动；创意工坊会从 Steam 库自动发现。"}
            </span>
          </div>
        ) : source === "workshop" ? (
          !workshopData ? (
            <div className="lg-lib__state">
              <Loader2 size={26} className="spin" />
              <span>正在扫描 Wallpaper Engine 壁纸…</span>
            </div>
          ) : !workshopData.found ? (
            <div className="lg-lib__state">
              <Gamepad2 size={30} />
              <strong>未检测到 Wallpaper Engine 创意工坊</strong>
              <span>已检查 Steam 库的 steamapps/workshop/content/431960。</span>
              <span>请确认 Steam 与 Wallpaper Engine 已安装并订阅壁纸，或切换到本地文件夹。</span>
            </div>
          ) : workshopData.items.length === 0 ? (
            <div className="lg-lib__state">
              <strong>
                {query
                  ? `没有匹配「${query}」的创意工坊壁纸`
                  : tab === "hidden"
                    ? "没有隐藏的创意工坊壁纸"
                    : kind === "all" ? "没有可用的创意工坊壁纸" : "没有匹配该类型的创意工坊壁纸"}
              </strong>
              <span>
                {query
                  ? "换一个关键词，或清空搜索。"
                  : tab === "hidden"
                    ? "在正常列表中点击卡片右上角的眼睛，即可把壁纸移到这里。"
                    : "换一个类型筛选看看，或切换到本地文件夹。"}
              </span>
            </div>
          ) : (
            <div className="lg-lib__grid">
              {workshopData.items.map((item) => (
                <LibraryCard
                  key={item.path}
                  item={item}
                  folder={item.folder}
                  applied={activePath === item.path}
                  hiddenView={tab === "hidden"}
                  onApply={() => applyItem(item)}
                  onToggleHidden={() => toggleHidden(item)}
                />
              ))}
            </div>
          )
        ) : !trimmedDir ? (
          <div className="lg-lib__state">
            <FolderOpen size={30} />
            <strong>先填写壁纸存放地址</strong>
            <span>支持子文件夹递归扫描：图片（png / jpg / webp / gif / bmp / avif）与视频（mp4 / webm / mov / m4v / ogv）。</span>
            <span>缩略图缓存在应用数据目录，不会往你的壁纸文件夹里写任何文件。</span>
          </div>
        ) : loading && !data ? (
          <div className="lg-lib__state">
            <Loader2 size={26} className="spin" />
            <span>正在扫描壁纸…</span>
          </div>
        ) : (data?.items.length ?? 0) === 0 ? (
          <div className="lg-lib__state">
            <strong>
              {query
                ? `没有匹配「${query}」的壁纸`
                : tab === "hidden" ? "没有隐藏的壁纸" : kind === "all" ? "这个文件夹里没有壁纸" : "没有匹配该类型的壁纸"}
            </strong>
            <span>
              {query
                ? "换一个关键词，或清空搜索。"
                : tab === "hidden" ? "在正常列表中点击卡片右上角的眼睛，即可把壁纸移到这里。" : "换个地址或类型筛选试试。"}
            </span>
          </div>
        ) : (
          <div className="lg-lib__grid">
            {data?.items.map((item) => (
              <LibraryCard
                key={item.path}
                item={item}
                folder={data.folder}
                applied={activePath === item.path}
                hiddenView={tab === "hidden"}
                onApply={() => applyItem(item)}
                onToggleHidden={() => toggleHidden(item)}
              />
            ))}
          </div>
        )}

        <footer className="lg-lib__pager">
          <span>
            {pageData
              ? `共 ${pageData.total} 个 · 第 ${pageData.page}/${pageData.pages} 页`
              : "共 0 个 · 第 1/1 页"}
          </span>
          <div className="lg-lib__pager-actions">
            <button
              type="button"
              className="lg-btn"
              disabled={!pageData || pageData.page <= 1 || activeLoading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              <ChevronLeft size={14} />上一页
            </button>
            <button
              type="button"
              className="lg-btn"
              disabled={!pageData || pageData.page >= pageData.pages || activeLoading}
              onClick={() => setPage((p) => p + 1)}
            >
              下一页<ChevronRight size={14} />
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}
