import { lazy, Suspense, useEffect, useState } from "react";
import {
  WORK_MODES, type WorkMode, isWorkMode,
  NAV_SECTIONS, type NavSection, isNavSection,
  SECTION_SUBNAV,
  resolveActivityChatTarget,
} from "./lib/viewRouting";
import { useSettings, activeChatModel, resolvedEmbedModel } from "./stores/settings";
import { useRepos, type Repo } from "./stores/repos";
import { Lightbox } from "./components/Lightbox";
import { RagToast } from "./components/RagToast";
import { ConfirmModal, PromptModal } from "./components/Modal";
import { RegexModal } from "./components/RegexModal";
import { PresetModal } from "./components/PresetModal";
import { BindRepoModal } from "./components/BindRepoModal";
import { listGenerations } from "./api/ai";
import { deleteRepoFolder } from "./api/userState";
import { AppBody } from "./AppBody";

// 页面边界按需加载；应用壳、后台活动和快捷工具保持常驻。
const SupportWidget = lazy(() => import("./components/SupportWidget").then((m) => ({ default: m.SupportWidget })));
const QuickToolsWidget = lazy(() => import("./components/QuickToolsWidget").then((m) => ({ default: m.QuickToolsWidget })));

// 导航壳（重设计 v2）：左上 Demiurge▾ 常驻切三模式；返回键在 Demiurge 下方第一个（不顶掉 Demiurge）。
// 首页顶部 仓库▾+作品▾ 两级选择器；选作品后三种模式都进入该作品会话(ChatView)。
// 管理类三区钻入，左栏换「返回+子项」；设置在左下，走真实 SettingsView。
export function App() {
  const settingsStore = useSettings();
  const { settings } = settingsStore;
  const {
    repos, addRepo, addCardWork, addBranch, renameRepo, bindRepo, resolveBinding,
    setCover, setGeneratedCover,
    relocateOutputPath, coverOf, deleteRepo, childrenOf,
  } = useRepos();

  const [workMode, setWorkMode] = useState<WorkMode>("story");
  const [section, setSection] = useState<NavSection>("home");
  const [subView, setSubView] = useState<string | null>(null);
  const [repoId, setRepoId] = useState<string | null>(null); // 大仓库（顶部选择器）
  const [workId, setWorkId] = useState<string | null>(null); // 小仓库/作品（顶部选择器）
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [modeMenuOpen, setModeMenuOpen] = useState(false);

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

  // 刷新后停留：hash = #/section 或 #/section/sub 或 #/workMode
  useEffect(() => {
    const [seg, sub] = window.location.hash.replace(/^#\/?/, "").split("/");
    if (isNavSection(seg)) {
      setSection(seg);
      if (seg !== "home" && sub) setSubView(sub);
      else if (seg !== "home") setSubView(SECTION_SUBNAV[seg][0].id);
    } else if (isWorkMode(seg)) {
      setWorkMode(seg);
      setSection("home");
    }
  }, []);

  const goMode = (m: WorkMode) => {
    setWorkMode(m);
    setSection("home");
    setSettingsOpen(false);
    setModeMenuOpen(false);
    window.location.hash = `#/${m}`;
  };

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
    const target = resolveActivityChatTarget(repos, threadId);
    if (!target) return;
    setRepoId(target.repoId);
    setWorkId(target.workId);
    setWorkMode("story");
    setSection("home");
    setSettingsOpen(false);
    setSubView(null);
    window.location.hash = "#/story";
  };

  const drilledIn = section !== "home" && !settingsOpen;
  const currentModeLabel = WORK_MODES.find((m) => m.id === workMode)?.label ?? "";

  return (
    <div className="app">
      <Lightbox />
      <RagToast />
      <aside className="app-nav sidebar">
        {/* Demiurge▾ 常驻顶部 */}
        <ModeDropdown
          workMode={workMode}
          open={modeMenuOpen}
          setOpen={setModeMenuOpen}
          onMode={goMode}
        />
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
          onClick={() => setSettingsOpen(true)}
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
          onRepo={(id) => { setRepoId(id); setWorkId(null); }}
          onWork={setWorkId}
          onOpenRegex={() => setRegexOpen(true)}
          onOpenPreset={() => setPresetOpen(true)}
          personas={(settings.userPersonas || []).map((p) => ({ id: p.id, name: p.name }))}
          activePersonaId={settings.activeUserPersonaId || ""}
          onPickPersona={(id) => settingsStore.update({ activeUserPersonaId: id || undefined })}
        />
        <main className="app-body main">
          <Suspense fallback={<div className="page-loading" role="status">正在载入…</div>}>
            <AppBody
            settingsOpen={settingsOpen}
            section={section}
            subView={subView}
            workMode={workMode}
            activeWork={activeWork}
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
              setWorkMode("story");
              setSection("home");
              setSettingsOpen(false);
              setSubView(null);
              window.location.hash = "#/story";
            }}
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
          {s.id === "home" ? props.currentModeLabel : s.label}
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
        <span className="topbar-title">{props.currentModeLabel}</span>
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
