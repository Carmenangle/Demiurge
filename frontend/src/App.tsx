import { lazy, Suspense, useEffect, useRef, useState } from "react";
import {
  WORK_MODES, type WorkMode, isWorkMode,
  NAV_SECTIONS, type NavSection, isNavSection,
  SECTION_SUBNAV,
  resolveActivityChatTarget,
} from "./lib/viewRouting";
import { useSettings, activeChatModel, resolvedEmbedModel, activeLiquidGlassWallpaper } from "./stores/settings";
import { liquidGlassCssVars, nextLiquidGlassWallpaper } from "./lib/liquidGlass";
import { LiquidGlassBackdrop } from "./components/LiquidGlassBackdrop";
import { useRepos, type Repo } from "./stores/repos";
import {
  parseSelection, pickSelection, readLocalSelection, reconcileSelection,
  writeLocalSelection, type LastSelection,
} from "./lib/lastSelection";
import { getUserState } from "./api/userState";
import { pushSelection } from "./lib/userStateSync";
import { Lightbox } from "./components/Lightbox";
import { RagToast } from "./components/RagToast";
import { ComfyStatusMonitor } from "./components/ComfyStatusMonitor";
import { ConfirmModal, PromptModal, AlertModal } from "./components/Modal";
import { RegexModal } from "./components/RegexModal";
import { PresetModal } from "./components/PresetModal";
import { BindRepoModal } from "./components/BindRepoModal";
import { fetchAgentRunning, listGenerations } from "./api/ai";
import { deleteRepoFolder } from "./api/userState";
import { AppBody } from "./AppBody";
import { WORKS_ROOT_UNCONFIGURED_EVENT } from "./api/client";
import { recentWorks as selectRecentWorks } from "./lib/repoPresentation";
import { hasPendingComfyGeneration, hasServerPendingComfyGeneration } from "./lib/comfyBackgroundActivity";

// 页面边界按需加载；应用壳、后台活动和快捷工具保持常驻。
const SupportWidget = lazy(() => import("./components/SupportWidget").then((m) => ({ default: m.SupportWidget })));
const QuickToolsWidget = lazy(() => import("./components/QuickToolsWidget").then((m) => ({ default: m.QuickToolsWidget })));
const LiquidGlassDock = lazy(() => import("./components/LiquidGlassDock").then((m) => ({ default: m.LiquidGlassDock })));

