// MaterialMarketModal.tsx — 图站素材市场（pixiv / danbooru）P2-1 切片
// 交互参照 D站画廊（Danbooru Gallery）：关键词 + 排行榜/收藏夹 + 全部（rating 四档）+ 分页；
// 瀑布流自然排版（按原图宽高比，不裁切）；每图四个按钮：查看大图 / 编辑信息 / 下载 / 收藏。
//
// 按钮语义（用户口径）——**按钮就是把对应 tag 写进搜索框、并立即搜索**：
//   排行榜 → 输入框追加 `order:rank`（再点移除并重搜）
//   收藏夹 → 输入框追加 `ordfav:<设置里的 danbooru 用户名>`（再点移除并重搜）
//   全部▾ → 输入框里 `rating:g|s|q|e` 的替换（选「全部」= 移除该 token）
//   ⚠ 进入弹窗即自动搜索一次（空 query＝danbooru 默认最新＝时间序）。
//
// 保存走既有 /web-materials/save（后端搜索时已登记受控下载候选，安全闸不破）；
// 收藏走 /web-materials/favorite（danbooru 账号收藏夹）；
// 查看大图走 /web-materials/proxy-image（后端代理抓取，规避 CDN 直连被墙/防盗链）。
// 错误**持久显示在弹窗内**（只弹 toast ⇒ 用户以为没反应，2026-10-16 实锤）。
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Check, Download, Edit3, Heart, Images, Loader2, Search, X,
} from "lucide-react";
import {
  searchWebMaterials, saveWebMaterial, saveWebSeries, favoriteWebMaterial, marketImageProxyUrl,
  pixivFollowing, pixivPages, type WebMaterialSearchItem, type PixivUser,
} from "../api/ai";
import { UI_BUILD as BUILD_TAG } from "../lib/buildInfo";
import { safeExternalHref } from "../lib/externalUrl";

const PAGE_LIMIT = 24;

const RATINGS: { key: "all" | "g" | "s" | "q" | "e"; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "g", label: "普通 (g)" },
  { key: "s", label: "敏感 (s)" },
  { key: "q", label: "可疑 (q)" },
  { key: "e", label: "露骨 (e)" },
];

// pixiv 排行榜周期（mode_param）
const RANKING_PERIODS: { key: string; label: string }[] = [
  { key: "day", label: "日榜" },
  { key: "week", label: "周榜" },
  { key: "month", label: "月榜" },
];

// 详情面板的标签分组（对应 danbooru 的 tag_string_* 分类）
type TagGroup = { key: string; label: string; cls: string; pick: (m: WebMaterialSearchItem) => string[] };

const TAG_GROUPS: TagGroup[] = [
  { key: "artist", label: "画师", cls: "t-artist", pick: (m) => m.tags_artist || [] },
  { key: "copyright", label: "版权", cls: "t-copyright", pick: (m) => m.tags_copyright || [] },
  { key: "character", label: "角色", cls: "t-character", pick: (m) => m.tags_character || [] },
  { key: "general", label: "普通", cls: "t-general", pick: (m) => m.tags_general || m.tags || [] },
  { key: "meta", label: "元数据", cls: "t-meta", pick: (m) => m.tags_meta || [] },
];

