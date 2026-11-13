import { useEffect, useMemo, useRef, useState } from "react";
import { Boxes, Images, Link2, MoreHorizontal, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { type Repo } from "../../stores/repos";
import { useSettings } from "../../stores/settings";
import { Pager } from "../../components/Pager";
import { PageShell } from "../../components/layout/PageShell";
import { listGenerations } from "../../api/ai";
import { formatLastUsed, repoLastUsedAt } from "../../lib/repoPresentation";
import { coverNeedsRepair, isVideoCover, latestGeneratedCover } from "../../lib/repoOrdering";
import { resolvedEmbedModel, type Settings } from "../../stores/settings";

type ChildrenOf = (parentId?: string) => Repo[];

function useAssetCounts(
  repos: Repo[],
  childrenOf: ChildrenOf,
  settings: Settings,
  onGeneratedCover?: (id: string, cover: string) => void,
): Map<string, number> {
  const [direct, setDirect] = useState<Record<string, number>>({});
  const ids = useMemo(() => [...new Set(repos.flatMap((repo) => [
    repo.id, ...(!repo.parentId ? childrenOf(repo.id).map((child) => child.id) : []),
  ]))], [repos, childrenOf]);
  const signature = ids.join("|");
  useEffect(() => {
    let alive = true;
    const load = () => Promise.all(ids.map(async (id) => {
      try { return [id, (await listGenerations(id, resolvedEmbedModel(settings))).items] as const; }
      catch { return [id, []] as const; }
    })).then((pairs) => {
      if (!alive) return;
      setDirect(Object.fromEntries(pairs.map(([id, items]) => [id, items.length])));
      if (!onGeneratedCover) return;
      // 历史封面回填：旧版本只在出图片时写 cover，视频资产（尤其是裁切自视频的封面）
      // 会停在旧图。这里复用已经取回的资产列表，把「最新生成媒体」补成封面；
      // 只在资产时间晚于 coverAt 时动，手动选封面（coverAt 不前进）不会被无休止覆盖。
      const repoById = new Map<string, Repo>();
      for (const repo of repos) {
        repoById.set(repo.id, repo);
        for (const child of childrenOf(repo.id)) repoById.set(child.id, child);
      }
      for (const [id, items] of pairs) {
        const repo = repoById.get(id);
        if (!repo) continue;
        const latest = latestGeneratedCover(items);
        if (latest && coverNeedsRepair(repo.cover, repo.coverAt, latest)) {
          onGeneratedCover(id, latest.url);
        }
      }
    });
    void load();
    const refresh = () => { void load(); };
    window.addEventListener("laf-generation-saved", refresh);
    return () => { alive = false; window.removeEventListener("laf-generation-saved", refresh); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, settings.embedModel]);
  return useMemo(() => new Map(repos.map((repo) => {
    const idsForRepo = repo.parentId ? [repo.id] : [repo.id, ...childrenOf(repo.id).map((child) => child.id)];
    return [repo.id, idsForRepo.reduce((sum, id) => sum + (direct[id] || 0), 0)];
  })), [repos, childrenOf, direct]);
}

// 仓库封面：图片加载失败（本地文件被删/ComfyUI 离线/地址失效）时回退占位，不显示破图。
// 视频封面用静音循环的 <video> 呈现动态效果；进入视口才播放、离开即暂停，
// 避免一屏几十个作品同时解码视频把首页拖慢；系统开启「减少动态效果」时只保留首帧。
export function RepoCover({ src, name }: { src?: string; name: string }) {
  const [failed, setFailed] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const isVideo = isVideoCover(src);
  useEffect(() => { setFailed(false); }, [src]);  // 换封面地址后重置
  useEffect(() => {
    const el = videoRef.current;
    if (!el || !isVideo || failed) return;
    const reduced = typeof window !== "undefined"
      && window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    if (reduced) return;
    const safePlay = () => { try { void el.play()?.catch(() => undefined); } catch { /* jsdom/不支持自动播放 */ } };
    const safePause = () => { try { el.pause(); } catch { /* 未加载完的暂停失败忽略 */ } };
    if (typeof IntersectionObserver === "undefined") {
      safePlay();
      return () => safePause();
    }
    let active = true;
    const io = new IntersectionObserver(([entry]) => {
      if (!active) return;
      if (entry.isIntersecting) safePlay();
      else safePause();
    }, { threshold: 0.2 });
    io.observe(el);
    return () => { active = false; io.disconnect(); safePause(); };
  }, [src, isVideo, failed]);
  if (!src || failed) {
    return (
      <span className="repo-cover-empty">
        <Images size={22} strokeWidth={1.4} />
        <span>暂无封面</span>
      </span>
    );
  }
  if (isVideo) {
    return (
      <>
        <video
          ref={videoRef}
          src={src}
          muted
          loop
          playsInline
          preload="metadata"
          aria-label={`${name}封面`}
          onError={() => setFailed(true)}
        />
        <span className="repo-cover-live">动态</span>
      </>
    );
  }
  return <img src={src} alt="" aria-label={`${name}封面`} loading="lazy" onError={() => setFailed(true)} />;
}

export function RepoGrid({
  repos,
  emptyText,
  coverOf,
  onOpen,
  onBind,
  onRename,
  onDelete,
  childrenOf,
  assetCounts,
  displayNameOf,
}: {
  repos: Repo[];
  emptyText: string;
  coverOf: (r: Repo) => string | undefined;
  onOpen: (r: Repo) => void;
  onBind: (r: Repo) => void;
  onRename: (r: Repo) => void;
  onDelete: (r: Repo) => void;
  childrenOf: ChildrenOf;
  assetCounts: ReadonlyMap<string, number>;
  displayNameOf?: (repo: Repo) => string;
}) {
  // P1-8（2026-10-28）：卡片「⋯」菜单同一时刻只开一张，点外部/Esc 关闭
  const [menuId, setMenuId] = useState<string | null>(null);
  useEffect(() => {
    if (!menuId) return;
    const onDown = (e: MouseEvent) => {
      if (!(e.target as Element)?.closest?.(".repo-card-more")) setMenuId(null);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenuId(null); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuId]);
  if (repos.length === 0) {
    return (
      <div className="empty-state">
        <Boxes size={32} strokeWidth={1.4} style={{ opacity: 0.5 }} />
        <p style={{ margin: 0 }}>{emptyText}</p>
      </div>
    );
  }
  return (
    <div className="repo-grid">
      {repos.map((r) => {
        const cover = coverOf(r);
        const children = r.parentId ? [] : childrenOf(r.id);
        const cardNames = r.cardNames?.length ? r.cardNames : (r.cardName ? [r.cardName] : []);
        return (
        <div className="repo-card" key={r.id}>
          <div className="repo-cover-wrap">
            <div className="repo-cover" onClick={() => onOpen(r)} title="点击打开">
              <RepoCover src={cover} name={r.name} />
            </div>
            {/* 主操作＝点封面打开；绑定常驻，重命名/删除低频且危险 ⇒ 收进「⋯」菜单 */}
            <div className="repo-tools">
            <button
              className={`icon-btn ${r.cardName || r.worldbookName || r.personaId || r.presetName ? "is-bound" : ""}`}
              title={r.cardName || r.worldbookName || r.personaId || r.presetName
                ? `绑定：${[r.cardName && `卡「${r.cardName}」`, r.worldbookName && `世界书「${r.worldbookName}」`, r.personaId && "已设人设", r.presetName && `预设「${r.presetName}」`].filter(Boolean).join("，")}`
                : "绑定角色卡 / 世界书 / 用户设定 / 预设"}
              onClick={() => onBind(r)}
            >
              <Link2 size={15} />
            </button>
            <div className="repo-card-more">
              <button className="icon-btn" title="更多操作（重命名 / 删除）"
                aria-haspopup="menu" aria-expanded={menuId === r.id}
                onClick={() => setMenuId((cur) => (cur === r.id ? null : r.id))}>
                <MoreHorizontal size={15} />
              </button>
              {menuId === r.id && (
                <div className="repo-card-menu" role="menu" aria-label={`${r.name} 操作`}>
                  <button role="menuitem" onClick={() => { setMenuId(null); onRename(r); }}>
                    <Pencil size={14} />重命名
                  </button>
                  <button role="menuitem" className="danger" onClick={() => { setMenuId(null); onDelete(r); }}>
                    <Trash2 size={14} />删除
                  </button>
                </div>
              )}
            </div>
            </div>
          </div>
          <div className="repo-name">{displayNameOf?.(r) || r.name}</div>
          <div className="repo-card-meta">
            <span title={cardNames.join("、")}>角色：{cardNames.length ? cardNames.join("、") : "未绑定"}</span>
            {!r.parentId && <span>作品：{children.length}</span>}
            <span>资产：{assetCounts.get(r.id) || 0}</span>
            {r.presetName && <span>预设：{r.presetName}</span>}
            <span>最近使用：{formatLastUsed(repoLastUsedAt(r, children))}</span>
          </div>
        </div>
        );
      })}
    </div>
  );
}

export function ReposView({
  repos,
  title,
  coverOf,
  onOpen,
  onBind,
  onRename,
  onDelete,
  onNew,
  childrenOf,
  settings,
  onGeneratedCover,
}: {
  repos: Repo[];
  title: string;
  coverOf: (r: Repo) => string | undefined;
  onOpen: (r: Repo) => void;
  onBind: (r: Repo) => void;
  onRename: (r: Repo) => void;
  onDelete: (r: Repo) => void;
  onNew: () => void;
  childrenOf: ChildrenOf;
  settings: Settings;
  onGeneratedCover?: (id: string, cover: string) => void;
}) {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const kw = q.trim().toLowerCase();
  const shownRepos = kw ? repos.filter((r) => r.name.toLowerCase().includes(kw)) : repos;
  const REPO_PAGE_SIZE = 20;  // 每行 5 个，一页最多 4 行；超过才翻页（正常向下增多）
  const repoPageCount = Math.max(1, Math.ceil(shownRepos.length / REPO_PAGE_SIZE));
  const curPage = Math.min(page, repoPageCount);
  const pagedRepos = shownRepos.slice((curPage - 1) * REPO_PAGE_SIZE, curPage * REPO_PAGE_SIZE);
  const assetCounts = useAssetCounts(pagedRepos, childrenOf, settings, onGeneratedCover);
  return (
    <PageShell
      title={title}
      actions={
        <button className="btn" onClick={onNew}>
          <Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          新建仓库
        </button>
      }
      toolbar={repos.length > 0 ? (
        <div className="search-field">
          <Search size={14} style={{ position: "absolute", left: 9, top: 9, color: "var(--text-muted)" }} />
          <input style={{ width: "100%", paddingLeft: 28, boxSizing: "border-box" }} placeholder="搜索仓库名称…"
            value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        </div>
      ) : undefined}
    >
      <RepoGrid
        repos={pagedRepos}
        emptyText={kw ? `没有名称含「${q}」的仓库。` : "还没有仓库，点击「新建仓库」创建一个。"}
        coverOf={coverOf}
        onOpen={onOpen}
        onBind={onBind}
        onRename={onRename}
        onDelete={onDelete}
        childrenOf={childrenOf}
        assetCounts={assetCounts}
      />
      <Pager page={curPage} pageCount={repoPageCount} onPage={setPage} />
    </PageShell>
  );
}

export function RepoDetailView({
  repo,
  children,
  coverOf,
  settings,
  onBack,
  onOpen,
  onBind,
  onRename,
  onDelete,
  onNewSub,
  onGeneratedCover,
}: {
  repo: Repo;
  children: Repo[];
  coverOf: (r: Repo) => string | undefined;
  settings: ReturnType<typeof useSettings>["settings"];
  onBack: () => void;
  onOpen: (r: Repo) => void;
  onBind: (r: Repo) => void;
  onRename: (r: Repo) => void;
  onDelete: (r: Repo) => void;
  onNewSub: () => void;
  onGeneratedCover?: (id: string, cover: string) => void;
}) {
  const [subQ, setSubQ] = useState("");
  const [subPage, setSubPage] = useState(1);
  const SUB_PAGE_SIZE = 20;  // 每行 5 × 4 行，多了翻页（资产库已独立成一级页，这里不再嵌）
  const kw = subQ.trim().toLowerCase();
  const matched = kw ? children.filter((r) => r.name.toLowerCase().includes(kw)) : children;
  const subPageCount = Math.max(1, Math.ceil(matched.length / SUB_PAGE_SIZE));
  const subCur = Math.min(subPage, subPageCount);
  const shownChildren = matched.slice((subCur - 1) * SUB_PAGE_SIZE, subCur * SUB_PAGE_SIZE);
  const assetCounts = useAssetCounts(shownChildren, () => [], settings, onGeneratedCover);
  return (
    <PageShell
      title={repo.name}
      back={onBack}
      actions={
        <button className="btn" onClick={onNewSub}>
          <Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          新建小仓库
        </button>
      }
    >
      <h3 style={{ margin: "4px 0 12px", fontSize: 15 }}>小仓库（角色 / 画风等）</h3>
      {children.length > 0 && (
        <div className="search-field" style={{ marginBottom: 12 }}>
          <Search size={14} style={{ position: "absolute", left: 9, top: 9, color: "var(--text-muted)" }} />
          <input style={{ width: "100%", paddingLeft: 28, boxSizing: "border-box" }} placeholder="搜索小仓库名称…"
            value={subQ} onChange={(e) => { setSubQ(e.target.value); setSubPage(1); }} />
        </div>
      )}
      <RepoGrid
        repos={shownChildren}
        emptyText={kw ? `没有名称含「${subQ}」的小仓库。` : "还没有小仓库，点击「新建小仓库」来存放角色、画风等内容。"}
        coverOf={coverOf}
        onOpen={onOpen}
        onBind={onBind}
        onRename={onRename}
        onDelete={onDelete}
        childrenOf={() => []}
        assetCounts={assetCounts}
      />
      <Pager page={subCur} pageCount={subPageCount} onPage={setSubPage} />
    </PageShell>
  );
}

export function HomeRecentWorks({
  works, parents, coverOf, settings, childrenOf, onOpen, onBind, onRename, onDelete, onGeneratedCover,
}: {
  works: Repo[];
  parents: Repo[];
  coverOf: (repo: Repo) => string | undefined;
  settings: Settings;
  childrenOf: ChildrenOf;
  onOpen: (repo: Repo) => void;
  onBind: (repo: Repo) => void;
  onRename: (repo: Repo) => void;
  onDelete: (repo: Repo) => void;
  onGeneratedCover?: (id: string, cover: string) => void;
}) {
  const assetCounts = useAssetCounts(works, () => [], settings, onGeneratedCover);
  if (!works.length) {
    return <div className="need-work"><p>在上方选择仓库与作品</p>
      <small>使用过的作品会在这里显示，最多保留最近 5 个</small></div>;
  }
  return <PageShell title="最近使用的作品">
    <p className="field-hint home-recent-hint">双击作品进入对话；仓库、作品与我的设定仍可在顶部直接切换。</p>
    <RepoGrid repos={works} emptyText="暂无最近作品" coverOf={coverOf} onOpen={onOpen}
      onBind={onBind} onRename={onRename} onDelete={onDelete}
      childrenOf={childrenOf} assetCounts={assetCounts}
      displayNameOf={(work) => `${parents.find((parent) => parent.id === work.parentId)?.name || "仓库"} · ${work.name}`} />
  </PageShell>;
}