// 导航壳（重设计 v2）：左上 Demiurge▾ 常驻切三模式；返回键在 Demiurge 下方第一个（不顶掉 Demiurge）。
// 首页顶部 仓库▾+作品▾ 两级选择器；选作品后三种模式都进入该作品会话(ChatView)。
// 管理类三区钻入，左栏换「返回+子项」；设置在左下，走真实 SettingsView。
export function App() {
  const settingsStore = useSettings();
  const { settings } = settingsStore;
  const lg = settings.liquidGlass;

  // 液态玻璃的配色/透明度全部落到 CSS 变量；属性开关控制设置窗口与侧栏玻璃。
  useEffect(() => {
    const vars = liquidGlassCssVars(lg);
    const root = document.documentElement;
    for (const [key, value] of Object.entries(vars)) root.style.setProperty(key, value);
    root.dataset.lgSidebar = lg.sidebarGlass ? "on" : "off";
    root.dataset.lgSettings = lg.glassWindow ? "on" : "off";
  }, [lg]);

  // 自动轮换：只在液态玻璃主题下运行，参数变化后从当前壁纸重新计时。
  const lgRef = useRef(lg);
  lgRef.current = lg;
  const updateLgRef = useRef(settingsStore.updateLiquidGlass);
  updateLgRef.current = settingsStore.updateLiquidGlass;
  useEffect(() => {
    if (settings.theme !== "liquid-glass" || !lg.rotationEnabled || lg.wallpapers.length < 2) return;
    // 用户主动关闭壁纸（activeId 为空）时不轮换，避免「关了又自己回来」。
    if (!lg.activeWallpaperId) return;
    const timer = window.setInterval(() => {
      updateLgRef.current({ activeWallpaperId: nextLiquidGlassWallpaper(lgRef.current) });
    }, lg.rotationInterval * 1000);
    return () => window.clearInterval(timer);
  }, [settings.theme, lg.rotationEnabled, lg.rotationInterval, lg.wallpapers.length, lg.activeWallpaperId]);
  const {
    repos, hydrated: reposHydrated, addRepo, addCardWork, addBranch, renameRepo, bindRepo, resolveBinding,
    setCover, setGeneratedCover, touchRepo,
    relocateOutputPath, coverOf, deleteRepo, childrenOf,
  } = useRepos();

  const [workMode, setWorkMode] = useState<WorkMode>("story");
  const [section, setSection] = useState<NavSection>("home");
  const [subView, setSubView] = useState<string | null>(null);
  const initialSelection = useState(readLocalSelection)[0];
  // 大仓库（顶部选择器）/小仓库（作品）：localStorage 先手恢复，后端 selection 启动后按时间戳校正。
  const [repoId, setRepoId] = useState<string | null>(initialSelection.repoId || null);
  const [workId, setWorkId] = useState<string | null>(initialSelection.workId || null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  /** 打开设置时要落的左侧导航项（空串 = 默认「外观」）。P15：未配置仓库文件夹的弹窗一键直达「路径与代理」。 */
  const [settingsSection, setSettingsSection] = useState<string>("");
  /** 作品根校验因「未配置仓库文件夹」被拒时的引导弹窗文案（P15）。 */
  const [worksRootAlert, setWorksRootAlert] = useState<string | null>(null);
  const [modeMenuOpen, setModeMenuOpen] = useState(false);

  // 上次选择的双份记忆：localStorage 立即生效；user_state.selection 负责跨 WebView2
  // 数据目录（桌面壳受限上下文会换目录）与跨浏览器恢复。
  const selectionRef = useRef({
    repoId: initialSelection.repoId,
    workId: initialSelection.workId,
  });
  const lastPersistedSelection = useRef({
    repoId: initialSelection.repoId,
    workId: initialSelection.workId,
  });
  const selectionLoadedRef = useRef(false);
  selectionRef.current = { repoId: repoId || "", workId: workId || "" };

  // 启动合并：本地与后端都读，savedAt 较新者胜。合并完成前不把本地旧值写回后端，
  // 否则每次启动都会用旧缓存压掉后端记录。
  useEffect(() => {
    let alive = true;
    getUserState()
      .then((state) => {
        if (!alive) return;
        const merged = pickSelection(readLocalSelection(), parseSelection(state.selection));
        selectionLoadedRef.current = true;
        if (merged.repoId !== selectionRef.current.repoId) setRepoId(merged.repoId || null);
        if (merged.workId !== selectionRef.current.workId) setWorkId(merged.workId || null);
        lastPersistedSelection.current = { repoId: merged.repoId, workId: merged.workId };
        writeLocalSelection(merged);
        pushSelection(merged);
      })
      .catch(() => { if (alive) selectionLoadedRef.current = true; });
    return () => { alive = false; };
  }, []);

  // 之后每次切换仓库/作品：落 localStorage + 镜像后端（空选择也写，代表用户主动回到首页）。
  useEffect(() => {
    if (!selectionLoadedRef.current) return;
    const nextRepo = repoId || "";
    const nextWork = workId || "";
    if (nextRepo === lastPersistedSelection.current.repoId
      && nextWork === lastPersistedSelection.current.workId) return;
    const selection: LastSelection = { repoId: nextRepo, workId: nextWork, savedAt: Date.now() };
    lastPersistedSelection.current = { repoId: nextRepo, workId: nextWork };
    writeLocalSelection(selection);
    pushSelection(selection);
  }, [repoId, workId]);

  // 仓库表就绪后按真实数据校正：作品/仓库被删时退回大仓库或首页，不卡在空页。
  useEffect(() => {
    if (!reposHydrated) return;
    const reconciled = reconcileSelection(
      { repoId: repoId || "", workId: workId || "", savedAt: 0 }, repos,
    );
    const nextRepo = reconciled?.repoId || "";
    const nextWork = reconciled?.workId || "";
    if (nextRepo !== (repoId || "")) setRepoId(nextRepo || null);
    if (nextWork !== (workId || "")) setWorkId(nextWork || null);
  }, [reposHydrated, repos, repoId, workId]);

  // 资产·作品 钻入时的浏览态（大仓库→小仓库两级），与顶部选择器解耦
  const [browseRepoId, setBrowseRepoId] = useState<string | null>(null);
  // 仓库增删改弹窗
  const [creating, setCreating] = useState(false);
  const [creatingSubFor, setCreatingSubFor] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<Repo | null>(null);
  const [binding, setBinding] = useState<Repo | null>(null);
  const [deleting, setDeleting] = useState<Repo | null>(null);
  const [delBlocked, setDelBlocked] = useState<string | null>(null);
  const [nameErr, setNameErr] = useState<string | null>(null);
  const [marketSearch, setMarketSearch] = useState<string>("");
  const [regexOpen, setRegexOpen] = useState(false);  // 方法功能栏「正则」弹层（全局作用）
  const [presetOpen, setPresetOpen] = useState(false); // 方法功能栏「预设」弹层（仅剧情模式）

  const topRepos = childrenOf(undefined);          // 顶层 = 大仓库
  const works = childrenOf(repoId ?? undefined);    // 选中大仓库下 = 作品
  const activeWorkRaw = workId ? repos.find((r) => r.id === workId) ?? null : null;
  // 把有效绑定（自身缺则继承父仓库）合并进传给对话的 repo，下游读 repo.cardName 等即拿解析值；id 不变不影响 threadId。
  const activeWork = activeWorkRaw ? { ...activeWorkRaw, ...resolveBinding(activeWorkRaw) } : null;
  const selectedRepo = repoId ? topRepos.find((repo) => repo.id === repoId) ?? null : null;
  const recentWorks = selectRecentWorks(repos, 5);

  const selectRepo = (id: string | null) => {
    setRepoId(id);
    setWorkId(null);
    if (!id) return;
    const children = childrenOf(id);
    if (children.length === 1) {
      setWorkId(children[0].id);
      touchRepo(children[0].id);
    }
  };

  const selectWork = (id: string | null) => {
    setWorkId(id);
    if (id) touchRepo(id);
  };

  // 刷新后停留：hash = #/section 或 #/section/sub 或 #/workMode
  useEffect(() => {
    const [seg, sub] = window.location.hash.replace(/^#\/?/, "").split("/");
    if (isNavSection(seg)) {
      setSection(seg);
      if (seg !== "home") {
        // 子项有效性校验：无效/已迁移的旧子项（如 #/system/newcomer-guide）回退该区第一项
        const items = SECTION_SUBNAV[seg];
        setSubView(sub && items.some((item) => item.id === sub) ? sub : items[0].id);
      }
    } else if (isWorkMode(seg)) {
      setWorkMode(seg);
      setSection("home");
    }
  }, []);

  // 未配置「仓库文件夹」→ 引导弹窗（P15：后端把作品根校验统一为「未配置即拒绝」，前端半场负责
  // 把这条拒绝变成「一句话 + 一键去配置」）。事件由 `api/client.ts` 的 parseResponse 广播，
  // 覆盖所有走作品根校验的端点（artifacts / characters / comfyui / scenario / ai / plans），
  // 所以不需要在每个调用点写 catch。
  useEffect(() => {
    const onUnconfigured = (e: Event) => {
      const message = (e as CustomEvent<{ message?: string }>).detail?.message || "";
      setWorksRootAlert(message || "尚未配置「仓库文件夹」，请先在设置里填写后再试。");
    };
    window.addEventListener(WORKS_ROOT_UNCONFIGURED_EVENT, onUnconfigured);
    return () => window.removeEventListener(WORKS_ROOT_UNCONFIGURED_EVENT, onUnconfigured);
  }, []);

  const goMode = (m: WorkMode) => {
    setWorkMode(m);
    setSection("home");
    setSettingsOpen(false);
    setModeMenuOpen(false);
    // 模式按作品记忆：每个作品记住自己的功能栏（剧情/多元生成/编辑）
    if (workId) { try { localStorage.setItem(`laf_mode_${workId}`, m); } catch {} }
    window.location.hash = `#/${m}`;
  };

  // 打开/切换作品时恢复该作品记忆的模式（无记录保持当前；新作品默认 story）。
  // 顶部 Demiurge 下拉主动切模式走 goMode，不受此影响。
  const lastModeWorkRef = useRef<string | null>(null);
  useEffect(() => {
    if (!workId || workId === lastModeWorkRef.current) return;
    lastModeWorkRef.current = workId;
    let saved = "";
    try { saved = localStorage.getItem(`laf_mode_${workId}`) || ""; } catch {}
    const mode = isWorkMode(saved) ? saved : "story";
    setWorkMode(mode);
    setSection("home");
    setSettingsOpen(false);
    setSubView(null);
    window.location.hash = `#/${mode}`;
  }, [workId]);

  const goSection = (s: NavSection) => {
    setSection(s);
    setSettingsOpen(false);
    if (s === "home") {
      setSubView(null);
      window.location.hash = `#/${workMode}`;
    } else {
      const first = SECTION_SUBNAV[s][0].id;
      setSubView(first);
      setBrowseRepoId(null);
      window.location.hash = `#/${s}/${first}`;
    }
  };

  const openBackgroundChat = (threadId: string) => {
    // 首页画布（未绑小仓库）没有可解析的会话目标，但它的出图同样进后台活动面板：
    // 点击应回首页，而不是像以前那样静默无反应（2026-10-19）。
    if (threadId === "home") {
      setWorkId(null);
      setSection("home");
      setSettingsOpen(false);
      setSubView(null);
      return;
    }
    const target = resolveActivityChatTarget(repos, threadId);
    if (!target) return;
    setRepoId(target.repoId);
    setWorkId(target.workId);
    setSection("home");
    setSettingsOpen(false);
    setSubView(null);
    // 模式由 workId effect 恢复该作品记忆值（含 hash）
  };

  /** 后台活动里下载/节点/LoRA 条目 → 设置对应子视图。 */
  const openSettingsSubview = (subView: string) => {
    setSection("system");
    setSubView(subView);
    setSettingsOpen(false);
    setBrowseRepoId(null);
    window.location.hash = `#/system/${subView}`;
  };

  const drilledIn = section !== "home" && !settingsOpen;
  const currentModeLabel = WORK_MODES.find((m) => m.id === workMode)?.label ?? "";

  return (
    <div className="app">
      {/* 液态玻璃的全应用壁纸层；只在主题启用或设置页预览时挂载，避免其他主题后台解码视频。 */}
      {(settings.theme === "liquid-glass" || settingsOpen) && (
        <LiquidGlassBackdrop lg={lg} wallpaper={activeLiquidGlassWallpaper(settings)} />
      )}
      <Lightbox />
      <RagToast />
      <aside className="app-nav sidebar">
        {/* 固定品牌（模式下拉已合并：顶栏功能栏即全部功能；#/generate、#/code 旧 hash 仍兼容解析） */}
        <div className="mode-dropdown brand-only">
          <span className="mode-trigger" style={{ cursor: "default" }}>Demiurge</span>
        </div>
        {drilledIn ? (
          <DrillNav
            section={section as Exclude<NavSection, "home">}
            subView={subView}
            onBack={() => goSection("home")}
            onPick={(id) => {
              setSubView(id);
              setBrowseRepoId(null);
              window.location.hash = `#/${section}/${id}`;
            }}
          />
        ) : (
          <SectionNav
            currentModeLabel={currentModeLabel}
            section={settingsOpen ? null : section}
            onSection={goSection}
          />
        )}
        <button
          type="button"
          className={`nav-item nav-settings ${settingsOpen ? "active" : ""}`}
          onClick={() => { setSettingsSection(""); setSettingsOpen(true); }}
        >
          ⚙ 设置
        </button>
      </aside>

      <div className="app-main">
        <Topbar
          settingsOpen={settingsOpen}
          section={section}
          subView={subView}
          workMode={workMode}
          currentModeLabel={currentModeLabel}
          repos={topRepos}
          works={works}
          repoId={repoId}
          workId={workId}
          onRepo={selectRepo}
          onWork={selectWork}
          onOpenRegex={() => setRegexOpen(true)}
          onOpenPreset={() => setPresetOpen(true)}
          personas={(settings.userPersonas || []).map((p) => ({ id: p.id, name: p.name }))}
          activePersonaId={settings.activeUserPersonaId || ""}
          onPickPersona={(id) => settingsStore.update({ activeUserPersonaId: id || undefined })}
        />
        <ComfyStatusMonitor comfyuiUrl={settings.comfyuiUrl} />
        <main className="app-body main">
          <Suspense fallback={<div className="page-loading" role="status">正在载入…</div>}>
            <AppBody
            settingsOpen={settingsOpen}
            settingsSection={settingsSection}
            section={section}
            subView={subView}
            workMode={workMode}
            activeWork={activeWork}
            selectedRepo={selectedRepo}
            recentWorks={recentWorks}
            topRepos={topRepos}
            settingsStore={settingsStore}
            settings={settings}
            relocateOutputPath={relocateOutputPath}
            setCover={setCover}
            setGeneratedCover={setGeneratedCover}
            childrenOf={childrenOf}
            coverOf={coverOf}
            browseRepoId={browseRepoId}
            setBrowseRepoId={setBrowseRepoId}
            onNewRepo={() => setCreating(true)}
            onNewSub={(id) => setCreatingSubFor(id)}
            onRename={setRenaming}
            onBind={setBinding}
            onDelete={setDeleting}
            onOpenWork={(rid, wid) => {
              setRepoId(rid);
              setWorkId(wid);
              touchRepo(wid);
              setSection("home");
              setSettingsOpen(false);
              setSubView(null);
              // 模式由 workId effect 恢复该作品记忆值（含 hash）
            }}
            onClearRepo={() => { setRepoId(null); setWorkId(null); }}
            onGoSection={(section, subView) => {
              setSection(section);
              setSettingsOpen(false);
              setSubView(subView);
              setBrowseRepoId(null);
              window.location.hash = `#/${section}/${subView}`;
            }}
            onOpenPreset={() => setPresetOpen(true)}
            addCardWork={addCardWork}
            addBranch={addBranch}
            marketSearch={marketSearch}
            setMarketSearch={setMarketSearch}
            />
          </Suspense>
        </main>
        <Suspense fallback={null}>
          <SupportWidget
            chat={activeChatModel(settings)}
            embed={resolvedEmbedModel(settings)}
            repoId={workId || repoId || "home"}
            onOpenChat={openBackgroundChat}
            onOpenSettings={openSettingsSubview}
          />
          <QuickToolsWidget onOpenFull={() => {
            setSection("system");
            setSubView("tools");
            setSettingsOpen(false);
            setBrowseRepoId(null);
            window.location.hash = "#/system/tools";
          }} />
        </Suspense>
      </div>

      {worksRootAlert && (
        <AlertModal
          title="尚未配置「仓库文件夹」"
          message={worksRootAlert}
          actions={[
            { label: "稍后再说", onClick: () => setWorksRootAlert(null) },
            {
              label: "去配置",
              primary: true,
              onClick: () => {
                // 直达「设置 → 路径与代理」；仓库文件夹为空时 PathsPanel 会自动定位并高亮该输入框
                setWorksRootAlert(null);
                setSettingsSection("paths");
                setSettingsOpen(true);
              },
            },
          ]}
          onClose={() => setWorksRootAlert(null)}
        />
      )}
      {creating && (
        <PromptModal
          title="新建仓库"
          confirmText="创建"
          onConfirm={(name) => {
            if (!addRepo(name)) { setNameErr("已有同名仓库，请换一个名字。"); return; }
            setCreating(false);
          }}
          onCancel={() => setCreating(false)}
        />
      )}
      {creatingSubFor && (
        <PromptModal
          title="新建作品（小仓库）"
          confirmText="创建"
          onConfirm={(name) => {
            if (!addRepo(name, creatingSubFor)) { setNameErr("该仓库下已有同名作品，请换一个名字。"); return; }
            setCreatingSubFor(null);
          }}
          onCancel={() => setCreatingSubFor(null)}
        />
      )}
      {renaming && (
        <PromptModal
          title="重命名"
          defaultValue={renaming.name}
          confirmText="保存"
          onConfirm={(name) => {
            if (!renameRepo(renaming.id, name)) { setNameErr("同层级已有同名，请换一个名字。"); return; }
            setRenaming(null);
          }}
          onCancel={() => setRenaming(null)}
        />
      )}
      {binding && (
        <BindRepoModal
          repo={binding}
          characterDir={settings.characterDir}
          outputDir={settings.outputDir}
          worldbookDir={settings.worldbookDir}
          presetDir={settings.presetDir}
          personas={settings.userPersonas || []}
          onSave={(patch) => bindRepo(binding.id, patch)}
          onClose={() => setBinding(null)}
        />
      )}
      {deleting && (
        <ConfirmModal
          title="删除仓库"
          message={`确认删除「${deleting.name}」？此操作不可恢复。`}
          confirmText="删除"
          danger
          onConfirm={async () => {
            const target = deleting;
            setDeleting(null);
            const ids = [target.id, ...childrenOf(target.id).map((c) => c.id)];
            // 先查本浏览器（同步、零延迟），再查服务端登记（换浏览器/清缓存后仍能挡住删除）。
            if (hasPendingComfyGeneration(ids) || await hasServerPendingComfyGeneration(ids)) {
              setDelBlocked(`「${target.name}」还有生图任务未完成，已阻止删除。请等待归档完成或先显式停止任务。`);
              return;
            }
            for (const id of ids) {
              try {
                if ((await fetchAgentRunning(id)).running) {
                  setDelBlocked(`「${target.name}」还有剧情或插画任务在运行，已阻止删除。`);
                  return;
                }
              } catch { /* 后台状态不可用时仍继续检查本地资产 */ }
            }
            let hasAssets = false;
            for (const id of ids) {
              try {
                const r = await listGenerations(id, resolvedEmbedModel(settings));
                if ((r.items || []).length > 0) { hasAssets = true; break; }
              } catch { /* 查询失败按无资产处理 */ }
            }
            if (hasAssets) {
              setDelBlocked(`「${target.name}」里还有生成图（资产），已阻止删除。请先在资产库删除这些图片，再删仓库。`);
              return;
            }
            // 源库-作品解耦：删仓库只清作品自己的文件夹（快照卡/世界书/persona/会话/图），
            // 绝不碰源库角色卡与独立世界书（它们是可复用素材，供别的作品继续用）。父+子文件夹都清。
            if (settings.outputDir) {
              for (const r of [target, ...childrenOf(target.id)]) {
                await deleteRepoFolder({ repo_id: r.id, name: r.name, output_dir: settings.outputDir })
                  .catch(() => { /* 文件夹不存在则忽略 */ });
              }
            }
            deleteRepo(target.id);
            if (browseRepoId === target.id) setBrowseRepoId(null);
            if (repoId === target.id) { setRepoId(null); setWorkId(null); }
            if (workId === target.id) setWorkId(null);
          }}
          onCancel={() => setDeleting(null)}
        />
      )}
      {delBlocked && (
        <ConfirmModal title="无法删除仓库" message={delBlocked} confirmText="知道了"
          onConfirm={() => setDelBlocked(null)} onCancel={() => setDelBlocked(null)} />
      )}
      {nameErr && (
        <ConfirmModal title="名字不可用" message={nameErr} confirmText="知道了"
          onConfirm={() => setNameErr(null)} onCancel={() => setNameErr(null)} />
      )}
      {regexOpen && (
        <RegexModal
          cardName={activeWork?.cardName}
          characterDir={settings.characterDir}
          presetName={settings.activePresetName}
          presetDir={settings.presetDir}
          onClose={() => setRegexOpen(false)}
        />
      )}
      {presetOpen && (
        <PresetModal
          base={settings.presetDir}
          activeName={settings.activePresetName}
          onSelectActive={(name) => settingsStore.update({ activePresetName: name })}
          onClose={() => setPresetOpen(false)}
        />
      )}
      {settings.theme === "liquid-glass" && !settingsOpen && (
        <Suspense fallback={null}>
          <LiquidGlassDock lg={lg} update={settingsStore.updateLiquidGlass} />
        </Suspense>
      )}
    </div>
  );
}

function ModeDropdown(props: {
  workMode: WorkMode;
  open: boolean;
  setOpen: (v: boolean) => void;
  onMode: (m: WorkMode) => void;
}) {
  return (
    <div className="mode-dropdown">
      <button type="button" className="mode-trigger" onClick={() => props.setOpen(!props.open)}>
        Demiurge <span className="caret">▾</span>
      </button>
      {props.open && (
        <ul className="mode-menu">
          {WORK_MODES.map((m) => (
            <li key={m.id}>
              <button type="button" onClick={() => props.onMode(m.id)}>
                <span className="mode-name">{m.label}</span>
                <span className="mode-hint">{m.hint}</span>
                {m.id === props.workMode && <span className="check">✓</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SectionNav(props: {
  currentModeLabel: string;
  section: NavSection | null;
  onSection: (s: NavSection) => void;
}) {
  return (
    <nav className="nav-sections">
      {NAV_SECTIONS.map((s) => (
        <button
          key={s.id}
          type="button"
          className={`nav-item ${s.id === props.section ? "active" : ""}`}
          onClick={() => props.onSection(s.id)}
        >
          {s.id === "home" ? "创作平台" : s.label}
        </button>
      ))}
    </nav>
  );
}

function DrillNav(props: {
  section: Exclude<NavSection, "home">;
  subView: string | null;
  onBack: () => void;
  onPick: (id: string) => void;
}) {
  return (
    <nav className="nav-sections drill">
      <button type="button" className="nav-item nav-back" onClick={props.onBack}>‹ 返回</button>
      {SECTION_SUBNAV[props.section].map((item) => (
        <button
          key={item.id}
          type="button"
          className={`nav-item ${item.id === props.subView ? "active" : ""}`}
          onClick={() => props.onPick(item.id)}
        >
          {item.label}
        </button>
      ))}
    </nav>
  );
}

function Topbar(props: {
  settingsOpen: boolean;
  section: NavSection;
  subView: string | null;
  workMode: WorkMode;
  currentModeLabel: string;
  repos: Repo[];
  works: Repo[];
  repoId: string | null;
  workId: string | null;
  onRepo: (id: string | null) => void;
  onWork: (id: string | null) => void;
  onOpenRegex: () => void;
  onOpenPreset: () => void;
  personas: { id: string; name: string }[];
  activePersonaId: string;
  onPickPersona: (id: string) => void;
}) {
  if (props.settingsOpen) return <header className="app-topbar"><span className="topbar-title">设置</span></header>;
  if (props.section === "home") {
    return (
      <header className="app-topbar">
        <span className="topbar-title">创作</span>
        <label className="repo-picker">
          仓库
          <select value={props.repoId ?? ""} onChange={(e) => props.onRepo(e.target.value || null)}>
            <option value="">（选仓库）</option>
            {props.repos.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </label>
        <label className="repo-picker">
          作品
          <select value={props.workId ?? ""} onChange={(e) => props.onWork(e.target.value || null)} disabled={!props.repoId}>
            <option value="">（选作品）</option>
            {props.works.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </label>
        {/* 用户角色切换（作品选择器右侧，仅剧情模式）：切当前扮演的「我是谁」，空=不注入（{{user}} 回退「我」） */}
        {props.workMode === "story" && (
          <label className="repo-picker">
            我
            <select value={props.activePersonaId} onChange={(e) => props.onPickPersona(e.target.value)} title="切换用户角色（填 {{user}}）">
              <option value="">（默认：我）</option>
              {props.personas.map((p) => <option key={p.id} value={p.id}>{p.name || "（未命名）"}</option>)}
            </select>
          </label>
        )}
        {/* 方法功能栏（仓库/作品右侧）：正则全局作用常驻；预设仅剧情模式显示 */}
        <div className="topbar-methods" style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          {props.workMode === "story" && (
            <button type="button" className="btn" onClick={props.onOpenPreset} title="偏置预设（导入/查看/编辑/激活，仅剧情模式）">预设</button>
          )}
          <button type="button" className="btn" onClick={props.onOpenRegex} title="全局正则（隐藏/压缩输出、改写输入等）">正则</button>
        </div>
      </header>
    );
  }
  const subLabel = props.subView
    ? (SECTION_SUBNAV[props.section].find((s) => s.id === props.subView)?.label ?? "")
    : "";
  const secLabel = NAV_SECTIONS.find((s) => s.id === props.section)?.label ?? "";
  return <header className="app-topbar"><span className="topbar-title">{secLabel}{subLabel ? ` · ${subLabel}` : ""}</span></header>;
}
