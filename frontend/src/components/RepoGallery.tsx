import { useEffect, useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { Check, ChevronDown, ExternalLink, Eye, FileText, Images, Music, Play, Plus, RotateCcw, Search, Send, Sparkles, Tag, Trash2, X } from "lucide-react";
import { activeVlmChat, vlmGapMessage, useSettings } from "../stores/settings";
import {
  listGenerations, deleteDoc, setTags, describeImage, tagStats, pruneGenerations, pruneOrphanRepos,
  searchGenerations, setGenerationDescription, type Generation,
  indexVisualGenerations, recordVisualPreference, type VisualPreferenceReason,
  generateThumbs, listTaggers, tagGeneration, listTagSnapshots, activateTagSnapshot,
  type TaggerInfo, type TagSnapshot,
  // P1-3 结构化搜索（FTS5）＋ P1-2 回收站（2026-10-18）
  searchAssets, listTrash, restoreAsset, purgeAsset, clearTrash, tagFromText,
  describeMissingAssets,
} from "../api/ai";
import { parseAssetQuery, removeTagFromQuery } from "../lib/assetQuery";
import { useOverlayRoute } from "../lib/overlayRoute";
import { distributeMasonry, masonryColumnCount } from "../lib/masonry";
import { shouldLoadMore } from "../lib/infiniteScroll";
import { subscribeJobActivities } from "../lib/jobActivity";
import { createJob, tickJob, finishJob } from "../api/jobs";
import { imageDataUri } from "../lib/imageData";
import { AI_TAG_PROMPT, fmtCreatedAt, seqOf, TAG_MODE_HINT, TAG_MODES, VISION_DESC_PROMPT, VLM_TWO_PARA_PROMPT, type TagMode } from "../lib/repoGalleryShared";

import { ConfirmModal } from "./Modal";
import { CopyButton } from "./CopyButton";
import { openLightbox } from "./Lightbox";
import { RepoSyncModal } from "./RepoSyncModal";
import { Pager } from "./Pager";
import { ToastLayer } from "./canvas/ToastLayer";
import type { ToastItem } from "./canvas/CanvasTypes";

// 合并展示时给每张图附带来源仓库 id（资产库按仓库名搜索用）
export type GenWithRepo = Generation & { repoId?: string };


/** 详情面板分区：小标题 + 上分隔线 + 留白（2026-10-16 加分层感；此前各段平铺成一坨）。 */
function DetailSection({ title, extra, children, first = false }: {
  title: string; extra?: ReactNode; children: ReactNode; first?: boolean;
}) {
  return (
    <div style={{
      marginTop: first ? 0 : 14,
      paddingTop: first ? 0 : 10,
      borderTop: first ? "none" : "1px solid var(--border)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: 11.5, color: "var(--text-muted)", letterSpacing: 0.4 }}>{title}</span>
        {extra != null && <span style={{ marginLeft: "auto", display: "flex", alignItems: "center" }}>{extra}</span>}
      </div>
      {children}
    </div>
  );
}

/** 两列键值行：左标签定宽灰字、右值自适应（比用全角空格凑对齐稳）。 */
function DetailKV({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 10, fontSize: 12.5, lineHeight: 1.9 }}>
      <span style={{ color: "var(--text-muted)", width: 58, flexShrink: 0 }}>{label}</span>
      <span style={{ flex: 1, minWidth: 0, wordBreak: "break-all" }}>{children}</span>
    </div>
  );
}


// 标签条里的胶囊下拉（2026-10-18 用户实拍：原生 select 白底高框、文字被截成
// 「v1·wd-si…」，和周围胶囊标签/小图标按钮格格不入）。改用同一套语言：
// 虚线胶囊（同 .tag-add）+ 浮层列表（同 .tag-menu），选中项打勾。
function TagPillSelect({
  label, title, options, value, onChange,
}: {
  label: string;
  title?: string;
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);
  const active = options.find((o) => o.value === value);
  return (
    <span className="tag-pill-select" ref={ref} onClick={(e) => e.stopPropagation()}>
      <button type="button" className={`tag-pill ${open ? "open" : ""}`}
        title={title} aria-haspopup="listbox" aria-expanded={open}
        onClick={() => setOpen((v) => !v)}>
        <span className="tag-pill-label">{active?.label ?? label}</span>
        <ChevronDown size={11} className="tag-pill-caret" />
      </button>
      {open && (
        <div className="tag-pill-pop" role="listbox">
          {options.map((o) => (
            <button key={o.value} type="button" role="option" aria-selected={o.value === value}
              className={o.value === value ? "on" : ""}
              onClick={() => { onChange(o.value); setOpen(false); }}>
              <Check size={11} className="tag-pill-check"
                style={{ opacity: o.value === value ? 1 : 0 }} />
              {o.label}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

// 单张图的标签编辑条：加/删标签、AI 打标、WD14 反推、按标签搜索。
function GalleryTags({
  g, tagging, onAdd, onRemove, onTag, mode, onMode, taggers, taggerModel, onTagger,
  onActivateSnapshot, onSearch, onAppendSearch, onClear,
}: {
  g: Generation; tagging: boolean;
  onAdd: (t: string) => void; onRemove: (t: string) => void;
  /** 统一反推入口（按 mode 分派：wd14 / ai / vlm 两段式） */
  onTag: () => void;
  mode: string; onMode: (mode: string) => void;
  taggers: TaggerInfo[]; taggerModel: string; onTagger: (id: string) => void;
  onActivateSnapshot: (snapshotId: string) => void;
  onSearch: (t: string) => void; onAppendSearch: (t: string) => void; onClear: () => void;
}) {
  const snaps: TagSnapshot[] = (() => {
    try {
      const arr = JSON.parse(g.tag_snapshots || "[]");
      return Array.isArray(arr) ? (arr as TagSnapshot[]) : [];
    } catch { return []; }
  })();
  const [adding, setAdding] = useState(false);
  const [val, setVal] = useState("");
  const [menuTag, setMenuTag] = useState<string | null>(null);  // 当前弹悬浮框的标签
  const rootRef = useRef<HTMLDivElement | null>(null);
  const commit = () => { if (val.trim()) onAdd(val.trim()); setVal(""); setAdding(false); };
  // 回车：存下当前标签后不关闭，清空并留在输入框，接着输下一个（连续加标签）
  const commitContinue = () => { if (val.trim()) onAdd(val.trim()); setVal(""); };

  // 点标签外部（含弹框外）关闭悬浮菜单
  useEffect(() => {
    if (!menuTag) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setMenuTag(null);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menuTag]);

  return (
    <div className="gallery-tags" ref={rootRef} onClick={(e) => e.stopPropagation()}>
      {g.tags.map((t) => (
        <span key={t} className="tag-chip" style={{ position: "relative" }}>
          <span className="tag-chip-label" onClick={() => setMenuTag(menuTag === t ? null : t)}>{t}</span>
          <X size={11} className="tag-chip-x" onClick={() => onRemove(t)} />
          {menuTag === t && (
            <div className="tag-menu" onClick={(e) => e.stopPropagation()}>
              <button onClick={() => { onSearch(t); setMenuTag(null); }}>🔍 搜索此标签</button>
              <button onClick={() => { onAppendSearch(t); setMenuTag(null); }}>＋ 添加此标签</button>
            </div>
          )}
        </span>
      ))}
      {adding ? (
        <input
          autoFocus
          className="tag-input"
          value={val}
          onChange={(e) => setVal(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commitContinue(); } if (e.key === "Escape") { setVal(""); setAdding(false); } }}
          placeholder="标签"
        />
      ) : (
        <button className="tag-add" title="加标签" onClick={() => setAdding(true)}>
          <Plus size={11} />
        </button>
      )}
      {/* 统一反推入口：一个按钮 + 模式选择（2026-10-18 用户定案，三个机制不再各占一键）。
          图标随模式变（#3：desc 模式不再与其他模式共用同一图标，避免误以为会出标签）。 */}
      <button className="tag-add" title={TAG_MODE_HINT[mode] ?? "反推打标"}
        disabled={tagging} onClick={onTag}>
        {tagging ? "…"
          : mode === "desc" ? <FileText size={11} />
          : mode === "wd14" ? <Tag size={11} />
          : mode === "ai" ? <Eye size={11} />
          : <Sparkles size={11} />}
      </button>
      <TagPillSelect
        label="反推模式"
        title={TAG_MODE_HINT[mode] ?? "反推模式"}
        value={mode}
        onChange={onMode}
        options={TAG_MODES}
      />
      {snaps.length > 0 && (
        <TagPillSelect
          label={`版本 v${snaps.length}`}
          title={`标签版本（每次反推存一版，共 ${snaps.length} 版；切换只换当前生效版，不丢旧版）`}
          value={g.active_tag_snapshot || snaps[snaps.length - 1].id}
          onChange={onActivateSnapshot}
          options={snaps.map((s, i) => ({
            value: s.id,
            label: `v${i + 1} · ${s.model || "tag"} · ${s.tags.length} 标签`,
          }))}
        />
      )}
      {/* WD14 模型选择只在 WD14 模式下有意义 */}
      {mode === "wd14" && taggers.filter((t) => t.available).length > 1 && taggerModel && (
        <TagPillSelect
          label="打标模型"
          title="反推打标模型"
          value={taggerModel}
          onChange={onTagger}
          options={taggers.filter((t) => t.available).map((t) => ({ value: t.id, label: t.name }))}
        />
      )}
      {g.tags.length > 0 && (
        <button className="tag-add" title="清除全部标签" onClick={onClear}>
          <Trash2 size={11} />
        </button>
      )}
    </div>
  );
}

const MEDIA_KIND_LABEL = { all: "全部", image: "图片", video: "视频", audio: "音频" } as const;

// 仓库图片网格：拉取一组仓库的生成记录（图+提示词），点击看大图与生成参数。
// repoIds 传多个时合并展示（顶层仓库聚合自身 + 所有子仓库的图）。
// 支持：出图后自动刷新、超量折叠、删除资产索引（保留本机文件）。
export function RepoGallery({ repoIds, embed, repoNames, hideTitle, enhanced, onSendToChat, onSendAsRecipe, onBatchSendToCanvas }: {
  repoIds: string[];
  embed: ReturnType<typeof useSettings>["settings"]["embedModel"];
  repoNames?: Record<string, string>;
  hideTitle?: boolean;
  enhanced?: boolean;                          // 资产库页开：批量删除/标签统计排序/发送至对话
  onSendToChat?: (g: GenWithRepo) => void;     // 发送至对话框（仅图片，弹框选仓库由上层处理）
  onSendAsRecipe?: (g: GenWithRepo) => void;   // 发送至对话（完整配方：提示词+图片/参考图）
  onBatchSendToCanvas?: (items: GenWithRepo[]) => void;  // 批量发送至画布（选目标小仓库）
}) {
  const { settings } = useSettings();
  const [items, setItems] = useState<GenWithRepo[]>([]);
  const [loading, setLoading] = useState(true);
  const [active, setActive] = useState<GenWithRepo | null>(null);
  // 素材详情开着就占一段历史：手机返回键先关详情，而不是退掉资产库（#95）。
  useOverlayRoute(active ? "asset-detail" : null, () => setActive(null));
  // 详情面板的图片实际像素尺寸（图片加载后读 naturalWidth/Height；与画布详情同款做法）
  const [detailImgSize, setDetailImgSize] = useState<{ w: number; h: number } | null>(null);
  const [page, setPage] = useState(1);
  const [deleting, setDeleting] = useState<Generation | null>(null);
  const [tagQuery, setTagQuery] = useState("");      // 标签/提示词搜索
  const [tagging, setTagging] = useState<string | null>(null);  // 正在 AI 打标的图 id
  // 可插拔打标器：可用模型列表 + 当前选择（默认取后端 default 且已安装的）
  const [taggers, setTaggers] = useState<TaggerInfo[]>([]);
  const [taggerModel, setTaggerModel] = useState("");
  // 反推模式（统一入口用）：vlm 两段式 / wd14 本地 / ai 视觉模型；存 localStorage 跨会话记住
  const [tagMode, setTagMode] = useState<string>(() => {
    try {
      const saved = localStorage.getItem("demiurge.tagMode");
      return saved && TAG_MODES.some((m) => m.value === saved) ? saved : "vlm";
    } catch { return "vlm"; }
  });
  useEffect(() => {
    try { localStorage.setItem("demiurge.tagMode", tagMode); } catch { /* 隐私模式忽略 */ }
  }, [tagMode]);
  useEffect(() => {
    let alive = true;
    listTaggers().then((r) => {
      if (!alive) return;
      setTaggers(r.items);
      const chosen = r.items.find((t) => t.id === r.default && t.available)?.id
        || r.items.find((t) => t.available)?.id || "";
      setTaggerModel((cur) => cur || chosen);
    }).catch(() => { /* 后端未就绪时静默走默认 */ });
    return () => { alive = false; };
  }, []);
  // Toast 队列：非阻断状态提示（打标/删除/描述/索引等）用 2s 自动渐隐弹窗替代模态框
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const toastIdRef = useRef(0);
  const showToast = useCallback((msg: string, kind: "info" | "error" | "success" = "info") => {
    const id = ++toastIdRef.current;
    setToasts((prev) => [...prev, { id, msg, kind }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 2200);
  }, []);
  const [selMode, setSelMode] = useState(false);     // 批量选择模式（增强页开）
  const [selected, setSelected] = useState<Set<string>>(new Set());  // 选中的图 id
  const [batchDel, setBatchDel] = useState(false);   // 批量删除确认
  // 2026-10-18：浏览器侧检测到的裂图 id（缩略图与原图都加载失败）。
  // 后端「清理裂图」按磁盘/probe 判定，外部死链探测失败时会保守保留 ⇒
  // 这里以**渲染事实**兜底：工具栏出「删除 N 条裂图记录」，走既有批量删除确认。
  const [brokenIds, setBrokenIds] = useState<Set<string>>(new Set());
  // P1-3 结构化搜索（tag: 语法 ⇒ 服务端 FTS5）
  const [searching, setSearching] = useState(false);
  const [searchItems, setSearchItems] = useState<GenWithRepo[]>([]);
  // P1-2 回收站（软删除记录：恢复 / 彻底删除 / 清空）
  const [trashMode, setTrashMode] = useState(false);
  const [trashItems, setTrashItems] = useState<GenWithRepo[]>([]);
  const [trashLoading, setTrashLoading] = useState(false);
  const [purgeTarget, setPurgeTarget] = useState<GenWithRepo | null>(null);
  const [restoringId, setRestoringId] = useState("");
  const [clearConfirm, setClearConfirm] = useState(false);
  const [showTagCloud, setShowTagCloud] = useState(false);  // 标签统计面板
  // P1-6（2026-10-28）：维护类动作（同步体检/描述/索引/缩略图/清理裂图/孤儿仓库）
  // 从工具栏按钮流收进「维护」下拉，工具栏只留查找—筛选—维护三层。
  const [maintOpen, setMaintOpen] = useState(false);
  const maintRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!maintOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!maintRef.current?.contains(e.target as Node)) setMaintOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMaintOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [maintOpen]);
  const [allTags, setAllTags] = useState<{ tag: string; count: number }[]>([]);  // 后端全量标签统计
  const [semanticMode, setSemanticMode] = useState(false);
  const [semanticItems, setSemanticItems] = useState<GenWithRepo[]>([]);
  // 批量视觉描述：提交给后端后只持有 job_id（2026-10-18）。
  // 原浏览器端循环跑有三个硬伤：只覆盖已加载条目、切页即断、没有持久化进度。
  const [describeJobId, setDescribeJobId] = useState("");
  const [visualIndexing, setVisualIndexing] = useState(false);
  const [thumbnailing, setThumbnailing] = useState(false);   // P0-2：补生成缩略图中
  // 切换详情对象时重置图片尺寸（读新图的 naturalWidth/Height）
  useEffect(() => { setDetailImgSize(null); }, [active]);

  // 详情参数键名归一：后端默认路径给 snake、keyset 分页路径给 camel（两套都要认，否则静默空）
  const dTemplate = active ? (active.templateName || active.template_name || "") : "";
  const dModel = active ? (active.modelName || active.model_name || "") : "";
  const dLoras = active
    ? (Array.isArray(active.loraNames) && active.loraNames.length > 0
      ? active.loraNames
      : (active.lora_names || "").split(",").map((s) => s.trim()).filter(Boolean))
    : [];

  const [preferenceBase, setPreferenceBase] = useState<GenWithRepo | null>(null);
  const [preferenceReason, setPreferenceReason] = useState<VisualPreferenceReason>("character");
  const [savingPreference, setSavingPreference] = useState(false);
  const [mediaFilter, setMediaFilter] = useState<"all" | "image" | "video" | "audio">("all");  // M2.2 媒体类型筛选（P2-2 补音频）
  const [hasMore, setHasMore] = useState(false);         // P0-1：分页模式是否还有下一页
  const [totalCount, setTotalCount] = useState(0);       // 命中总数（后端回；0=未知 ⇒ 退回虚拟页）
  const [loadMoreError, setLoadMoreError] = useState(""); // 翻页失败原因（无限滚动下否则「毫无反应」）
  const loadingMoreRef = useRef(false);                  // 并发翻页闸（见 loadMore 注释）
  const [loadingMore, setLoadingMore] = useState(false); // P0-1：正在加载下一页
  const [fullyLoaded, setFullyLoaded] = useState(false); // P0-1：已升级全量（搜索/筛选触发一次，sticky）
  const prevKeyRef = useRef("");                          // P0-1：检测切仓库（重置回分页浏览态）
  const PAGE_SIZE = 32; // 一行 8 个 × 四行
  const MAX_FETCH = 512;  // 单笔拉取上限（16 页）：跳远页链式补，别一笔打几万条
  // P0-1 分页浏览态（2026-10-18 扩到多仓库）：资产库页 + 未升级全量。
  // 多仓库走后端合并 keyset（repo_ids）⇒ 聚合页不再全量 dump（千图库首屏提速）。
  const browsing = enhanced && !fullyLoaded;

  // AI 打标专用对话模型（与生图主流程用的是同一处配置）
  // 图像描述/反推一律用「视觉大模型（VLM）」——不能用普通对话模型（云对话模型普遍拒识图，
  // 2026-10-18 用户实测「素材/资产生成描述后仍空」根因就在此：claude 经第三方代理拒答）。
  const vlmChat = activeVlmChat(settings);

  const key = repoIds.join(",");
  const reposSet = repoIds;

  // P0-1 分页浏览态：资产库页 + 未升级全量。needFull=false 时首屏只拉一页。
  // 多仓库由后端合并 keyset（同一游标各库取 limit+1 后归并）⇒ 也不再全量。
  const needFull = !enhanced || fullyLoaded;

  // P1-3（2026-10-18）：`tag:xxx` 语法 ⇒ 服务端结构化搜索（FTS5 + asset_tags 精确 AND）。
  // 无 tag: 前缀时维持旧口径（自由文本/仓库名，走客户端过滤），不改变既有输入习惯。
  const structured = parseAssetQuery(tagQuery);
  const serverSearch = enhanced && !trashMode && structured.tags.length > 0;

  const refresh = (retryLeft = 1, full = needFull) => {
    let alive = true;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    setLoading(true);
    setBrokenIds(new Set());   // 2026-10-18：刷新/切仓库后旧裂图集合作废（防计数虚高）
    // 单仓库传字符串、多仓库传数组（后端走 list_generations_multi 合并分页）
    const scope: string | string[] = repoIds.length === 1 ? repoIds[0] : repoIds;
    // 媒体类型下推 SQL（2026-10-19）：total 与 items 同口径，分页条才是真页数
    const media = mediaFilter === "all" ? "" : mediaFilter;
    // ⚠ 分页元信息与 items 必须同批更新：hasMore 先翻转会让 effect 在「items 还是空」
    //   那一帧跑完一次然后早退（本轮「滚到底不加载」的真根因），故统一放到第二段 then。
    let pageMeta = { hasMore: false, total: 0 };
    const firstPage = () => listGenerations(scope, embed, { limit: PAGE_SIZE, mediaType: media })
      .then((r) => {
        pageMeta = { hasMore: r.has_more === true, total: r.total ?? 0 };
        return (r.items || []).map((g) => ({ ...g, repoId: g.repo_id || repoIds[0] } as GenWithRepo));
      })
      .catch(() => { pageMeta = { hasMore: false, total: 0 }; return [] as GenWithRepo[]; });
    const allPages = () => Promise.all(repoIds.map((id) =>
      listGenerations(id, embed, { mediaType: media })
        .then((r) => (r.items || []).map((g) => ({ ...g, repoId: id } as GenWithRepo)))
        .catch(() => [] as GenWithRepo[]),
    ))
      .then((lists) => {
        // 合并去重（按 id），保留来源仓库 id
        const seen = new Set<string>();
        const merged: GenWithRepo[] = [];
        for (const list of lists) for (const g of list) {
          if (!seen.has(g.id)) { seen.add(g.id); merged.push(g); }
        }
        return merged;
      });
    (full ? allPages() : firstPage())
      .then((merged) => {
        if (!alive) return;
        setItems(merged);
        setHasMore(full ? false : pageMeta.hasMore);
        setTotalCount(full ? merged.length : pageMeta.total);
        // 全量标签统计（新增图/改标签后刷新时同步更新，供输入补全）
        if (enhanced) {
          tagStats(repoIds, embed).then((r) => { if (alive) setAllTags(r.items || []); }).catch(() => {});
        }
        // 首次读到空可能是 Chroma 写入尚未可见（刚出图/刚进仓库），短延迟重试一次兜底
        if (merged.length === 0 && retryLeft > 0) {
          retryTimer = setTimeout(() => { if (alive) refresh(retryLeft - 1, full); }, 800);
        }
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; if (retryTimer) clearTimeout(retryTimer); };
  };

  // 加载下一页 / 直达第 N 页：用 **offset 区间取**（2026-10-19）。
  // 旧实现只走 keyset 游标 ⇒ 前端永远算不出总页数，也只能一页页顺序翻（用户实锤）。
  // 现在 items 始终是「从头累积的前缀」，所以：offset = items.length。
  // ⚠ 单笔有上限（MAX_FETCH）：跳远页若一笔打「目标页*32 − 已加载」，5000 图库跳末页
  //   就是一笔 16 万条的请求。改成链式分段补，界面仍是「加载中…」直到到位。
  // nextPage：加载成功后停留的页码。
  // ⚠ loadingMore 用 ref 双保险：滚动回调可能在同一帧触发多次，
  //   React state 还没刷出来，纯靠 state 判重会并发发起两笔相同 offset 的请求。
  const loadMore = async (nextPage?: number) => {
    // 结构化搜索 / 回收站一次取全，不分页
    if (serverSearch || trashMode) return;
    if (loadingMoreRef.current || items.length === 0) return;
    const wantPage = nextPage ?? (page + 1);
    const target = Math.max(PAGE_SIZE, wantPage * PAGE_SIZE);   // 目标累积条数
    if (totalCount > 0 && items.length >= totalCount) { setHasMore(false); return; }
    if (totalCount === 0 && !hasMore) return;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    try {
      const scope: string | string[] = repoIds.length === 1 ? repoIds[0] : repoIds;
      const media = mediaFilter === "all" ? "" : mediaFilter;
      const batch: GenWithRepo[] = [];
      let loaded = items.length;
      let more = hasMore;
      let total = totalCount;
      while (loaded < target && more) {
        const take = Math.min(MAX_FETCH, target - loaded);
        const r = await listGenerations(scope, embed, { limit: take, offset: loaded, mediaType: media });
        const got = (r.items || []).map((g) => ({ ...g, repoId: g.repo_id || repoIds[0] } as GenWithRepo));
        batch.push(...got);
        loaded += got.length;
        more = r.has_more === true;
        if (typeof r.total === "number" && r.total > 0) total = r.total;
        if (got.length === 0) break;   // 兜底：空响应别死循环
      }
      // 去重：offset 分页期间若有新图插到最前，会与已加载项重叠（keyset 不会）
      setItems((prev) => {
        const seen = new Set(prev.map((g) => g.id));
        return [...prev, ...batch.filter((g) => !seen.has(g.id))];
      });
      setHasMore(more);
      setTotalCount(total);
      setPage(wantPage);
      setLoadMoreError("");
    } catch (e) {
      // 别静默吞：翻页失败在无限滚动下等于「滚到底毫无反应」，用户无从判断
      setLoadMoreError((e as Error)?.message || "加载失败");
    }
    finally { loadingMoreRef.current = false; setLoadingMore(false); }
    // 等一帧让 React 把新 items 提交进 DOM：跳页的调用方（loadMore(n).then(scroll)）
    // 要能立刻 scrollIntoView 到刚加载出来的项，否则目标节点还不存在 ⇒ 静默不滚动。
    await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
  };

  // ── 无限滚动（2026-10-19）────────────────────────────────────────────
  // ⚠ 不用 IntersectionObserver：本应用滚动发生在 `.app-body`（overflow:auto）这层
  //   内层容器，IO 的 root 默认是视口，跨嵌套滚动 + 祖先裁剪时回调可能永不触发
  //   （用户实锤：滚到最下方毫无反应）。改为直接量哨兵到视口底的距离。
  //   scroll 事件不冒泡，但对 window 用 capture=true 能收到所有内层容器的滚动。
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const lastAutoRef = useRef(0);   // 自动加载节流：失败/连续触发时别把请求打爆
  // 显式跳页的时间窗（毫秒时间戳）：窗口内自动加载链一律让位，避免「点 11 跳 2」
  const suppressAutoRef = useRef(0);
  const jumpRetryRef = useRef(0);  // 跳页遇到在途加载时的重试计数（有界，防无限排自己）
  useEffect(() => {
    if (!browsing || !hasMore) return;
    let raf = 0;
    let timer = 0;
    const check = () => {
      raf = 0;
      const el = sentinelRef.current;
      if (!el || loadingMoreRef.current) return;
      if (loadMoreError) return;                              // 失败态交给用户点重试
      // 显式跳页进行中：自动链必须让位（2026-10-19 用户实锤「点 11 却跳到 2」）
      if (Date.now() < suppressAutoRef.current) return;
      const since = Date.now() - lastAutoRef.current;
      if (since < 800) {
        // 刚加载过：等节流窗口过去再量一次（内容仍不足一屏时要能继续续加载）
        timer = window.setTimeout(check, 800 - since + 20);
        return;
      }
      const rect = el.getBoundingClientRect();
      const vh = window.innerHeight || document.documentElement.clientHeight || 0;
      if (!shouldLoadMore(rect.top, vh, 600)) return;
      lastAutoRef.current = Date.now();
      void loadMore(page + 1);
    };
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(check); };
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    // 挂载/换页/追加后先量一次：内容不足一屏时不必等用户滚动就继续续加载
    timer = window.setTimeout(check, 0);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
      window.clearTimeout(timer);
      if (raf) cancelAnimationFrame(raf);
    };
    // ⚠ items.length 必须在 deps 里：首帧 effect 会在「hasMore 已翻转、items 还没到」
    //   那一刻跑完，此时哨兵尚未渲染（items=0 走空态分支）⇒ 一次早退后再不重跑。
    //   这是本轮「滚到底毫无反应」的真根因（埋点实测 hasEl=false / itemLen=0）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [browsing, hasMore, loadingMore, page, loadMoreError, items.length]);

  // ── 跳页（输入框 / 末页）──────────────────────────────────────────────
  // 有 offset 支持后不再需要「升级全量」：目标页没加载就从当前前缀末尾一次取到那里
  // （limit = 目标页*32 − 已加载数），代价只是一笔请求；加载完滚到该页第一张。
  const scrollToPage = (n: number) => {
    const el = document.querySelector(`[data-gal-page="${n}"]`);
    if (el) el.scrollIntoView({ block: "start", behavior: "smooth" });
  };
  const jumpToPage = (target: number | "last") => {
    const last = Math.max(1, pagerCount);
    const n = target === "last" ? last : Math.max(1, Math.min(last, Math.floor(target) || 1));
    if (n * PAGE_SIZE <= items.length) { jumpRetryRef.current = 0; setPage(n); scrollToPage(n); return; }   // 已加载
    if (clientFiltering) { setPage(Math.min(n, pageCount)); scrollToPage(Math.min(n, pageCount)); return; }
    // ⚠ 2026-10-19 用户实锤：「点 11 页却先跳到 2 页，要再点一次才行」。
    //   根因＝自动加载链正在跑时 `loadingMoreRef` 为 true，`loadMore(n)` 被并发闸
    //   提前 return ⇒ 这次跳页请求**静默丢失**，随后自动链把页码一级级爬上去。
    //   两件防护：① 跳页期间压住自动链（suppressAutoRef）；
    //             ② 若已有加载在途，等它落地再重发跳页（有界重试，别无限排自己）。
    suppressAutoRef.current = Date.now() + 4000;
    if (loadingMoreRef.current) {
      if (jumpRetryRef.current < 50) {
        jumpRetryRef.current += 1;
        window.setTimeout(() => jumpToPage(n), 120);
      }
      return;
    }
    jumpRetryRef.current = 0;
    void loadMore(n).then(() => scrollToPage(n));
  };

  useEffect(() => {
    let cancel: (() => void) | undefined;
    if (prevKeyRef.current !== key) {
      // 切仓库：重置回分页浏览态（强制一页，不沿用上一个仓库的 fullyLoaded）
      prevKeyRef.current = key;
      setFullyLoaded(false);
      setPage(1);
      setHasMore(false);
      setTotalCount(0);
      setLoadMoreError("");
      cancel = refresh(1, false);
    } else {
      cancel = refresh();
    }
    // 出图后（finalize 入库落定后派发）若属于本组仓库则刷新。
    // 延迟一拍 + 二次刷新兜底：Chroma 写入到可检索有轻微延迟，避免刷太早读不到新图。
    const onSaved = (e: Event) => {
      const rid = (e as CustomEvent).detail as string;
      if (!reposSet.includes(rid)) return;
      refresh();
      setTimeout(() => refresh(), 700);
    };
    window.addEventListener("laf-generation-saved", onSaved);
    return () => { cancel?.(); window.removeEventListener("laf-generation-saved", onSaved); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, embed, fullyLoaded]);

  // 自由文本/语义模式一旦启用，本组仓库升级全量加载（sticky：不清回分页，
  // 避免用户逐字输入时在全量/分页间抖动）。多仓库与嵌入画廊本就全量，无需升级。
  // ⚠ 媒体类型**不在**此列：它已下推 SQL（list_generations.media_type），
  //    分页照样能取 ⇒ 不再需要为它升级全量（聚合页也能正常翻页）。
  useEffect(() => {
    const filtering = !!tagQuery.trim() || semanticMode;
    if (enhanced && !serverSearch && repoIds.length === 1 && filtering) setFullyLoaded(true);
  }, [tagQuery, semanticMode, enhanced, repoIds.length, serverSearch]);

  // 媒体类型切换 ⇒ 从第一页重取（服务端筛选，旧列表作废）。
  // 不这么做的话，切到「视频」后 items 还是之前那批图片，翻页只会往后追加。
  useEffect(() => {
    if (serverSearch || trashMode) return;   // 结构化搜索/回收站各自的重取链已覆盖
    setPage(1);
    setItems([]);
    setHasMore(false);
    setTotalCount(0);
    setLoadMoreError("");
    const cancel = refresh(1, fullyLoaded);
    return cancel;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mediaFilter]);

  // P1-3：结构化搜索（tag: 语法）——服务端 FTS5 + asset_tags 精确 AND，千图库不走全量 dump。
  // 一次取全量匹配（SQL 单查询，1000 图级 ~10ms）⇒ 搜索结果不分页，避免与浏览分页语义打架。
  useEffect(() => {
    if (!serverSearch) { setSearchItems([]); return; }
    let alive = true;
    setSearching(true);
    const timer = setTimeout(() => {
      searchAssets(repoIds, embed, {
        q: structured.text, tags: structured.tags,
        mediaType: mediaFilter === "all" ? "" : mediaFilter, limit: 0,
      })
        .then((r) => {
          if (!alive) return;
          setSearchItems((r.items || []).map((g) => ({ ...g, repoId: g.repo_id || repoIds[0] } as GenWithRepo)));
        })
        .catch((e) => { if (alive) { setSearchItems([]); showToast(`搜索失败：${(e as Error).message}`, "error"); } })
        .finally(() => { if (alive) setSearching(false); });
    }, 220);
    return () => { alive = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverSearch, tagQuery, mediaFilter, key]);

  // 退出结构化搜索：回到常规浏览（重新拉一页，避免停在搜索结果）
  const prevSearchRef = useRef(serverSearch);
  useEffect(() => {
    if (prevSearchRef.current === serverSearch) return;
    prevSearchRef.current = serverSearch;
    if (!serverSearch) refresh(1, needFull);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverSearch]);

  // 详情弹窗键盘导航（P3 ③，2026-10-18）：Esc 关闭；←/→ 在当前结果集里切上一张/下一张
  // （正对「审 150 张挑图」：开一张、方向键过片、顺手打标/删，手不离键盘）。
  // 结果集用 ref 持最新（filtered 定义在本 effect 之后，避免前向引用＋免得每渲染重订阅）；
  // 输入框内的方向键留给光标移动，不触发翻页。
  const navListRef = useRef<Generation[]>([]);
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      // 全屏 Lightbox 开着时它是最上层：Esc/方向键都交给它，别连带关掉/切走底层详情弹窗
      // （否则点图看大图后按 Esc 会一口气关两层、直接飞回网格）。
      if (document.querySelector(".lightbox-mask")) return;
      if (e.key === "Escape") { setActive(null); return; }
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      const list = navListRef.current;
      const idx = list.findIndex((g) => g.id === active.id);
      if (idx < 0) return;
      const next = idx + (e.key === "ArrowRight" ? 1 : -1);
      if (next < 0 || next >= list.length) return;
      e.preventDefault();
      setActive(list[next]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);

  // 哪个仓库的库要删这条：repoIds 里逐个尝试（删错库会 no-op）。这里用第一个，
  // 因 image_url 全局唯一，delete_doc 会在系统库+该仓库库定位 id；多仓库时用所属仓库。
  // 删一条：优先用图自带 repoId，回退遍历各仓库库
  const deleteOne = async (g: GenWithRepo, removeFile = false) => {
    const tryIds = g.repoId ? [g.repoId, ...repoIds] : repoIds;
    for (const rid of tryIds) {
      try { await deleteDoc(g.id, rid, embed, removeFile); return; }
      catch { /* 该库没有则试下一个 */ }
    }
  };
  const doDelete = async (g: GenWithRepo) => {
    const removeFile = settings.galleryRemoveFile === true;
    await deleteOne(g, removeFile);
    setDeleting(null);
    setItems((prev) => prev.filter((x) => x.id !== g.id));
    if (removeFile) showToast("已删除资产记录及本机图片文件", "success");
    else showToast("已删除资产记录（本机文件保留）", "success");
  };

  // 批量删除选中项
  const doBatchDelete = async () => {
    setBatchDel(false);
    const ids = new Set(selected);
    const targets = items.filter((x) => ids.has(x.id));
    const removeFile = settings.galleryRemoveFile === true;
    for (const g of targets) await deleteOne(g, removeFile);
    setItems((prev) => prev.filter((x) => !ids.has(x.id)));
    setBrokenIds((prev) => {
      const next = new Set(prev);
      for (const id of ids) next.delete(id);   // 删掉的裂图记录从计数集合里摘掉
      return next;
    });
    setSelected(new Set());
    setSelMode(false);
    if (removeFile) showToast(`已删除 ${targets.length} 条资产记录及本机图片文件`, "success");
    else showToast(`已删除 ${targets.length} 条资产记录（本机文件保留）`, "success");
  };
  const toggleSel = (id: string) =>
    setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  // 清理裂图：删除指向本机图但磁盘文件已不存在的僵尸记录（多因手动删文件留下）。逐仓库清后刷新。
  // 请求失败的仓库单独计failed：不能把后端异常伪装成「没有发现裂图记录」。
  const [pruning, setPruning] = useState(false);
  const doPrune = async () => {
    setPruning(true);
    try {
      let total = 0;
      let failed = 0;
      for (const id of repoIds) {
        try { total += (await pruneGenerations(id, embed)).removed; } catch { failed += 1; }
      }
      if (failed > 0 && total > 0) showToast(`已清理 ${total} 条裂图记录；另有 ${failed} 个仓库请求失败。`, "error");
      else if (failed > 0) showToast(`清理失败：${failed} 个仓库请求出错，请确认后端已启动后重试。`, "error");
      else showToast(total > 0 ? `已清理 ${total} 条裂图记录（磁盘文件已不存在）。` : "没有发现裂图记录。", total > 0 ? "success" : "info");
      refresh();
    } finally { setPruning(false); }
  };

  // 孤儿仓库清理（2026-10-18）：把「有记录但不在注册表、也非合成 repo」的仓库也纳入 prune
  // （此前 doPrune 只扫注册仓库 ⇒ 删了文件夹/丢了注册的仓库，其裂图记录永远清不掉、搜索里冒幽灵）。
  const [orphanPruning, setOrphanPruning] = useState(false);
  const doOrphanPrune = async () => {
    setOrphanPruning(true);
    try {
      const r = await pruneOrphanRepos(embed);
      if (r.orphan_repos.length === 0) {
        showToast("没有孤儿仓库（所有有记录的仓库都在注册表内）。", "info");
      } else {
        showToast(
          r.removed > 0
            ? `发现 ${r.orphan_repos.length} 个孤儿仓库，清理了 ${r.removed} 条失效记录。`
            : `发现 ${r.orphan_repos.length} 个孤儿仓库，但无失效记录可清（文件都还在）。`,
          r.removed > 0 ? "success" : "info",
        );
      }
      refresh();
    } catch (e) {
      showToast(`孤儿仓库清理失败：${(e as Error).message}`, "error");
    } finally { setOrphanPruning(false); }
  };

  // ===== 仓库同步（P3 ⑤，2026-10-18）：只读体检 + 一键修复 =====
  const [showSync, setShowSync] = useState(false);

  // ===== P1-2 回收站（2026-10-18）=====
  const loadTrash = async () => {
    setTrashLoading(true);
    try {
      const r = await listTrash(repoIds, embed);
      setTrashItems((r.items || []).map((g) => ({ ...g, repoId: g.repo_id || repoIds[0] } as GenWithRepo)));
    } catch (e) {
      showToast(`回收站加载失败：${(e as Error).message}`, "error");
    } finally { setTrashLoading(false); }
  };
  useEffect(() => {
    if (!trashMode) { setTrashItems([]); return; }
    void loadTrash();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trashMode, key]);

  const doRestore = async (g: GenWithRepo) => {
    const rid = g.repoId || g.repo_id || repoIds[0];
    setRestoringId(g.id);
    try {
      await restoreAsset(g.id, rid, embed);
      showToast("已恢复到资产库", "success");
      await loadTrash();
      refresh();
    } catch (e) {
      showToast(`恢复失败：${(e as Error).message}`, "error");
    } finally { setRestoringId(""); }
  };
  const doPurge = async (g: GenWithRepo) => {
    const rid = g.repoId || g.repo_id || repoIds[0];
    setPurgeTarget(null);
    try {
      await purgeAsset(g.id, rid, embed);
      showToast("已彻底删除（不可恢复）", "success");
    } catch (e) {
      showToast(`彻底删除失败：${(e as Error).message}`, "error");
    }
    await loadTrash();
  };
  const doClearTrash = async () => {
    setClearConfirm(false);
    try {
      const r = await clearTrash(repoIds, embed);
      showToast(`已清空回收站（${r.removed} 条）`, "success");
    } catch (e) {
      showToast(`清空失败：${(e as Error).message}`, "error");
    }
    await loadTrash();
  };

  // 标签统计：整合所有图的标签，按出现数目降序（供标签云排序/筛选）
  // 标签统计：enhanced 用后端全量 allTags；否则本地聚合当前 items。统一 [标签,数目] 按量降序。
  const tagCounts: [string, number][] = enhanced && allTags.length > 0
    ? allTags.map((s) => [s.tag, s.count] as [string, number])
    : (() => {
        const m = new Map<string, number>();
        for (const g of items) for (const t of g.tags) m.set(t, (m.get(t) || 0) + 1);
        return [...m.entries()].sort((a, b) => b[1] - a[1]);
      })();

  // 更新某图标签：先乐观改本地，再落库（失败回滚）
  const saveTags = async (g: Generation, tags: string[]) => {
    const prev = g.tags;
    setItems((arr) => arr.map((x) => (x.id === g.id ? { ...x, tags } : x)));
    setActive((a) => (a && a.id === g.id ? { ...a, tags } : a));  // 大图弹窗同步
    for (const rid of repoIds) {
      try { await setTags(g.id, rid, tags, embed); return; } catch { /* 试下一个库 */ }
    }
    setItems((arr) => arr.map((x) => (x.id === g.id ? { ...x, tags: prev } : x)));  // 全失败回滚
    setActive((a) => (a && a.id === g.id ? { ...a, tags: prev } : a));
  };
  const addTag = (g: Generation, t: string) => {
    const v = t.trim();
    if (!v || g.tags.includes(v)) return;
    saveTags(g, [...g.tags, v]);
  };
  const removeTag = (g: Generation, t: string) => saveTags(g, g.tags.filter((x) => x !== t));

  // AI 打标：分析图片本身提取标签（始终反推图片），结果追加到现有标签。人工点才触发。
  // 注意：g.image_url 是后端本地代理地址(127.0.0.1)，外部视觉模型访问不到，必须先取回转 dataURI 再喂模型。
  // 旧的「AI 打标」独立入口已并入统一反推（runTag 的 ai 分支）——2026-10-18 用户定案

  // 反推打标（版本化）：每次打标存一版快照，可切换；不合并旧版（2026-10-17 用户定案）
  const refreshSnapshots = async (id: string, repoId: string) => {
    try { return await listTagSnapshots(id, repoId, embed); } catch { return null; }
  };
  const patchItem = (id: string, patch: Partial<Generation>) => {
    setItems((arr) => arr.map((x) => (x.id === id ? { ...x, ...patch } : x)));
    setActive((a) => (a && a.id === id ? { ...a, ...patch } : a));
  };

  // 反推模式 → 面板用的短标签（单图 job 的 label 用它）
  const tagModeLabel = (mode: string) =>
    TAG_MODES.find((m) => m.value === mode)?.label ?? mode;

  // 单张反推核心（不碰 UI 态）：返回 {ok, summary}。批量/单图都走它。
  // 2026-10-19：拆出来是为了让「单图」和「批量所选」共用同一份逻辑 + 都能登记 job。
  const reverseOne = async (g: Generation, mode: string) => {
    const repoId = (g as GenWithRepo).repoId || g.repo_id || repoIds[0];
    if (mode === "wd14") {
      const result = await tagGeneration(taggerModel, g.id, repoId, embed);
      const snaps = await refreshSnapshots(g.id, repoId);
      patchItem(g.id, {
        tags: result.tags,
        ...(snaps ? { tag_snapshots: JSON.stringify(snaps.snapshots), active_tag_snapshot: snaps.active } : {}),
      });
      return { ok: true, summary: `${result.total} 个标签（第 ${snaps?.snapshots.length ?? 1} 版）` };
    }
    if (!vlmChat) { showToast(vlmGapMessage(settings), "error"); return { ok: false, summary: "未配置视觉模型" }; }
    const imgForModel = await imageDataUri(g.image_url);
    // 纯描述模式：只写描述，不产标签、不动快照
    if (mode === "desc") {
      const r = await describeImage([imgForModel], vlmChat!, VISION_DESC_PROMPT);
      const desc = (r.prompt || "").trim();
      if (!desc) return { ok: false, summary: "模型未返回描述（可能不支持视觉）" };
      await setGenerationDescription(g.id, repoId, desc, embed);
      patchItem(g.id, { description: desc });
      return { ok: true, summary: "已写回视觉描述" };
    }
    const prompt = mode === "vlm" ? VLM_TWO_PARA_PROMPT : AI_TAG_PROMPT;
    const r = await describeImage([imgForModel], vlmChat!, prompt);
    const text = (r.prompt || "").trim();
    if (!text) return { ok: false, summary: "模型未返回内容（可能不支持视觉）" };
    const saved = await tagFromText(g.id, repoId, embed, text, {
      model: mode,
      label: mode === "vlm" ? "两段式" : "视觉打标",
      saveDescription: mode === "vlm",
    });
    const snaps = await refreshSnapshots(g.id, repoId);
    patchItem(g.id, {
      tags: saved.tags,
      ...(saved.description ? { description: saved.description } : {}),
      ...(snaps ? { tag_snapshots: JSON.stringify(snaps.snapshots), active_tag_snapshot: snaps.active } : {}),
    });
    return {
      ok: true,
      summary: saved.saved_description
        ? `${saved.total} 个标签＋描述（第 ${snaps?.snapshots.length ?? 1} 版）`
        : `${saved.total} 个标签（第 ${snaps?.snapshots.length ?? 1} 版）`,
    };
  };

  // 反推一批（并发 3）：登记 job ⇒ 面板显示 X/N；逐张失败不中断其余。
  // 单图也走这条（total=1），保证「后台活动」对反推不再瞎眼（2026-10-19 用户实锤）。
  const runReverseBatch = async (targets: Generation[], mode: string) => {
    if (targets.length === 0) return;
    if (!vlmChat && mode !== "wd14") { showToast(vlmGapMessage(settings), "error"); return; }
    const labelOf = (g: Generation) => g.prompt?.slice(0, 12) || g.id.slice(0, 8);
    let jobId = "";
    try {
      const created = await createJob("tag_infer",
        targets.length > 1 ? `反推标签（${targets.length} 张）` : `反推标签 · ${tagModeLabel(mode)}`,
        targets.length);
      jobId = created.job_id;
      const queue = [...targets];
      let ok = 0; let failed = 0;
      const errs: string[] = [];   // 汇总失败原因，收尾一次性提示（逐张弹会刷屏）
      const worker = async () => {
        while (queue.length > 0) {
          const g = queue.shift();
          if (!g) return;
          setTagging(g.id);
          try {
            const r = await reverseOne(g, mode);
            if (r.ok) ok += 1;
            else { failed += 1; errs.push(`「${labelOf(g)}」：${r.summary}`); }
          } catch (e) {
            failed += 1;
            errs.push(`「${labelOf(g)}」：${(e as Error).message}`);
          } finally {
            await tickJob(jobId);
            // 不在这里清 tagging：并发 3 时先完成的 worker 会把别人的转圈也擦掉
          }
        }
      };
      await Promise.all(Array.from({ length: Math.min(3, targets.length) }, () => worker()));
      const detail = `成功 ${ok} / 失败 ${failed}`;
      await finishJob(jobId, "done", { detail });
      const head = errs.length > 0 ? `${detail}；首条失败：${errs[0]}` : detail;
      showToast(targets.length > 1
        ? `批量反推完成（${head}）——详情里可「复制标签 / 复制提示词」。`
        : errs.length === 0 ? "反推完成：1 张——详情里可「复制标签 / 复制提示词」。" : `反推失败：${errs[0]}`,
        failed === targets.length ? "error" : "success");
    } catch (e) {
      if (jobId) { await finishJob(jobId, "failed", { error: (e as Error).message }); }
      showToast(`反推失败：${(e as Error).message}`, "error");
    } finally {
      setTagging(null);
    }
  };

  const runTag = (g: Generation) => void runReverseBatch([g], tagMode);

  // 批量反推所选（按当前模式；wd14/ai/vlm/desc 四种都支持）
  const runTagBatch = () => {
    const ids = new Set(selected);
    const targets = items.filter((g) => ids.has(g.id));
    if (targets.length === 0) { showToast("请先选择要反推的图片。", "error"); return; }
    void runReverseBatch(targets, tagMode);
  };

  // 切换标签版本快照：**乐观更新**——后端要重嵌入整条文档（实测 ≈1.6s），
  // 不能挡 UI；失败再回滚（2026-10-18 用户反馈「切个快照要 3.5s」）。
  const activateSnapshot = async (g: Generation, snapshotId: string) => {
    const repoId = (g as GenWithRepo).repoId || g.repo_id || repoIds[0];
    const snaps: TagSnapshot[] = (() => {
      try {
        const arr = JSON.parse(g.tag_snapshots || "[]");
        return Array.isArray(arr) ? (arr as TagSnapshot[]) : [];
      } catch { return []; }
    })();
    const target = snaps.find((s) => s.id === snapshotId);
    const prev = { tags: g.tags, active: g.active_tag_snapshot };
    patchItem(g.id, {
      ...(target ? { tags: target.tags } : {}),
      active_tag_snapshot: snapshotId,
    });
    try {
      await activateTagSnapshot(g.id, repoId, snapshotId, embed);
      showToast("已切换到该标签版本（语义索引后台更新）。", "success");
    } catch (e) {
      patchItem(g.id, { tags: prev.tags, active_tag_snapshot: prev.active });
      showToast(`切换版本失败：${(e as Error).message}`, "error");
    }
  };

  // 批量补视觉描述：提交给后端做后台任务（覆盖**当前视图的全部仓库**，不看已加载页）。
  // 在「资产库」页点 ⇒ repoIds 是全部仓库 ⇒ 等价于全库补；在子仓库页点 ⇒ 只补该仓库。
  const describeMissing = async () => {
    if (!vlmChat) { showToast(vlmGapMessage(settings), "error"); return; }
    try {
      const r = await describeMissingAssets(repoIds, {
        base_url: vlmChat.baseUrl, api_key: vlmChat.apiKey,
        model: vlmChat.modelName, proxy: vlmChat.proxyUrl || "",
      }, embed);
      if (!r.total) { showToast("当前范围内所有资产已有视觉描述。", "success"); return; }
      setDescribeJobId(r.job_id);
      showToast(`已提交后台任务：为 ${r.total} 张资产补视觉描述（进度见左下「后台活动」）。`, "success");
    } catch (e) {
      showToast(`提交失败：${(e as Error).message}`, "error");
    }
  };

  // 视觉描述任务：运行中**节流增量刷新**（4s 一次），跑完再整刷一次。
  // 2026-10-18 用户实锤：只在收尾刷新 ⇒ 面板显示 192/307 时第一页仍全是「无描述」，
  // 因为网格停留在页面加载时的快照。后端处理顺序＝created_at desc＝前端首页顺序，
  // 所以边跑边刷就能看到描述逐张出现。
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  useEffect(() => {
    if (!describeJobId) return;
    let lastAt = 0;
    return subscribeJobActivities((list) => {
      const job = list.find((j) => j.id === describeJobId);
      if (!job) return;
      if (job.status === "running") {
        const now = Date.now();
        if (now - lastAt < 4000) return;   // 节流：全量刷新别把 UI 拖死
        lastAt = now;
        refreshRef.current();
        return;
      }
      setDescribeJobId("");
      refreshRef.current();
      const label = job.status === "cancelled" ? "已取消"
        : job.status === "failed" ? `失败：${job.error || "未知"}` : "完成";
      showToast(`视觉描述任务${label}（${job.done}/${job.total}）${job.detail ? `　${job.detail}` : ""}`,
        job.status === "failed" ? "error" : "success");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [describeJobId]);

  const buildVisualIndex = async () => {
    setVisualIndexing(true);
    try {
      // 一次传全部 repo_ids ⇒ 后端登记为一个后台任务，进度在「后台活动」面板显示 1/157
      const result = await indexVisualGenerations(repoIds, embed);
      showToast(`视觉索引完成：${result.indexed} 张（只建搜索向量、不含描述；要描述请点「自动描述缺失资产」）。`, "success");
    } catch (error) {
      showToast(`视觉索引失败：${(error as Error).message}`, "error");
    } finally {
      setVisualIndexing(false);
    }
  };

  // P0-2：历史资产补生成缩略图（本机静态图；远程/裂图跳过）。幂等，可重复跑。
  const buildThumbs = async () => {
    setThumbnailing(true);
    try {
      // 一次传全部 repo_ids ⇒ 后端聚合为一个后台任务，进度在「后台活动」面板显示 X/N
      const result = await generateThumbs(repoIds, embed);
      showToast(`缩略图补生成完成：${result.generated} 张，跳过 ${result.skipped} 张远程或缺失图片。`, "success");
      refresh();  // 刷新让 thumb_url_if_ready 兜底发现的 URL 立即生效
    } catch (error) {
      showToast(`缩略图补生成失败：${(error as Error).message}`, "error");
    } finally {
      setThumbnailing(false);
    }
  };

  const preferActive = async () => {
    if (!active || !preferenceBase || active.id === preferenceBase.id) return;
    const repoId = active.repoId || active.repo_id || repoIds[0];
    const baseRepoId = preferenceBase.repoId || preferenceBase.repo_id || repoIds[0];
    if (repoId !== baseRepoId) {
      showToast("偏好比较必须来自同一个作品，避免不同仓库审美串线。", "error");
      return;
    }
    setSavingPreference(true);
    try {
      await recordVisualPreference(
        settings.outputDir || "", repoId, active.id, preferenceBase.id, preferenceReason,
      );
      setPreferenceBase(active);
      showToast("偏好已记录；语义搜索结果会按本作品的选择历史重排，不会自动修改 LoRA。 ", "success");
    } catch (error) {
      showToast(`记录偏好失败：${(error as Error).message}`, "error");
    } finally {
      setSavingPreference(false);
    }
  };

  useEffect(() => {
    if (!semanticMode || !tagQuery.trim()) { setSemanticItems([]); return; }
    let alive = true;
    const timer = setTimeout(() => {
      searchGenerations(repoIds, tagQuery.trim(), embed, 64, settings.outputDir || "")
        .then((result) => {
          if (!alive) return;
          setSemanticItems((result.items || []).map((g) => ({
            ...g, repoId: g.repo_id || repoIds[0],
          })));
        })
        .catch((error) => { if (alive) showToast(`语义搜索失败：${(error as Error).message}`, "error"); });
    }, 250);
    return () => { alive = false; clearTimeout(timer); };
  }, [semanticMode, tagQuery, key, embed]);

  // 标签/提示词搜索：多关键词（空格/逗号/分号分隔），全部命中才显示（AND）。纯前端过滤。
  // 资产库模式额外支持按仓库名搜索：命中仓库名则显示该仓库全部图。
  const terms = tagQuery.toLowerCase().split(/[\s,，;；]+/).filter(Boolean);
  // P1-3：结构化搜索（tag: 语法）与服务端语义搜索都直接吃服务端结果；否则本地 items
  const sourceItems = serverSearch ? searchItems
    : semanticMode && terms.length > 0 ? semanticItems : items;
  const filteredBase = serverSearch || (semanticMode && terms.length > 0)
    ? sourceItems
    : terms.length === 0 ? sourceItems : sourceItems.filter((g) => {
        const repoName = (repoNames?.[(g as GenWithRepo).repoId || ""] || "").toLowerCase();
        const hay = [g.prompt.toLowerCase(), (g.description || "").toLowerCase(),
          ...g.tags.map((t) => t.toLowerCase()), repoName];
        return terms.every((term) => hay.some((h) => h.includes(term)));
      });
  // M2.2 媒体类型筛选（图片/视频/音频）：**已下推 SQL**（2026-10-19）。
  // 旧实现是前端过滤已加载项 ⇒ 聚合页筛「视频」后翻页不会为筛选结果取更多
  // （滚动一直加载却看不到新内容）。现在 list_generations 带 media_type，
  // total 与 items 同口径 ⇒ 分页条页数也准。此处不再本地二次过滤。
  const filtered = filteredBase;
  // 详情弹窗键盘导航用的「当前结果集」：每次渲染后同步进 ref（键盘 ←/→ 在这份列表里切）。
  useEffect(() => { navListRef.current = filtered; });
  // 分页浏览态：后端已按 created_at 倒序返回，不再本地重排（重排会打乱已按页追加的顺序）
  const orderKey = (g: Generation) => g.created_at || seqOf(g.image_url);
  const sorted = serverSearch ? filtered
    : semanticMode && terms.length > 0
      ? filtered
      : browsing ? filtered
        : [...filtered].sort((a, b) => orderKey(b) - orderKey(a));
  // 结构化搜索 / 回收站：一次取全、不分页（搜索走 SQL 单查询；回收站量小）
  const pageCount = (serverSearch || trashMode) ? 1
    : Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  // 客户端筛选（自由文本/语义）时，total 是「未筛选」的总数 ⇒ 不能拿来算页数，
  // 否则会出现「共 N 页但内容只有一页」的假页。此时退回已加载页模型。
  // ⚠ 媒体类型不算：它已下推 SQL，total 与 items 同口径，页数照样准。
  const clientFiltering = !!tagQuery.trim() || semanticMode;
  // 总页数优先用后端给的 total（2026-10-19：只靠游标算不出总页数 ⇒ 用户看到「共 2 页」
  // 却明明有五六页）。total=0（未知/向量库回退）才退回「已加载页 + 虚拟下一页」。
  const useServerTotal = !serverSearch && !trashMode && browsing && totalCount > 0 && !clientFiltering;
  const pagerCount = (serverSearch || trashMode)
    ? pageCount
    : useServerTotal
      ? Math.max(1, Math.ceil(totalCount / PAGE_SIZE))
      : (browsing && hasMore && !clientFiltering ? pageCount + 1 : pageCount);
  // 页码：分页浏览态＝「已加载/当前跳到的页」（由 loadMore/jumpToPage 驱动，确定性）；
  // 全量/搜索态按页切。
  // ⚠ 不做「跟随视口反推页码」：平滑滚动 + 瀑布流重排（宽高比量到后会重新分配列）
  //   会让反推结果抖动，实测点「末页」会先显示 10 再跳回 2——比不跟随更糟。
  const curPage = Math.min(pagerCount, Math.max(1, page));
  // 分页浏览态**连续渲染全部已加载项**（2026-10-19）：切成单页会让「滚动加载」变成
  // 视图跳到下一页而不是往下延伸（用户要的是 danbooru 市场那种连续延伸）。
  const shown = trashMode ? trashItems
    : serverSearch ? sorted
      : browsing ? sorted
        : sorted.slice((curPage - 1) * PAGE_SIZE, curPage * PAGE_SIZE);
  // 项 id → 全局序号（用于「跳到第 N 页」定位到该页第一张）
  const idxOf = useMemo(() => {
    const m = new Map<string, number>();
    sorted.forEach((g, i) => m.set(g.id, i));
    return m;
  }, [sorted]);

  // ── 瀑布流：JS 均列（2026-10-19 替掉 CSS columns）──────────────────────
  // CSS columns 的硬伤：阅读顺序变成列优先 + 列底参差（暗色素材/超宽图混排时一排排空隙）。
  // 这里按「当前最矮的列」投放 ⇒ 列底齐平；宽高比在 img onLoad 时回填
  // （未知按 1 估，量到后批量重排一次）。
  const ratioRef = useRef<Map<string, number>>(new Map());
  const ratioTimer = useRef<number | null>(null);
  const [gridEl, setGridEl] = useState<HTMLDivElement | null>(null);
  const [colCount, setColCount] = useState(6);
  const [ratioTick, setRatioTick] = useState(0);
  useEffect(() => () => { if (ratioTimer.current) window.clearTimeout(ratioTimer.current); }, []);
  // 列数跟随容器宽度。⚠ 必须挂在「节点真的出现」上（回调 ref → state）：
  // 瀑布流只在 items>0 时渲染，挂载首帧拿不到节点，`[]` 依赖的 effect 早已跑完并 return，
  // 观察器永不接管 ⇒ 手机一直停在初始 6 列（390px 下每格 ~50px）。2026-10-27 真机反馈「图被压得很小」的真根因。
  useEffect(() => {
    if (!gridEl || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const n = masonryColumnCount(entries[0]?.contentRect.width ?? 0);
      setColCount((prev) => (prev === n ? prev : n));
    });
    ro.observe(gridEl);
    return () => ro.disconnect();
  }, [gridEl]);
  const noteRatio = (id: string, ratio: number) => {
    if (!(ratio > 0) || ratioRef.current.get(id) === ratio) return;
    ratioRef.current.set(id, ratio);
    if (ratioTimer.current) return;   // 60ms 内的多次 onLoad 合并成一次重排
    ratioTimer.current = window.setTimeout(() => {
      ratioTimer.current = null;
      setRatioTick((t) => t + 1);
    }, 60);
  };
  const masonryCols = useMemo(
    () => distributeMasonry(shown, colCount, (g) => {
      const measured = ratioRef.current.get(g.id);
      if (measured) return measured;
      // 视频/音频单元格是固定 16:9 的占位（CSS aspect-ratio），没有 <img> 可量 ⇒ 直接给已知比例，
      // 否则会按 1（方形）估高，列底立刻参差（本轮「空隙」类问题的同一个病根）。
      // 有封面的视频走 <img> ⇒ 量到的就是视频真实宽高比（2026-10-31：9:16 不再被排成 16:9）。
      if (g.mediaType === "audio" || (g.mediaType === "video" && !g.thumbUrl)) return 16 / 9;
      return 0;
    }),
    // ratioTick：图片量到真实宽高比后重排
    [shown, colCount, ratioTick],
  );

  // 搜索框标签补全：取当前输入最后一段(分隔符后)为前缀，从标签统计里匹配，按图片数降序
  const lastSeg = tagQuery.split(/[,，;；]/).pop()?.trim().toLowerCase() || "";
  const tagSuggest = lastSeg && enhanced
    ? tagCounts.filter(([t]) => t.toLowerCase().includes(lastSeg)).slice(0, 8)
    : [];
  // 选中补全：替换最后一段为选中标签，末尾补分隔符方便接着输
  const pickSuggest = (tag: string) => {
    const head = tagQuery.replace(/[^,，;；]*$/, "");
    setTagQuery(head + tag + "，");
    setPage(1);
  };

  const maintBusy = !!describeJobId || visualIndexing || thumbnailing || pruning || orphanPruning;
  // 结果计数：服务端给了 total 且没有客户端筛选时用 total（无限滚动下已加载 < 总数）；
  // 搜索/回收站/客户端筛选的结果集在内存里，直接数 sorted。
  const matchTotal = trashMode ? trashItems.length
    : (!serverSearch && !clientFiltering && totalCount > 0) ? totalCount : sorted.length;
  const filterBits = [
    trashMode ? "回收站" : "",
    mediaFilter !== "all" ? MEDIA_KIND_LABEL[mediaFilter] : "",
    structured.tags.length ? `标签 ${structured.tags.map((t) => `#${t}`).join(" ")}` : "",
    semanticMode ? "语义搜索" : "",
  ].filter(Boolean);

  return (
    <div style={{ marginTop: hideTitle ? 0 : 28 }}>
      {!hideTitle && <h3 style={{ margin: "4px 0 12px", fontSize: 15 }}>生成内容</h3>}
      {/* P1-3：已选标签 chips（tag: 语法解析结果，点 ✕ 移除该项） */}
      {structured.tags.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", marginBottom: 10 }}>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>标签筛选：</span>
          {structured.tags.map((t) => (
            <button key={t} className="tag-cloud-chip" title="移除该标签"
              onClick={() => { setTagQuery(removeTagFromQuery(tagQuery, t)); setPage(1); }}>
              {t} ✕
            </button>
          ))}
          {searching && <span style={{ fontSize: 12, color: "var(--text-muted)" }}>搜索中…</span>}
        </div>
      )}
      {/* 工具栏常驻：曾经挂在 items.length > 0 上，于是切到「视频／音频」这种本来就没有
          资产的分类时整条工具栏（含分类按钮本身）一起消失，用户被锁在空态里回不去
          （2026-10-29 实锤）。空列表的可自救入口由空态自带，不由工具栏的显隐来兜。 */}
      {enhanced && (
        <div className="asset-toolbar-sticky">
          <div className="page-toolbar asset-toolbar">
            {/* 第一行 · 查找：弹性搜索框 / 全部·图片·视频·音频分段 / 语义搜索 */}
            <div className="asset-toolbar-row asset-toolbar-find">
              <div className="asset-search">
                <Search size={14} className="asset-search-icon" />
                <input
                  className="asset-search-input"
                  placeholder={semanticMode ? "按画面语义搜索，例如：雨夜里穿红裙的角色…"
                    : repoNames ? "按标签或仓库名搜索（tag:xxx 精确标签筛）…" : "按标签搜索（回车确认一个标签后自动补「，」接着输下一个）…"}
                  value={tagQuery}
                  onChange={(e) => { setTagQuery(e.target.value); setPage(1); }}
                  onKeyDown={(e) => {
                    // 回车确认当前标签：末尾补「，」分隔，方便接着输下一个（已是分隔符结尾则不重复补）
                    if (e.key === "Enter" && !semanticMode) {
                      e.preventDefault();
                      setTagQuery((q) => (q.trim() && !/[,，;；]\s*$/.test(q) ? q.trimEnd() + "，" : q));
                    }
                  }}
                />
                {tagSuggest.length > 0 && (
                  <div className="tag-suggest-pop">
                    {tagSuggest.map(([t, n]) => (
                      <button key={t} className="tag-suggest-item" onMouseDown={(e) => { e.preventDefault(); pickSuggest(t); }}>
                        <span>{t}</span>
                        <span className="tag-suggest-count">{n}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <div className="asset-seg" role="group" aria-label="媒体类型">
                {(["all", "image", "video", "audio"] as const).map((m) => (
                  <button
                    key={m}
                    className={`btn ${mediaFilter === m ? "primary" : ""}`}
                    aria-pressed={mediaFilter === m}
                    onClick={() => { setMediaFilter(m); setPage(1); }}
                  >
                    {m === "all" ? "全部" : m === "image" ? "图片" : m === "video" ? "视频" : "音频"}
                  </button>
                ))}
              </div>
              <button className={`btn ${semanticMode ? "primary" : ""}`} aria-pressed={semanticMode}
                onClick={() => { setSemanticMode((value) => !value); setPage(1); }}>
                {semanticMode ? "语义搜索：开" : "语义搜索"}
              </button>
            </div>
            {/* 第二行 · 筛选与维护：批量选择 / 标签统计 / 回收站 / 维护菜单 */}
            <div className="asset-toolbar-row asset-toolbar-manage">
              <button className="btn" onClick={() => { setSelMode((v) => !v); setSelected(new Set()); }}>
                {selMode ? "退出多选" : "批量选择"}
              </button>
              {selMode && (
                <>
                  <button className="btn danger" disabled={selected.size === 0} onClick={() => setBatchDel(true)}>
                    <Trash2 size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />删除所选（{selected.size}）
                  </button>
                  {onBatchSendToCanvas && (
                    <button className="btn" disabled={selected.size === 0}
                      onClick={() => {
                        const ids = new Set(selected);
                        onBatchSendToCanvas(items.filter((g) => ids.has(g.id)));
                      }}>
                      <Send size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />发送至画布（{selected.size}）
                    </button>
                  )}
                  <button className="btn" disabled={selected.size === 0} onClick={runTagBatch}
                    title={`按当前反推模式（${tagModeLabel(tagMode)}）反推选中的图片；并发 3，进度显示在右下「后台活动」`}>
                    <Tag size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />反推所选（{selected.size}）
                  </button>
                  <button className="btn" onClick={() => setSelected(new Set(shown.map((g) => g.id)))}>选本页</button>
                  <button className="btn" disabled={selected.size === 0} onClick={() => setSelected(new Set())}>清除</button>
                </>
              )}
              <button className="btn" onClick={() => setShowTagCloud((v) => !v)}>
                标签统计（{tagCounts.length}）
              </button>
              {items.some((g) => brokenIds.has(g.id)) && (
                <button className="btn danger"
                  onClick={() => { setSelected(new Set(brokenIds)); setBatchDel(true); }}
                  title="删除当前列表中**浏览器检测到**的裂图记录（缩略图与原图都加载失败，如云端过期直链）；走批量删除确认，本机文件按设置处理">
                  <Trash2 size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
                  删除 {items.filter((g) => brokenIds.has(g.id)).length} 条裂图记录
                </button>
              )}
              {/* P1-2 回收站（2026-10-18）：删除改软删，回收站里可恢复 / 彻底删除 / 清空 */}
              <button className={`btn ${trashMode ? "primary" : ""}`} aria-pressed={trashMode}
                onClick={() => { setTrashMode((v) => !v); setSelMode(false); setSelected(new Set()); }}
                title="回收站：删除的资产先放这里，可恢复或彻底删除">
                <RotateCcw size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
                {trashMode ? "退出回收站" : "回收站"}
              </button>
              {trashMode && trashItems.length > 0 && (
                <button className="btn danger" onClick={() => setClearConfirm(true)}>
                  <Trash2 size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />清空回收站（{trashItems.length}）
                </button>
              )}
              <div className="asset-maint" ref={maintRef}>
                <button className="btn" aria-haspopup="menu" aria-expanded={maintOpen}
                  onClick={() => setMaintOpen((v) => !v)}
                  title="维护：同步体检 / 自动描述 / 视觉索引 / 补缩略图 / 清理裂图 / 清理孤儿仓库">
                  维护{maintBusy ? "（进行中）" : ""}
                  <ChevronDown size={14} style={{ verticalAlign: "-2px", marginLeft: 4 }} />
                </button>
                {maintOpen && (
                  <div className="asset-maint-menu" role="menu" aria-label="资产库维护">
                    <button role="menuitem" onClick={() => { setMaintOpen(false); setShowSync(true); }}
                      title="仓库同步体检：对账「注册表/磁盘/索引」三方——补录丢进文件夹但没入库的文件、清理磁盘已没了的裂图/孤儿">
                      同步体检
                    </button>
                    <button role="menuitem" disabled={describeJobId !== ""}
                      onClick={() => { setMaintOpen(false); void describeMissing(); }}
                      title="用视觉模型并发描述【当前视图的全部仓库】内缺描述的图片——不看已加载页、后端跑、切页也不中断。在「资产库」总页点＝补全库；在子仓库页点＝只补该仓库">
                      {describeJobId ? "自动描述缺失资产（进行中）" : "自动描述缺失资产"}
                    </button>
                    <button role="menuitem" disabled={visualIndexing}
                      onClick={() => { setMaintOpen(false); buildVisualIndex(); }}
                      title="把图片画面编码成向量，供「语义搜索按图匹配」用——不生成描述文字。要生成描述请点「自动描述缺失资产」">
                      {visualIndexing ? "构建视觉索引（进行中）" : "构建视觉索引"}
                    </button>
                    <button role="menuitem" disabled={thumbnailing}
                      onClick={() => { setMaintOpen(false); buildThumbs(); }}
                      title="为本仓库历史图片补生成缩略图（长边 512，网格加载更快；新图入库时已自动生成）">
                      {thumbnailing ? "补缩略图（进行中）" : "补缩略图"}
                    </button>
                    <button role="menuitem" disabled={pruning}
                      onClick={() => { setMaintOpen(false); doPrune(); }}
                      title="删除指向本机图但磁盘文件已不存在的裂图记录（多因手动删文件留下）">
                      {pruning ? "清理裂图（进行中）" : "清理裂图"}
                    </button>
                    <button role="menuitem" disabled={orphanPruning}
                      onClick={() => { setMaintOpen(false); doOrphanPrune(); }}
                      title="清理「孤儿仓库」的失效记录：有资产记录但已不在仓库注册表里的仓库（如删了文件夹），其裂图记录平时扫不到，这里统一清掉">
                      {orphanPruning ? "清理孤儿仓库（进行中）" : "清理孤儿仓库"}
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
          {/* 结果计数 / 筛选摘要 */}
          <div className="asset-summary">
            <span>共 {matchTotal} 条</span>
            {shown.length !== matchTotal && <span>已显示 {shown.length} 条</span>}
            {filterBits.length > 0 && <span className="asset-summary-filter">筛选：{filterBits.join(" · ")}</span>}
            {searching && <span>搜索中…</span>}
          </div>
        </div>
      )}
      {enhanced && showTagCloud && (
        <div className="tag-cloud">
          {tagCounts.length === 0 ? (
            <span style={{ color: "var(--text-muted)", fontSize: 13 }}>还没有任何标签。</span>
          ) : tagCounts.map(([t, n]) => (
            <button key={t} className="tag-cloud-chip"
              title={`点击筛选「${t}」（${n} 张）`}
              onClick={() => { setTagQuery(t); setPage(1); }}>
              {t} <span className="tag-cloud-num">{n}</span>
            </button>
          ))}
        </div>
      )}
      {/* P1-2：回收站空态 */}
      {trashMode && !trashLoading && trashItems.length === 0 && (
        <div style={{ color: "var(--text-muted)", fontSize: 13, margin: "10px 0" }}>回收站是空的。</div>
      )}
      {loading ? (
        <div className="gallery-grid">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="gallery-cell skeleton" style={{ aspectRatio: "1 / 1" }} />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="empty-state">
          <Images size={32} strokeWidth={1.4} style={{ opacity: 0.5 }} />
          <p style={{ margin: 0 }}>
            {mediaFilter === "all"
              ? "还没有生成记录。出图后会自动出现在这里。"
              : `${MEDIA_KIND_LABEL[mediaFilter]}还没有资产。用上方「全部／图片」切换查看其它内容。`}
          </p>
          {/* 空态自带「同步体检」：离线同步过来的仓库最容易撞到这里——文件已在磁盘、索引里没有。
              2026-10-26 手机端实锤时工具栏还挂在 items.length > 0 上，这里是唯一出口；
              2026-10-29 工具栏改常驻后仍保留，省得让用户去右上角菜单里找。 */}
          {enhanced && (
            <>
              <p style={{ margin: 0, fontSize: 12.5 }}>资产已在文件夹里、这里却为空＝索引没跟上，可对账补录。</p>
              <button className="btn" onClick={() => setShowSync(true)}
                title="对账「注册表/磁盘/索引」——补录丢进文件夹但没入库的文件、清理磁盘已没了的裂图/孤儿">
                同步体检
              </button>
            </>
          )}
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <Search size={30} strokeWidth={1.4} style={{ opacity: 0.5 }} />
          <p style={{ margin: 0 }}>没有匹配「{tagQuery}」的图片。</p>
        </div>
      ) : (
        <>
          {/* JS 均列瀑布流：外层 flex 行 + 每列一个纵向 flex。
              单元格内容与旧实现完全一致（不裁剪、保留 hover 操作/角标/裂图回退）。 */}
          <div className="gallery-masonry" ref={setGridEl}>
            {masonryCols.map((col, ci) => (
              <div className="gallery-masonry-col" key={ci}>
                {col.map((g) => (
                  <div key={g.id} data-gal-page={Math.floor((idxOf.get(g.id) ?? 0) / PAGE_SIZE) + 1}
                    className={`gallery-cell ${selMode && selected.has(g.id) ? "sel" : ""}`} title={g.prompt}>
                {g.mediaType === "audio" || (g.mediaType === "video" && !g.thumbUrl) ? (
                  <div className="gallery-video-cell" onClick={() => selMode ? toggleSel(g.id) : setActive(g)}>
                    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)", gap: 6 }}>
                      {g.mediaType === "video" ? <Play size={22} /> : <Music size={22} />}
                      {g.mediaType === "video" ? "视频" : "音频"}
                    </div>
                    <span className="gallery-media-badge">{g.mediaType === "video" ? "🎬" : "🎵"}</span>
                  </div>
                ) : (
                  <img src={g.thumbUrl || g.image_url} alt="生成图" loading="lazy"
                    onClick={() => { if (trashMode) return; selMode ? toggleSel(g.id) : setActive(g); }}
                    style={trashMode ? { cursor: "default", opacity: 0.75 } : undefined}
                    onLoad={(e) => {
                      // 量到真实宽高比 ⇒ 回填供均列重排（60ms 批量合并，避免每张都重渲）
                      noteRatio(g.id, e.currentTarget.naturalWidth / e.currentTarget.naturalHeight);
                    }}
                    onError={(e) => {
                      // 2026-10-18 裂图修复：缩略图坏 ⇒ 回退原图；原图也坏 ⇒ 记为裂图
                      // （此前网格无回退，陈旧 thumb_url / 云端死链直接显示破图图标）
                      const el = e.currentTarget;
                      // 视频封面坏了不能回退成 <img src=mp4>：渲染不出，还会被误记成裂图
                      if (el.dataset.fb !== "1" && g.thumbUrl && g.image_url && g.mediaType !== "video") {
                        el.dataset.fb = "1";
                        el.src = g.image_url;
                        return;
                      }
                      setBrokenIds((s) => (s.has(g.id) ? s : new Set(s).add(g.id)));
                      el.style.opacity = "0.15";
                    }} />
                )}
                {g.mediaType === "video" && g.thumbUrl && (
                  <span className="gallery-media-badge">🎬</span>
                )}
                {g.mediaType === "video" && !selMode && (
                  <span className="gallery-media-tag" title="视频资产（点击播放）">视频</span>
                )}
                {trashMode ? (
                  <>
                    <button className="gallery-send-recipe" title={restoringId === g.id ? "恢复中…" : "恢复（回到资产库）"}
                      disabled={restoringId === g.id}
                      onClick={(e) => { e.stopPropagation(); void doRestore(g); }}>
                      <RotateCcw size={13} />
                    </button>
                    <button className="gallery-del" title="彻底删除（不可恢复）"
                      onClick={(e) => { e.stopPropagation(); setPurgeTarget(g); }}>
                      <Trash2 size={14} />
                    </button>
                  </>
                ) : selMode ? (
                  <span className={`gallery-check ${selected.has(g.id) ? "on" : ""}`}>
                    {selected.has(g.id) && <Check size={14} />}
                  </span>
                ) : (
                  <>
                    {enhanced && onSendAsRecipe && (
                      <button className="gallery-send-recipe" title="发送至对话（完整配方：提示词+图片/参考图）"
                        onClick={(e) => { e.stopPropagation(); onSendAsRecipe(g); }}>
                        <Send size={13} />
                      </button>
                    )}
                    {enhanced && onSendToChat && (
                      <button className="gallery-send" title="发送至对话框（仅图片）"
                        onClick={(e) => { e.stopPropagation(); onSendToChat(g); }}>
                        <Send size={13} />
                      </button>
                    )}
                    <button className="gallery-del" title="从资产库删除（保留本机文件）"
                      onClick={(e) => { e.stopPropagation(); setDeleting(g); }}>
                      <Trash2 size={14} />
                    </button>
                  </>
                )}
                  </div>
                ))}
              </div>
            ))}
          </div>
          {/* 无限滚动哨兵：分页浏览态才有；滚到距底 600px 触发 loadMore */}
          {browsing && hasMore && <div ref={sentinelRef} style={{ height: 1 }} aria-hidden />}
          {browsing && hasMore && (
            <div style={{ textAlign: "center", color: "var(--text-muted)", fontSize: 12, padding: "6px 0 2px" }}>
              {loadMoreError ? (
                <button className="btn" style={{ fontSize: 12, padding: "3px 10px" }}
                  onClick={() => { setLoadMoreError(""); void loadMore(page + 1); }}>
                  加载失败（{loadMoreError}）—— 点此重试
                </button>
              ) : loadingMore ? "正在加载下一页…" : "向下滚动自动加载更多"}
            </div>
          )}
          {/* 分页条吸底常驻：无限滚动下内容会一直变长，留在内容底部等于要滚到最后一页
              才能跳页（用户实锤）。见 styles.css .pager-dock。 */}
          <div className="pager-dock">
            <Pager page={curPage} pageCount={pagerCount}
              onPage={(n) => jumpToPage(n)}
              onFirst={() => jumpToPage(1)}
              onLast={() => jumpToPage("last")}
              always />
            {loadingMore && (
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>加载中…</span>
            )}
          </div>
        </>
      )}
      {deleting && (
        <ConfirmModal
          title="删除生成图"
          message={settings.galleryRemoveFile
            ? "确认从资产库删除这条记录？本机图片文件也将一并删除。"
            : "确认从资产库删除这条记录？本机图片文件会保留。"
          }
          confirmText="删除"
          danger
          onConfirm={() => doDelete(deleting)}
          onCancel={() => setDeleting(null)}
        />
      )}
      {batchDel && (
        <ConfirmModal
          title="批量删除"
          message={settings.galleryRemoveFile
            ? `确认从资产库删除选中的 ${selected.size} 条记录？本机图片文件也将一并删除。`
            : `确认从资产库删除选中的 ${selected.size} 条记录？本机图片文件会保留。`
          }
          confirmText="删除"
          danger
          onConfirm={doBatchDelete}
          onCancel={() => setBatchDel(false)}
        />
      )}
      {/* P1-2 回收站：彻底删除 / 清空都要二次确认（不可恢复） */}
      {purgeTarget && (
        <ConfirmModal
          title="彻底删除"
          message="确认彻底删除这条资产记录？此操作不可恢复。"
          confirmText="彻底删除"
          danger
          onConfirm={() => void doPurge(purgeTarget)}
          onCancel={() => setPurgeTarget(null)}
        />
      )}
      {clearConfirm && (
        <ConfirmModal
          title="清空回收站"
          message={`确认清空回收站里的 ${trashItems.length} 条记录？此操作不可恢复。`}
          confirmText="清空"
          danger
          onConfirm={() => void doClearTrash()}
          onCancel={() => setClearConfirm(false)}
        />
      )}
      {showSync && (
        <RepoSyncModal
          outputDir={settings.outputDir || ""}
          embed={embed}
          onClose={() => setShowSync(false)}
          onFixed={() => refresh()}
        />
      )}
      {active && (
        <div className="modal-mask">
          <div className="modal asset-detail-modal" style={{
            maxWidth: 1060, width: "95vw", maxHeight: "90vh",
            display: "flex", flexDirection: "column",
            // ⚠ 必须覆盖 `.modal` 基线的 `overflow-y: auto`：外层滚动会吃掉高度约束，
            // 内层右栏拿不到确定高度 ⇒ 右栏不滚、底部按钮被内容穿过（2026-10-16 实机截图）
            overflow: "hidden",
          }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <h3 style={{ margin: 0 }}>生成详情</h3>
              <button className="icon-btn" style={{ background: "transparent", color: "var(--text)" }} onClick={() => setActive(null)}>
                <X size={18} />
              </button>
            </div>
            {/* 左图右信息（2026-10-16 重排，对齐画布模式 `CanvasDetailPanel` 的结构）
                ⚠ 不要 flexWrap：换行会让容器高度需求翻倍 ⇒ 撑破 maxHeight、右栏失去滚动
                ⚠ 手机上由 `.asset-detail-body` 切成上下堆叠（390px 左右分栏会把图压到 ~190px） */}
            <div className="asset-detail-body" style={{ flex: 1, minHeight: 0, display: "flex", gap: 14, overflow: "hidden" }}>
              {/* 左：完整图片 / 视频（点击进共享 Lightbox 全屏） */}
              <div style={{ flex: "0 0 52%", minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{
                  flex: 1, minHeight: 0, display: "flex", alignItems: "center",
                  justifyContent: "center", background: "var(--surface-2)",
                  borderRadius: 10, overflow: "hidden",
                }}>
                  {active.mediaType === "video" ? (
                    <video src={active.image_url} controls style={{ maxWidth: "100%", maxHeight: "100%", background: "#000" }} />
                  ) : active.mediaType === "audio" ? (
                    // P2-2 音频：内嵌播放器（与视频同构；音频没有「画面」，居中一个播放条即可）
                    <audio src={active.image_url} controls style={{ width: "100%", maxWidth: 480 }} />
                  ) : (
                    // 保持 P0-2 契约「详情取原图」（质量优先）；原图不可用时（云端图未落盘/
                    // 被清理）onError 回退本机缩略图 —— 否则出现空白框（2026-10-16 实机截图）
                    <img src={active.image_url} alt="大图" title="点击全屏查看（滚轮缩放）"
                      onClick={() => openLightbox(active.image_url)}
                      onLoad={(e) => {
                        const el = e.currentTarget;
                        if (el.naturalWidth > 0) setDetailImgSize({ w: el.naturalWidth, h: el.naturalHeight });
                      }}
                      onError={(e) => {
                        const el = e.currentTarget;
                        const fallback = active.thumbUrl || "";
                        if (fallback && el.dataset.fb !== "1") {
                          el.dataset.fb = "1";
                          el.src = fallback;
                        }
                      }}
                      style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain", cursor: "zoom-in" }} />
                  )}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0, fontSize: 12, color: "var(--text-muted)" }}>
                  <span title={active.image_url} style={{
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 260,
                  }}>
                    {decodeURIComponent((active.image_url || "").split("?")[0].split("/").pop() || "")}
                  </span>
                  <a className="img-tool" href={active.image_url} target="_blank" rel="noreferrer">
                    <ExternalLink size={14} /> 查看原图
                  </a>
                  {(() => {
                    const i = filtered.findIndex((g) => g.id === active.id);
                    return i >= 0
                      ? <span title="← / → 切换上一张/下一张，Esc 关闭">第 {i + 1} / {filtered.length} 张（← → 切换）</span>
                      : null;
                  })()}
                  {active.mediaType === "video" && <span>🎬 视频资产（首帧底图派生链见右）</span>}
                  {active.mediaType === "audio" && <span>🎵 音频资产</span>}
                </div>
              </div>
              {/* 右：生成信息（可滚动；flex-basis 0 才不会被内容顶开） */}
              <div style={{ flex: "1 1 0%", minWidth: 0, minHeight: 0, overflowY: "auto", paddingRight: 4 }}>
              <DetailSection title="生成参数" first>
                {dTemplate && (
                  <DetailKV label="模板">
                    <span style={{ color: "var(--primary, #3b82f6)", fontWeight: 500 }}>{dTemplate}</span>
                  </DetailKV>
                )}
                {dModel && (
                  <DetailKV label="模型"><span style={{ fontFamily: "monospace" }}>{dModel}</span></DetailKV>
                )}
                {dLoras.length > 0 && (
                  <DetailKV label="LoRA">
                    <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap" }}>
                      {dLoras.map((n) => (
                        <span key={n} style={{
                          padding: "1px 6px", borderRadius: 3,
                          background: "rgba(127,127,127,0.16)",
                        }}>{n}</span>
                      ))}
                    </span>
                  </DetailKV>
                )}
                {active.mediaType === "video" ? (
                  <DetailKV label="尺寸">视频</DetailKV>
                ) : detailImgSize ? (
                  <DetailKV label="尺寸">{detailImgSize.w} × {detailImgSize.h}</DetailKV>
                ) : null}
                {fmtCreatedAt(active.created_at) && (
                  <DetailKV label="生成时间">{fmtCreatedAt(active.created_at)}</DetailKV>
                )}
                {(active.repoId || "") && (
                  <DetailKV label="作品">
                    {(repoNames && active.repoId ? repoNames[active.repoId] : "") || active.repoId}
                  </DetailKV>
                )}
              </DetailSection>
              {/* P2-1：素材资产来源（上网素材入库即资产记录；generation 链无此段） */}
              {active.source ? (
                <DetailSection title="来源">
                  <DetailKV label="渠道">
                    {active.source === "web" ? "联网素材" : active.source}
                  </DetailKV>
                  {active.sourceMeta?.author && (
                    <DetailKV label="作者">{active.sourceMeta.author}</DetailKV>
                  )}
                  {active.sourceMeta?.rating && (
                    <DetailKV label="评级">{active.sourceMeta.rating}</DetailKV>
                  )}
                  {active.sourceMeta?.query && (
                    <DetailKV label="搜索词">{active.sourceMeta.query}</DetailKV>
                  )}
                  {active.sourceUrl && (
                    <DetailKV label="来源页">
                      <a href={active.sourceUrl} target="_blank" rel="noreferrer"
                        style={{ color: "var(--primary, #3b82f6)", wordBreak: "break-all" }}>
                        {active.sourceUrl}
                      </a>
                    </DetailKV>
                  )}
                </DetailSection>
              ) : null}
              {active.derivedFrom?.length ? (
                <DetailSection title="派生来源">
                  <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.6, marginBottom: 4 }}>
                    只读展示，删除来源不影响本条
                  </div>
                  {active.derivedFrom.map((d, i) => {
                    const slot = d.media_slot_ref;
                    const label = slot
                      ? `本作品对话中的插画槽（消息 ${slot.message_id.slice(0, 8)}… · 槽 ${slot.slot_id}）`
                      : (d.asset_id ? `资产 ${d.asset_id}` : "来源已删除");
                    return (
                      <div key={i} style={{ background: "rgba(127,127,127,0.1)", borderRadius: 6, padding: "4px 8px", marginBottom: 4 }}>
                        <span style={{ color: "var(--text)" }}>首帧底图</span> ← {label}
                        {d.kind ? <span style={{ opacity: 0.7 }}> · {d.kind}</span> : null}
                      </div>
                    );
                  })}
                </DetailSection>
              ) : null}
              <DetailSection
                title="提示词"
                extra={active.prompt ? <CopyButton text={active.prompt} className="img-tool" /> : null}
              >
                <div style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {active.prompt || "（无提示词记录）"}
                </div>
              </DetailSection>
              <DetailSection title="视觉描述">
                {active.description
                  ? (
                    <>
                      <div style={{ fontSize: 13, lineHeight: 1.6 }}>{active.description}</div>
                      {/* 2026-10-18：描述/标签都补一键复制（用户反馈此前无处可复制） */}
                      <div style={{ marginTop: 6 }}>
                        <CopyButton text={active.description} label="复制描述" className="btn" />
                      </div>
                    </>
                  )
                  : (
                    <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                      还没有视觉描述——把反推模式切到「视觉描述（只写描述）」后点反推即可生成。
                    </div>
                  )}
              </DetailSection>
              <DetailSection title="标签">
                <GalleryTags
                  g={active}
                  tagging={tagging === active.id}
                  onAdd={(t) => addTag(active, t)}
                  onRemove={(t) => removeTag(active, t)}
                  onTag={() => runTag(active)}
                  mode={tagMode}
                  onMode={setTagMode}
                  taggers={taggers}
                  taggerModel={taggerModel}
                  onTagger={setTaggerModel}
                  onActivateSnapshot={(sid) => activateSnapshot(active, sid)}
                  onSearch={(t) => { setTagQuery(`${t}，`); setPage(1); setActive(null); }}
                  onAppendSearch={(t) => {
                    // 追加到已有搜索词，末尾自带「，」方便继续加下一个标签
                    setTagQuery((q) => (q.trim() ? `${q.trim().replace(/[,，;；]\s*$/, "")}，${t}，` : `${t}，`));
                    setPage(1);
                    setActive(null);
                  }}
                  onClear={() => saveTags(active, [])}
                />
                {/* 一键复制：标签（逗号+空格）；以及「两段式」＝标签行＋空行＋描述段，
                    直接可贴进 ComfyUI 提示词框（就是你最初要的形态，2026-10-18 闭环） */}
                <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <CopyButton text={active.tags.join(", ")} label="复制标签" className="btn" />
                  {(active.tags.length > 0 || active.description) && (
                    <CopyButton
                      text={[active.tags.join(", "), active.description].filter(Boolean).join("\n\n")}
                      label="复制提示词（两段式）"
                      className="btn"
                    />
                  )}
                </div>
              </DetailSection>
              {enhanced && (
                <DetailSection title="偏好比较">
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                  {!preferenceBase || preferenceBase.id === active.id ? (
                    <button className="btn" onClick={() => setPreferenceBase(
                      preferenceBase?.id === active.id ? null : active,
                    )}>
                      {preferenceBase?.id === active.id ? "取消比较基准" : "作为偏好比较基准"}
                    </button>
                  ) : (
                    <>
                      <select value={preferenceReason}
                        onChange={(event) => setPreferenceReason(event.target.value as VisualPreferenceReason)}>
                        <option value="character">人物更像</option>
                        <option value="action">动作更准确</option>
                        <option value="composition">构图更好</option>
                        <option value="lighting">光影更好</option>
                        <option value="color">色彩更符合</option>
                        <option value="quality">画质更好</option>
                        <option value="other">其他</option>
                      </select>
                      <button className="btn" disabled={savingPreference} onClick={preferActive}>
                        {savingPreference ? "记录中…" : "比基准图更喜欢这张"}
                      </button>
                    </>
                  )}
                </div>
                </DetailSection>
              )}
              </div>
            </div>
            {/* 底部操作条：与画布详情 / 上网素材详情统一 */}
            <div className="modal-actions" style={{ flexShrink: 0 }}>
              {enhanced && onSendToChat && (
                <button className="btn" onClick={() => { onSendToChat(active); setActive(null); }}>
                  <Send size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />发送至对话框
                </button>
              )}
              {enhanced && onSendAsRecipe && (
                <button className="btn" onClick={() => { onSendAsRecipe(active); setActive(null); }}>
                  <Send size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />发送至对话
                </button>
              )}
              <button className="btn primary" style={{ marginLeft: "auto" }} onClick={() => setActive(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}
      <ToastLayer toasts={toasts} position="fixed" />
    </div>
  );
}