export function MaterialMarketModal({
  source, outputDir, proxy, login, onClose, onSaved, onNotify,
}: {
  source: "pixiv" | "danbooru";
  outputDir: string;
  proxy: string;
  login: string;          // danbooru 用户名（收藏夹 ordfav: 用）
  onClose: () => void;
  onSaved: () => void;
  onNotify: (msg: string, kind?: "success" | "error") => void;
}) {
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);          // 视角所在页（随滚动更新）
  const [loadedPages, setLoadedPages] = useState(0);   // 已接续加载到的页数
  const [loadingMore, setLoadingMore] = useState(false);
  const gridRef = useRef<HTMLDivElement | null>(null);
  const loadingMoreRef = useRef(false);
  const [results, setResults] = useState<WebMaterialSearchItem[]>([]);
  const [searched, setSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [jump, setJump] = useState("");
  // 单图操作态：查看大图 / 编辑信息 / 下载 / 收藏
  const [busyKey, setBusyKey] = useState("");
  const [favIds, setFavIds] = useState<Set<number>>(new Set());
  const [detail, setDetail] = useState<WebMaterialSearchItem | null>(null);
  // 页内大图查看（查看大图按钮 → 本页弹窗，不新开标签页）
  const [viewing, setViewing] = useState<WebMaterialSearchItem | null>(null);
  const [viewPage, setViewPage] = useState(0);   // 多页作品当前页（0 起）
  const [viewLoading, setViewLoading] = useState(false);
  const [viewErr, setViewErr] = useState("");
  // 大图视图：滚轮缩放（以鼠标为焦点）+ 拖拽平移 + 双击复位
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 });
  const viewerRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ sx: number; sy: number; vx: number; vy: number } | null>(null);
  const [removedTags, setRemovedTags] = useState<Set<string>>(new Set());  // 详情面板里的本地删除
  const [tagMenu, setTagMenu] = useState<{ tag: string; x: number; y: number } | null>(null);

  // pixiv 信息流模式（默认进入＝推荐；工具栏切换 最新/排行榜/收藏夹/关注作者）
  type PixivMode = "recommended" | "ranking" | "bookmarks" | "author" | "search";
  const [pixivMode, setPixivMode] = useState<PixivMode>("recommended");
  const [pixivModeParam, setPixivModeParam] = useState("");   // ranking 周期 | author user_id
  const [pixivCursor, setPixivCursor] = useState("");         // 收藏夹下一页游标（max_bookmark_id）
  const [showFollowing, setShowFollowing] = useState(false);  // 关注作者列表视图
  const [following, setFollowing] = useState<PixivUser[]>([]);
  const [followingLoading, setFollowingLoading] = useState(false);
  const [followingErr, setFollowingErr] = useState("");
  const [followingPage, setFollowingPage] = useState(1);

  // 缩略图地址：pixiv 走后端代理（i.pximg.net 需 pixiv Referer + 代理）；danbooru 直连 CDN 即可
  // 前端构建标记：用于排查「标签页是否还在跑旧 JS」。改前端时递增；刷新后这里若仍是旧值
  // ⇒ 标签页没真正重载（HMR 断连/缓存），需关掉标签页重开或清缓存刷新。
  const UI_BUILD = BUILD_TAG;

  const thumbSrc = (m: WebMaterialSearchItem) =>
    source === "pixiv" ? marketImageProxyUrl(m.thumb_url || m.full_url, proxy)
                       : (m.thumb_url || m.full_url);

  const label = source === "pixiv" ? "pixiv 市场" : "danbooru 市场";
  const isDanbooru = source === "danbooru";
  const favReady = login.trim().length > 0;

  // 查询真源＝输入框内容（按钮只是「把 tag 写进输入框」，用户可见可改）
  // 分词同时按逗号与空白（token 用「, 」连接；danbooru 两种分隔都接受）
  const toTokens = (s: string) => s.split(/[,\s]+/).filter(Boolean);
  const hasToken = (t: string) => toTokens(keyword).includes(t);
  const ratingToken = keyword.match(/(?:^|[,\s])rating:([gseq])(?:[,\s]|$)/)?.[1] ?? "all";

  /** 增删关键字后返回新 query（不改 state，交由调用方同步 setKeyword + 立刻搜）。
      多 token 用「, 」连接（用户口径：`ordfav:X, order:rank`）。 */
  const withToken = (add: string | null, strip?: (t: string) => boolean) => {
    let arr = toTokens(keyword);
    if (strip) arr = arr.filter((t) => !strip(t));
    if (add && !arr.includes(add)) arr.push(add);
    return arr.join(", ");
  };

  /** 收藏夹浏览（ordfav:）时结果本身就是「我的收藏」⇒ 直接标实心。 */
  const markFavFromQuery = (items: WebMaterialSearchItem[], q: string) => {
    if (!isDanbooru || !/(?:^|[,\s])ordfav:/.test(q)) return;
    const ids = items.map((i) => i.post_id).filter((x): x is number => typeof x === "number");
    if (!ids.length) return;
    setFavIds((s) => {
      const n = new Set(s);
      ids.forEach((i) => n.add(i));
      return n;
    });
  };

  /** pixiv 收藏夹流：结果本身就是「我的收藏」⇒ 直接标实心。
      ⚠ 必须传「本次搜索实际用的 mode」而非闭包里的 pixivMode——按钮点击后 setState 异步，
      runSearch 闭包捕获的 pixivMode 还是旧值（如从收藏夹切到排行榜时仍是 "bookmarks"），
      会把排行榜/推荐结果误标成已收藏（用户实锤：没收藏却实心）。 */
  const markFavPixivBookmarks = (items: WebMaterialSearchItem[], mode: PixivMode) => {
    if (source !== "pixiv" || mode !== "bookmarks") return;
    const ids = items.map((i) => i.post_id).filter((x): x is number => typeof x === "number");
    if (!ids.length) return;
    setFavIds((s) => {
      const n = new Set(s);
      ids.forEach((i) => n.add(i));
      return n;
    });
  };

  const runSearch = async (
    targetPage = 1, qOverride?: string,
    modeOverride?: PixivMode, modeParamOverride?: string,
  ) => {
    const q = (qOverride ?? keyword).trim();
    // pixiv 模式解析：有关键词 ⇒ 关键词搜索；空 query + search ⇒ 回退 recommended（无公开「最新」流）
    let mode: PixivMode = modeOverride ?? pixivMode;
    let modeParam = modeParamOverride ?? pixivModeParam;
    if (source === "pixiv") {
      if (q) { mode = "search"; modeParam = ""; }
      else if (mode === "search") { mode = "recommended"; modeParam = ""; }
    }
    // 空 query 合法：danbooru 默认按最新返回 = 时间序浏览
    setLoading(true);
    setError("");
    setSelected(new Set());
    setPage(targetPage);
    try {
      const r = await searchWebMaterials(source, q, PAGE_LIMIT, proxy, targetPage, mode, modeParam);
      setResults(r.items || []);
      // 收藏夹下一页游标（其他模式 next_url 为空 ⇒ 置空）
      setPixivCursor(mode === "bookmarks" ? (r.next_cursor || "") : "");
      if (r.ok) {
        setLoadedPages(targetPage);
        markFavFromQuery(r.items || [], q);
        markFavPixivBookmarks(r.items || [], mode);
      } else {
        setLoadedPages(0);
        setError(r.error || "搜索失败");
      }
    } catch (e) {
      setResults([]);
      setLoadedPages(0);
      setError(`请求失败：${(e as Error).message}`);
    } finally {
      setSearched(true);
      setLoading(false);
      // 重置换后回到顶部
      if (gridRef.current) gridRef.current.scrollTop = 0;
    }
  };

  /** 接续加载下一页（无限滚动：追加而非翻页替换）。 */
  const loadMore = async () => {
    if (loading || loadingMoreRef.current || !searched) return;
    const next = loadedPages + 1;
    if (!hasMore || next > 1000) return;
    const q = keyword.trim();
    const m: PixivMode = q || source !== "pixiv" ? "search" : pixivMode;
    // pixiv 收藏夹：用 max_bookmark_id 游标接续（无 offset；无游标＝没有更多）
    if (source === "pixiv" && m === "bookmarks") {
      if (!pixivCursor) return;
      loadingMoreRef.current = true;
      setLoadingMore(true);
      try {
        const r = await searchWebMaterials(source, "", PAGE_LIMIT, proxy, 1, "bookmarks", pixivCursor);
        if (r.ok && (r.items || []).length > 0) {
          setResults((prev) => [...prev, ...(r.items || [])]);
          setPixivCursor(r.next_cursor || "");
          markFavPixivBookmarks(r.items || [], "bookmarks");
        } else if (!r.ok) {
          setError(r.error || "加载下一页失败");
        } else {
          setPixivCursor("");
        }
      } catch (e) {
        setError(`加载下一页失败：${(e as Error).message}`);
      } finally {
        loadingMoreRef.current = false;
        setLoadingMore(false);
      }
      return;
    }
    loadingMoreRef.current = true;
    setLoadingMore(true);
    try {
      const mp = m === "search" ? "" : pixivModeParam;
      const r = await searchWebMaterials(source, q, PAGE_LIMIT, proxy, next, m, mp);
      if (r.ok && (r.items || []).length > 0) {
        setResults((prev) => [...prev, ...(r.items || [])]);
        setLoadedPages(next);
        markFavFromQuery(r.items || [], q);
      } else if (!r.ok) {
        setError(r.error || "加载下一页失败");   // 持久显示，不静默
      }
    } catch (e) {
      setError(`加载下一页失败：${(e as Error).message}`);
    } finally {
      loadingMoreRef.current = false;
      setLoadingMore(false);
    }
  };

  /** 滚动：接近底部自动接续 + 「当前页」跟随视角所在区间的页数。 */
  const onGridScroll = () => {
    const el = gridRef.current;
    if (!el) return;
    const { scrollTop, clientHeight, scrollHeight } = el;
    if (scrollTop + clientHeight >= scrollHeight - 420) void loadMore();
    const ratio = scrollHeight > clientHeight
      ? (scrollTop + clientHeight) / scrollHeight
      : 1;
    const est = Math.max(1, Math.min(Math.max(loadedPages, 1), Math.ceil(ratio * Math.max(loadedPages, 1))));
    setPage((p) => (p === est ? p : est));
  };

  // 进入弹窗即自动加载：danbooru 不带关键词＝最新；pixiv＝推荐流（无公开「最新」流）
  useEffect(() => {
    if (source === "pixiv") {
      setPixivMode("recommended");
      setPixivModeParam("");
      void runSearch(1, "", "recommended", "");
    } else {
      void runSearch(1, "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 已收藏态初始化：danbooru 带鉴权请求会回 is_favorited（♥ 实心）
  useEffect(() => {
    const ids = results
      .filter((r) => r.is_favorited && r.post_id)
      .map((r) => r.post_id as number);
    if (ids.length === 0) return;
    setFavIds((s) => {
      const n = new Set(s);
      ids.forEach((i) => n.add(i));
      return n;
    });
  }, [results]);

  // 点遮罩不再关闭（复制粘贴友好）⇒ 用 ESC 作为快捷关闭（从最上层往下逐层关）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (viewing) { setViewing(null); return; }
      if (tagMenu) { setTagMenu(null); return; }
      if (detail) { setDetail(null); return; }
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const toggle = (url: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(url)) n.delete(url); else n.add(url);
      return n;
    });

  /** 保存单张，带重试（非 pixiv / danbooru 用）。 */
  const saveWithRetry = async (src: string, sourceUrl: string, title: string, retries = 2): Promise<void> => {
    let lastErr: unknown;
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        await saveWebMaterial(outputDir, src, sourceUrl, title);
        return;
      } catch (e) {
        lastErr = e;
        if (attempt < retries) await new Promise((r) => setTimeout(r, 700));
      }
    }
    throw lastErr;
  };

  /** 把一个条目存入素材库，返回 {ok, fail}（成功/失败张数）。
      ⚠ pixiv ⇒ 走后端 `save-series`：一次请求、服务端把全页原图逐页写完，**前端崩溃/断连也不影响**
      （2026-10-17 实锤：客户端 for-循环下到一半浏览器崩了，5 页只落 1 页）。其他源 ⇒ 单张带重试保存。 */
  const saveItemToLibrary = async (item: WebMaterialSearchItem): Promise<{ ok: number; fail: number }> => {
    if (source === "pixiv" && item.post_id) {
      const r = await saveWebSeries(outputDir, item.post_id as number, item.source_url, item.title, proxy);
      return { ok: r.saved, fail: r.failed };
    }
    try {
      await saveWithRetry(item.full_url, item.source_url, item.title);
      return { ok: 1, fail: 0 };
    } catch {
      return { ok: 0, fail: 1 };
    }
  };

  const saveSelected = async () => {
    if (selected.size === 0 || saving) return;
    setSaving(true);
    let ok = 0;
    let fail = 0;
    const picked = results.filter((m) => selected.has(m.full_url));
    for (const item of picked) {
      try {
        const r = await saveItemToLibrary(item);
        ok += r.ok;
        fail += r.fail;
      } catch { fail += 1; }
    }
    setSaving(false);
    setSelected(new Set());
    onSaved();
    if (fail === 0) onNotify(`已保存 ${ok} 张素材到「上网素材」。`, "success");
    else onNotify(`保存完成：成功 ${ok} 张，失败 ${fail} 张。`, fail === ok ? "error" : "success");
  };

  // 单图下载（↓）：与批量保存同一条链（pixiv 走后端整册保存，浏览器崩溃也不影响完成）。
  const downloadOne = async (m: WebMaterialSearchItem) => {
    setBusyKey(m.full_url);
    try {
      if (source === "pixiv" && (m.page_count || 0) > 1) {
        onNotify(`正在保存「${m.title}」全部 ${m.page_count} 页原图，请稍候（大图经代理约 1–2 分钟，浏览器崩了也不影响，服务端会写完）…`, "success");
      }
      const { ok, fail } = await saveItemToLibrary(m);
      onSaved();
      if (fail === 0) onNotify(`已保存「${m.title}」${ok} 张到「上网素材」。`, "success");
      else onNotify(`保存完成：成功 ${ok} 张，失败 ${fail} 张。`, "error");
    } catch (e) {
      onNotify(`保存失败：${(e as Error).message}`, "error");
    } finally {
      setBusyKey("");
    }
  };

  // 收藏（♥）：pixiv 已收藏的再点＝取消收藏（toggle）；danbooru 仅支持加入。
  const favoriteOne = async (m: WebMaterialSearchItem) => {
    if (!m.post_id) {
      onNotify(source === "pixiv" ? "该结果没有 pixiv illust id，无法收藏。"
                                  : "该结果没有 danbooru post_id，无法收藏。", "error");
      return;
    }
    const wasFav = favIds.has(m.post_id);
    const canToggle = source === "pixiv" && wasFav;   // pixiv 支持取消收藏
    setBusyKey(m.full_url);
    try {
      const r = await favoriteWebMaterial(m.post_id, proxy, source, canToggle);
      if (!r.ok) {
        onNotify(r.error || "收藏失败", "error");
      } else if (canToggle) {
        setFavIds((s) => { const n = new Set(s); n.delete(m.post_id as number); return n; });
        onNotify("已从 pixiv 收藏夹移除。", "success");
      } else if (r.already) {
        setFavIds((s) => new Set(s).add(m.post_id as number));
        onNotify(source === "pixiv" ? "这张图已在你的 pixiv 收藏夹里。"
                                    : "这张图已在你的 danbooru 收藏夹里。", "success");
      } else {
        setFavIds((s) => new Set(s).add(m.post_id as number));
        onNotify(source === "pixiv" ? "已收藏到 pixiv 账号收藏夹。"
                                    : "已收藏到 danbooru 账号收藏夹。", "success");
      }
    } catch (e) {
      onNotify(`收藏失败：${(e as Error).message}`, "error");
    } finally {
      setBusyKey("");
    }
  };

  // 查看大图（🔍）：**页内弹窗**展示（2026-10-16 用户口径：不再新开标签页）。
  // 用 1920px 的 large 版（原图常 20MB+ 需数分钟）；弹窗内另给「原图新标签」兜底。
  // 多页作品：feed 可能只给 page_count、pages 不全 ⇒ 打开时按需补拉全页（illust_detail）。
  const openFull = async (m: WebMaterialSearchItem, page = 0) => {
    setViewErr("");
    setViewLoading(true);
    setView({ scale: 1, x: 0, y: 0 });   // 每次打开/翻页复位视图
    setViewPage(page);
    setViewing(m);
    const pc = m.page_count || 0;
    if (pc > 1 && (m.pages || []).length < pc) {
      try {
        const r = await pixivPages(m.post_id as number, proxy);
        if (r.ok && r.pages.length > (m.pages || []).length) {
          setViewing((v) => (v && v.full_url === m.full_url)
            ? { ...v, pages: r.pages, page_count: r.page_count } : v);
        }
      } catch { /* 补拉失败就用 feed 的 pages（至少封面） */ }
    }
  };

  // 多页作品翻页（pixiv meta_pages）：切页重置缩放/平移、重新加载。
  // ⚠ 用 page_count 判定多页（feed 对大作品只给 page_count、pages 可能只有封面 1 条），
  // 打开时 openFull 会补拉全页填满 viewing.pages。
  const viewPages = viewing && ((viewing.page_count || 0) > 1 || (viewing.pages || []).length > 1)
    ? (viewing.pages || []) : null;
  const viewImgUrl = viewing
    ? (viewPages && viewPages.length > 0 ? viewPages[viewPage] : (viewing.large_url || viewing.full_url))
    : "";
  const gotoViewPage = (delta: number) => {
    if (!viewing || !viewPages) return;
    const n = Math.min(viewPages.length - 1, Math.max(0, viewPage + delta));
    if (n === viewPage) return;
    openFull(viewing, n);
  };

  // 加载 pixiv 关注作者列表（首次打开清空；后续按 followingPage 接续翻页）
  const loadFollowing = async (reset = true) => {
    setFollowingLoading(true);
    setFollowingErr("");
    const page = reset ? 1 : followingPage + 1;
    try {
      const r = await pixivFollowing(proxy, page);
      if (r.ok && (r.users || []).length > 0) {
        setFollowing((prev) => (reset ? (r.users || []) : [...prev, ...(r.users || [])]));
        setFollowingPage(page);
      } else if (!r.ok) {
        setFollowingErr(r.error || "加载关注列表失败");
      } else if (reset) {
        setFollowing([]);
        setFollowingErr("你还没有关注任何作者。");
      } else {
        setFollowingErr("已加载全部关注作者。");
      }
    } catch (e) {
      setFollowingErr(`加载关注列表失败：${(e as Error).message}`);
    } finally {
      setFollowingLoading(false);
    }
  };

  // 点关注列表里的作者 ⇒ 切到「该作者作品」流
  const openAuthorWorks = (u: PixivUser) => {
    setShowFollowing(false);
    setKeyword("");
    setPixivMode("author");
    setPixivModeParam(String(u.id));
    void runSearch(1, "", "author", String(u.id));
  };

  // 滚轮缩放（以鼠标位置为焦点，缩放后基准点不跑）——wheel 必须 passive:false 才能 preventDefault
  useEffect(() => {
    const el = viewerRef.current;
    if (!el || !viewing) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const cx = e.clientX - (rect.left + rect.width / 2);
      const cy = e.clientY - (rect.top + rect.height / 2);
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      setView((v) => {
        const next = Math.min(8, Math.max(0.3, v.scale * factor));
        const k = next / v.scale;
        return { scale: next, x: cx - (cx - v.x) * k, y: cy - (cy - v.y) * k };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [viewing]);

  // 是否还有下一页：pixiv 收藏夹看游标（next_url 的 max_bookmark_id），其余看本页是否填满
  const hasMore = source === "pixiv" && pixivMode === "bookmarks"
    ? !!pixivCursor
    : results.length >= PAGE_LIMIT;

  // 详情面板：可见标签（过滤本地删除的）
  const detailTags = (m: WebMaterialSearchItem) =>
    TAG_GROUPS.map((g) => ({ ...g, tags: g.pick(m).filter((t) => !removedTags.has(t)) }))
      .filter((g) => g.tags.length > 0);

  const copyDetailTags = async (m: WebMaterialSearchItem) => {
    const all = detailTags(m).flatMap((g) => g.tags).join(", ");
    try {
      await navigator.clipboard.writeText(all);
      onNotify(`已复制 ${detailTags(m).flatMap((g) => g.tags).length} 个标签到剪贴板。`, "success");
    } catch {
      onNotify("复制失败（浏览器未授权剪贴板）。", "error");
    }
  };

  return (
    <div className="modal-mask">
      <div
        className="modal"
        style={{
          maxWidth: 1060, width: "95vw",
          // 高度自适应：flex 列 + 90vh 上限，中间网格区自己滚 ⇒ 底部按钮永不被切
          maxHeight: "90vh", display: "flex", flexDirection: "column",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 10, flexShrink: 0 }}>
          <h3 style={{ marginTop: 0, marginBottom: 0, flex: 1, minWidth: 0 }}>
            {label}
            <span style={{ fontSize: 12, fontWeight: 400, color: "var(--text-muted)", marginLeft: 10 }}>
              {isDanbooru
                ? "danbooru 公开 API（标签用英文下划线；不填关键词＝按最新浏览）"
                : "pixiv（需在「设置 → 凭证」填 Refresh Token；默认进入＝推荐流，可切 最新/排行榜/收藏夹/关注作者）"}
            </span>
          </h3>
          <button className="btn" title="关闭（ESC 亦可）" onClick={onClose} style={{ flexShrink: 0, padding: "6px 9px" }}>
            <X size={16} />
          </button>
        </div>

        {/* 工具栏：搜索 + 排行榜/收藏夹 + 全部（rating 四档）——按钮即「写进输入框并搜索」 */}
        <div style={{ display: "flex", gap: 8, marginBottom: 8, flexWrap: "wrap", flexShrink: 0 }}>
          <input
            style={{ flex: 1, minWidth: 200 }}
            placeholder="按标签搜索（如 blue_sky 1girl）；留空 = 按最新浏览…"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void runSearch(1); }}
          />
          <button className="btn primary" disabled={loading} onClick={() => void runSearch(1)}>
            {loading
              ? <Loader2 size={14} className="spin" style={{ verticalAlign: "-2px" }} />
              : <Search size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />}
            搜索
          </button>
          {isDanbooru ? (
            <>
              <button
                className={`btn ${hasToken("order:rank") ? "primary" : ""}`}
                title="排行榜：把 order:rank 写进搜索框并搜索（再点移除）"
                onClick={() => {
                  const on = hasToken("order:rank");
                  const next = on ? withToken(null, (t) => t === "order:rank") : withToken("order:rank");
                  setKeyword(next);
                  void runSearch(1, next);
                }}
              >排行榜</button>
              <button
                className={`btn ${favReady && hasToken(`ordfav:${login.trim()}`) ? "primary" : ""}`}
                title={favReady
                  ? `收藏夹：把 ordfav:${login.trim()} 写进搜索框并搜索（再点移除）`
                  : "收藏夹需要先在「设置 → 凭证」填 danbooru 用户名"}
                onClick={() => {
                  if (!favReady) {
                    onNotify("收藏夹需要 danbooru 用户名：请到「设置 → 凭证 → 上网素材源鉴权」填写。", "error");
                    return;
                  }
                  const tag = `ordfav:${login.trim()}`;
                  const on = hasToken(tag);
                  const next = on ? withToken(null, (t) => t === tag) : withToken(tag);
                  setKeyword(next);
                  void runSearch(1, next);
                }}
              >收藏夹</button>
              <select
                value={ratingToken}
                title="评级筛选：把 rating:x 写进搜索框并搜索"
                onChange={(e) => {
                  const v = e.target.value;
                  const next = withToken(v === "all" ? null : `rating:${v}`,
                                         (t) => t.startsWith("rating:"));
                  setKeyword(next);
                  void runSearch(1, next);
                }}
              >
                {RATINGS.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
              </select>
            </>
          ) : (
            <>
              <button
                className={`btn ${pixivMode === "recommended" && !showFollowing ? "primary" : ""}`}
                title="最新推荐（默认进入页）"
                onClick={() => { setKeyword(""); setPixivMode("recommended"); setPixivModeParam(""); setShowFollowing(false); void runSearch(1, "", "recommended", ""); }}
              >最新</button>
              <button
                className={`btn ${pixivMode === "ranking" ? "primary" : ""}`}
                title="排行榜：按 日/周/月 榜浏览"
                onClick={() => {
                  const mp = pixivModeParam || "day";
                  setKeyword(""); setPixivMode("ranking"); setPixivModeParam(mp); setShowFollowing(false);
                  void runSearch(1, "", "ranking", mp);
                }}
              >排行榜</button>
              <select
                value={pixivMode === "ranking" ? pixivModeParam : ""}
                title="排行榜周期：日 / 周 / 月"
                onChange={(e) => {
                  const v = e.target.value || "day";
                  setKeyword(""); setPixivMode("ranking"); setPixivModeParam(v); setShowFollowing(false);
                  void runSearch(1, "", "ranking", v);
                }}
              >
                <option value="">排行榜周期</option>
                {RANKING_PERIODS.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
              </select>
              <button
                className={`btn ${pixivMode === "bookmarks" ? "primary" : ""}`}
                title="我的 pixiv 收藏夹"
                onClick={() => { setKeyword(""); setPixivMode("bookmarks"); setPixivModeParam(""); setShowFollowing(false); void runSearch(1, "", "bookmarks", ""); }}
              >收藏夹</button>
              <button
                className={`btn ${showFollowing ? "primary" : ""}`}
                title="关注的作者列表（点作者看其作品）"
                onClick={() => { setShowFollowing(true); setResults([]); setSearched(false); void loadFollowing(true); }}
              >关注作者</button>
            </>
          )}
        </div>

        {/* 持久错误条 */}
        {error && (
          <div style={{
            background: "rgba(192,57,43,0.14)", border: "1px solid rgba(192,57,43,0.5)",
            color: "var(--text)", borderRadius: 8, padding: "8px 12px", fontSize: 13, marginBottom: 8,
          }}>
            {error}
          </div>
        )}

        <div
          ref={gridRef}
          onScroll={onGridScroll}
          style={{ flex: 1, minHeight: 0, overflow: "auto", marginBottom: 10 }}
        >
          {showFollowing ? (
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                <button className="btn" onClick={() => {
                  setShowFollowing(false);
                  setPixivMode("recommended"); setPixivModeParam("");
                  void runSearch(1, "", "recommended", "");
                }}>← 返回作品</button>
                <span style={{ fontWeight: 600, fontSize: 13 }}>关注作者（{following.length}）</span>
                {followingLoading && <Loader2 size={13} className="spin" />}
              </div>
              {followingErr && (
                <div style={{
                  background: "rgba(192,57,43,0.14)", border: "1px solid rgba(192,57,43,0.5)",
                  color: "var(--text)", borderRadius: 8, padding: "8px 12px", fontSize: 13, marginBottom: 8,
                }}>{followingErr}</div>
              )}
              <div className="market-masonry">
                {following.map((u) => (
                  <div
                    key={u.id}
                    className="market-card"
                    style={{ cursor: "pointer" }}
                    onClick={() => openAuthorWorks(u)}
                    title={`查看 ${u.name || u.account} 的作品`}
                  >
                    {u.avatar_url ? (
                      <img
                        src={marketImageProxyUrl(u.avatar_url, proxy)}
                        alt={u.name}
                        loading="lazy"
                        style={{ width: "100%", height: 150, objectFit: "cover", background: "#222" }}
                      />
                    ) : (
                      <div style={{ width: "100%", height: 150, background: "#222" }} />
                    )}
                    <div className="market-card-meta">
                      <span>{u.name || u.account}</span>
                      {u.account && u.account !== (u.name || "") && (
                        <span style={{ opacity: 0.65 }}> @{u.account}</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
              {following.length > 0 && (
                <div style={{ textAlign: "center", marginTop: 10 }}>
                  <button className="btn" disabled={followingLoading}
                    onClick={() => void loadFollowing(false)}>
                    {followingLoading ? "加载中…" : "加载更多关注"}
                  </button>
                </div>
              )}
            </div>
          ) : loading ? (
            <div className="market-masonry">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="market-card skeleton" style={{ height: 200 + (i % 3) * 60 }} />
              ))}
            </div>
          ) : !searched ? (
            <p style={{ color: "var(--text-muted)", fontSize: 13 }}>
              直接点「搜索」＝按最新浏览；也可输入标签、或点「排行榜 / 收藏夹」按榜单浏览。
              每张图有 查看大图 / 编辑信息 / 下载 / 收藏 四个按钮；勾选后可批量保存到「上网素材」。
            </p>
          ) : results.length === 0 ? (
            <div className="empty-state">
              <Images size={30} strokeWidth={1.4} style={{ opacity: 0.5 }} />
              <p style={{ margin: 0 }}>没有结果，换个标签或筛选条件试试。</p>
            </div>
          ) : (
            <div className="market-masonry">
              {results.map((m) => (
                <div
                  key={m.full_url}
                  className={`market-card ${selected.has(m.full_url) ? "sel" : ""}`}
                  style={source === "pixiv" ? { minHeight: 200 } : undefined}
                >
                  <img
                    src={thumbSrc(m)}
                    alt={m.title}
                    loading="lazy"
                    onClick={() => toggle(m.full_url)}
                    title={`${m.title}${m.author ? ` · ${m.author}` : ""}${m.tags.length ? `\n${m.tags.join(", ")}` : ""}\n（点图＝勾选）`}
                  />
                  <span
                    className={`wm-check ${selected.has(m.full_url) ? "on" : ""}`}
                    onClick={() => toggle(m.full_url)}
                  >
                    {selected.has(m.full_url) && <Check size={14} />}
                  </span>
                  <div className="market-card-actions">
                    <button className="market-act" title="查看大图（新标签页）" onClick={() => void openFull(m)}>
                      <Search size={13} />
                    </button>
                    <button className="market-act" title="编辑信息（查看详细参数与标签）"
                      onClick={() => { setRemovedTags(new Set()); setDetail(m); }}>
                      <Edit3 size={13} />
                    </button>
                    <button className="market-act" title="下载（保存到「上网素材」）"
                      disabled={busyKey === m.full_url} onClick={() => void downloadOne(m)}>
                      {busyKey === m.full_url ? <Loader2 size={13} className="spin" /> : <Download size={13} />}
                    </button>
                    <button
                      className={`market-act ${m.post_id && favIds.has(m.post_id) ? "on" : ""}`}
                      title={m.post_id && favIds.has(m.post_id)
                        ? (isDanbooru ? "已加入你的 danbooru 收藏夹" : "已加入你的 pixiv 收藏夹")
                        : (isDanbooru ? "收藏到 danbooru 账号收藏夹" : "收藏到 pixiv 账号收藏夹")}
                      disabled={!m.post_id || busyKey === m.full_url}
                      onClick={() => void favoriteOne(m)}
                    >
                      <Heart size={13} fill={m.post_id && favIds.has(m.post_id) ? "currentColor" : "none"} />
                    </button>
                  </div>
                  <div className="market-card-meta">
                    {m.author ? `${m.author} · ` : ""}{(m.tags[0] || m.title || "").slice(0, 26)}
                    {m.page_count && m.page_count > 1 ? ` · ${m.page_count}页` : ""}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 无限滚动：滚到底自动接续下一页；「当前页」＝视角所在区间的页数（不翻页替换） */}
        {isDanbooru && (
          <div style={{ flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", gap: 8, marginBottom: 10 }}>
            <span className="page-info">当前页：{page}</span>
            <span className="page-info" style={{ opacity: 0.65 }}>/ 已加载 {loadedPages} 页</span>
            {loadingMore && (
              <span style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, color: "var(--text-muted)" }}>
                <Loader2 size={13} className="spin" />接续加载…
              </span>
            )}
            <input
              style={{ width: 56, textAlign: "center" }}
              placeholder="页码"
              value={jump}
              onChange={(e) => setJump(e.target.value.replace(/\D/g, ""))}
              onKeyDown={(e) => {
                if (e.key !== "Enter" || !jump) return;
                void runSearch(Math.max(1, Math.min(1000, parseInt(jump, 10) || 1)));
              }}
            />
            <span className="page-info" title="跳转会清空当前列表、从该页重新开始接续">跳转（重载）</span>
          </div>
        )}

        <div className="modal-actions" style={{ flexShrink: 0 }}>
          <span style={{ marginRight: "auto", fontSize: 12, color: "var(--text-muted)" }}>
            {results.length > 0 ? `本页 ${results.length} 条 · 已选 ${selected.size}` : ""}
            {proxy
              ? <span style={{ marginLeft: 8 }}>· 代理 {proxy}</span>
              : <span style={{ marginLeft: 8 }}>· 直连（若搜索失败请在「设置 → 常规」配代理）</span>}
            <span style={{ marginLeft: 8, opacity: 0.7 }} title="前端构建标记：刷新后仍是旧值说明标签页没真正重载">· UI {UI_BUILD}</span>
          </span>
          <button className="btn" onClick={onClose}>关闭</button>
          <button className="btn primary" disabled={selected.size === 0 || saving} onClick={() => void saveSelected()}>
            {saving ? "保存中…" : `保存到上网素材（${selected.size}）`}
          </button>
        </div>

        {/* ===== 查看大图（🔍）：**页内弹窗**（2026-10-16 用户口径：不新开标签页） =====
            深色背景 + contain 适配；工具条给「原图新标签 / 下载 / 收藏」兜底 */}
        {viewing && createPortal(
          <div className="modal-mask" style={{ zIndex: 1700, background: "rgba(0,0,0,0.86)" }}>
            <div style={{
              maxWidth: "96vw", maxHeight: "94vh", display: "flex", flexDirection: "column",
              alignItems: "center", gap: 10,
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", flexWrap: "wrap" }}>
                <span style={{ color: "#fff", fontSize: 13 }}>
                  {viewing.title}
                  {viewing.author ? ` · ${viewing.author}` : ""}
                  {viewPages
                    ? ` · 第 ${viewPage + 1}/${viewPages.length} 页`
                    : (viewing.width && viewing.height ? ` · ${viewing.width}×${viewing.height}` : "")}
                </span>
                {viewPages && (
                  <>
                    <button className="btn" disabled={viewPage <= 0} onClick={() => gotoViewPage(-1)} title="上一页">‹ 上一页</button>
                    <button className="btn" disabled={viewPage >= viewPages.length - 1} onClick={() => gotoViewPage(1)} title="下一页">› 下一页</button>
                  </>
                )}
                <span style={{ marginLeft: "auto" }} />
                <button className="btn" title="用原始分辨率在新标签页打开"
                  onClick={() => window.open(marketImageProxyUrl(viewImgUrl, proxy), "_blank", "noopener")}>
                  原图新标签
                </button>
                <button className="btn" disabled={busyKey === viewing.full_url}
                  onClick={() => void downloadOne(viewing)}>
                  {busyKey === viewing.full_url
                    ? "保存中…"
                    : (viewPages ? `下载全部 ${viewPages.length} 页` : "下载")}
                </button>
                {(source === "pixiv" ? !!viewing.post_id : isDanbooru) && (
                  <button className="btn" disabled={!viewing.post_id || busyKey === viewing.full_url}
                    onClick={() => void favoriteOne(viewing)}>
                    <Heart size={13}
                      fill={viewing.post_id && favIds.has(viewing.post_id) ? "currentColor" : "none"}
                      style={{ verticalAlign: "-2px", marginRight: 4 }} />
                    {viewing.post_id && favIds.has(viewing.post_id) ? "已收藏" : "收藏"}
                  </button>
                )}
                <button className="btn primary" onClick={() => setViewing(null)}>关闭</button>
              </div>
              <div
                ref={viewerRef}
                style={{
                  flex: 1, minHeight: 0, display: "flex", alignItems: "center",
                  justifyContent: "center", position: "relative", overflow: "hidden",
                  cursor: view.scale > 1 ? "grab" : "default", userSelect: "none",
                }}
                onMouseDown={(e) => {
                  if (view.scale <= 1) return;
                  dragRef.current = { sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y };
                }}
                onMouseMove={(e) => {
                  const d = dragRef.current;
                  if (!d) return;
                  setView((v) => ({ ...v, x: d.vx + (e.clientX - d.sx), y: d.vy + (e.clientY - d.sy) }));
                }}
                onMouseUp={() => { dragRef.current = null; }}
                onMouseLeave={() => { dragRef.current = null; }}
                onDoubleClick={() => setView({ scale: 1, x: 0, y: 0 })}
                title="滚轮缩放 · 放大后拖拽平移 · 双击复位"
              >
                {viewErr ? (
                  <div style={{ color: "#eee", fontSize: 13, textAlign: "center", lineHeight: 1.9 }}>
                    <div>{viewErr}</div>
                    <button className="btn" style={{ marginTop: 8 }}
                      onClick={() => window.open(marketImageProxyUrl(viewing.full_url, proxy), "_blank", "noopener")}>
                      改用原图新标签打开
                    </button>
                  </div>
                ) : (
                  <>
                    {viewLoading && (
                      <div style={{ position: "absolute", color: "#eee", fontSize: 13, display: "flex", alignItems: "center", gap: 6 }}>
                        <Loader2 size={16} className="spin" />加载大图（1920px）…
                      </div>
                    )}
                    <img
                      src={marketImageProxyUrl(viewImgUrl, proxy)}
                      alt={viewing.title}
                      onLoad={() => setViewLoading(false)}
                      onError={() => { setViewLoading(false); setViewErr("大图加载失败（代理抓取失败或链接失效）。"); }}
                      draggable={false}
                      style={{
                        maxWidth: "96vw", maxHeight: "84vh", objectFit: "contain",
                        borderRadius: 6, opacity: viewLoading ? 0.2 : 1, transition: "opacity .15s",
                        transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`,
                        willChange: "transform",
                      }}
                    />
                    <div style={{
                      position: "absolute", bottom: 4, left: "50%", transform: "translateX(-50%)",
                      color: "rgba(255,255,255,0.72)", fontSize: 11.5, pointerEvents: "none",
                    }}>
                      滚轮缩放 · 拖拽平移 · 双击复位（{Math.round(view.scale * 100)}%）
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>,
          document.body,
        )}

        {/* ===== 编辑信息面板（✏️）：详细参数 + 分组标签（点标签→搜索/删除） =====
            ⚠ 用 portal 挂到 body 并抬 z-index——否则嵌在市场弹窗的层叠上下文里会被压在下层
            （用户实测：点「编辑信息」看不到面板 = 被市场弹窗盖住） */}
        {detail && createPortal(
          <div className="modal-mask" style={{ zIndex: 1500 }}>
            <div className="modal" style={{ maxWidth: 720, width: "92vw" }} onClick={(e) => e.stopPropagation()}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: 10 }}>
                <h3 style={{ margin: 0 }}>图片信息与标签</h3>
                <button className="btn" style={{ marginLeft: "auto" }} onClick={() => { setDetail(null); setTagMenu(null); }}>
                  <X size={14} />
                </button>
              </div>
              <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 10, lineHeight: 1.9 }}>
                <div>上传时间：{detail.created_at ? detail.created_at.replace("T", " ").slice(0, 19) : "—"}</div>
                <div>分辨率：{detail.width && detail.height ? `${detail.width}×${detail.height}` : "—"}
                  {detail.file_size ? ` · ${(detail.file_size / 1048576).toFixed(1)}MB` : ""}</div>
                <div>评分：{detail.score ?? "—"} · 收藏数：{detail.fav_count ?? "—"} · 评级：{detail.rating || "—"}</div>
                <div>
                  来源：<a href={safeExternalHref(detail.source_url)} target="_blank" rel="noreferrer">{detail.source_url}</a>
                </div>
              </div>
              <div style={{ maxHeight: "46vh", overflow: "auto", marginBottom: 12 }}>
                {detailTags(detail).map((g) => (
                  <div key={g.key} style={{ marginBottom: 10 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{g.label}（{g.tags.length}）</div>
                    <div>
                      {g.tags.map((t) => (
                        <span
                          key={t}
                          className={`market-tag ${g.cls}`}
                          title="点击：搜索 / 删除"
                          onClick={(e) => setTagMenu({ tag: t, x: e.clientX, y: e.clientY })}
                        >{t}</span>
                      ))}
                    </div>
                  </div>
                ))}
                {detailTags(detail).length === 0 && (
                  <p style={{ color: "var(--text-muted)", fontSize: 13 }}>（无标签，或已全部删除）</p>
                )}
              </div>
              <div className="modal-actions">
                {(source === "pixiv" ? !!detail.post_id : isDanbooru) && (
                  <button className="btn" disabled={!detail.post_id || busyKey === detail.full_url}
                    onClick={() => void favoriteOne(detail)}>
                    <Heart size={13}
                      fill={detail.post_id && favIds.has(detail.post_id) ? "currentColor" : "none"}
                      style={{ verticalAlign: "-2px", marginRight: 4 }} />
                    {detail.post_id && favIds.has(detail.post_id) ? "已收藏" : (isDanbooru ? "收藏到 danbooru" : "收藏到 pixiv")}
                  </button>
                )}
                <button className="btn" onClick={() => void copyDetailTags(detail)}>复制标签</button>
                <button className="btn" onClick={() => { setRemovedTags(new Set()); onNotify("已重置标签（本地）。", "success"); }}>
                  重置标签
                </button>
                <button className="btn primary" onClick={() => { setDetail(null); setTagMenu(null); }}>完成</button>
              </div>
            </div>
          </div>,
          document.body,
        )}

        {/* 标签点选菜单：搜索（填入搜索栏）/ 删除（本地面板移除）——同样 portal 置顶 */}
        {tagMenu && createPortal(
          <>
            <div style={{ position: "fixed", inset: 0, zIndex: 1590 }} onClick={() => setTagMenu(null)} />
            <div className="market-tag-menu" style={{ left: tagMenu.x, top: tagMenu.y + 8, zIndex: 1600 }}>
              <button onClick={() => {
                const next = tagMenu.tag;
                setKeyword(next);
                setTagMenu(null);
                setDetail(null);
                void runSearch(1, next);
                onNotify(`已按「${next}」搜索。`, "success");
              }}>
                <Search size={12} style={{ verticalAlign: "-1px", marginRight: 6 }} />搜索
              </button>
              <button onClick={() => {
                setRemovedTags((s) => new Set(s).add(tagMenu.tag));
                setTagMenu(null);
              }}>
                <X size={12} style={{ verticalAlign: "-1px", marginRight: 6 }} />删除
              </button>
            </div>
          </>,
          document.body,
        )}
      </div>
    </div>
  );
}
