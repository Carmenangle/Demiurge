import { lazy } from "react";
import { type NavSection, type WorkMode } from "./lib/viewRouting";
import { activeUserPersona, type useSettings } from "./stores/settings";
import { type Repo, type RepoBinding, type useRepos } from "./stores/repos";
import { SectionPlaceholder } from "./modes/SectionPlaceholder";
import { saveSnapshot } from "./api/ai";
import { createScenarioSnapshot, forkScenarioSnapshot, listScenarioSnapshots } from "./api/scenario";
import { createScenarioBranch } from "./lib/scenarioBranchRuntime";

const SettingsView = lazy(() => import("./views/settings/SettingsView").then((m) => ({ default: m.SettingsView })));
const WorkflowTemplates = lazy(() => import("./pages/WorkflowTemplates").then((m) => ({ default: m.WorkflowTemplates })));
const ModelDownload = lazy(() => import("./pages/ModelDownload").then((m) => ({ default: m.ModelDownload })));
const CharacterCards = lazy(() => import("./pages/CharacterCards").then((m) => ({ default: m.CharacterCards })));
const WorldBook = lazy(() => import("./pages/WorldBook").then((m) => ({ default: m.WorldBook })));
const NodeIndexView = lazy(() => import("./views/NodeIndexView").then((m) => ({ default: m.NodeIndexView })));
const AIBuildView = lazy(() => import("./views/AIBuildView").then((m) => ({ default: m.AIBuildView })));
const NodeManagerView = lazy(() => import("./views/NodeManagerView").then((m) => ({ default: m.NodeManagerView })));
const LoraTriggersTab = lazy(() => import("./views/tools/LoraTriggersTab").then((m) => ({ default: m.LoraTriggersTab })));
const ToolsView = lazy(() => import("./views/ToolsView").then((m) => ({ default: m.ToolsView })));
const ReposView = lazy(() => import("./views/repos/RepoViews").then((m) => ({ default: m.ReposView })));
const RepoDetailView = lazy(() => import("./views/repos/RepoViews").then((m) => ({ default: m.RepoDetailView })));
const HomeRecentWorks = lazy(() => import("./views/repos/RepoViews").then((m) => ({ default: m.HomeRecentWorks })));
const AssetsView = lazy(() => import("./views/PlaceholderViews").then((m) => ({ default: m.AssetsView })));
const ChatView = lazy(() => import("./views/ChatView").then((m) => ({ default: m.ChatView })));

export interface AppBodyProps {
  settingsOpen: boolean;
  section: NavSection;
  subView: string | null;
  workMode: WorkMode;
  activeWork: Repo | null;
  selectedRepo: Repo | null;
  recentWorks: Repo[];
  topRepos: Repo[];
  settingsStore: ReturnType<typeof useSettings>;
  settings: ReturnType<typeof useSettings>["settings"];
  relocateOutputPath: ReturnType<typeof useRepos>["relocateOutputPath"];
  setCover: ReturnType<typeof useRepos>["setCover"];
  setGeneratedCover: ReturnType<typeof useRepos>["setGeneratedCover"];
  childrenOf: ReturnType<typeof useRepos>["childrenOf"];
  coverOf: ReturnType<typeof useRepos>["coverOf"];
  browseRepoId: string | null;
  setBrowseRepoId: (id: string | null) => void;
  onNewRepo: () => void;
  onNewSub: (id: string) => void;
  onRename: (repo: Repo) => void;
  onBind: (repo: Repo) => void;
  onDelete: (repo: Repo) => void;
  onOpenWork: (repoId: string, workId: string) => void;
  onClearRepo: () => void;
  addCardWork: (cardName: string) => { parentId: string; childId: string };
  addBranch: (parentId: string, binding?: Partial<RepoBinding>, branchId?: string) => string;
  marketSearch: string;
  setMarketSearch: (query: string) => void;
}

export function AppBody(props: AppBodyProps) {
  const { settingsStore, settings } = props;
  if (props.settingsOpen) {
    return <SettingsView settings={settings} update={settingsStore.update}
      onOutputPathMigrated={props.relocateOutputPath} />;
  }

  if (props.section === "home") {
    if (props.activeWork) {
      const activeWork = props.activeWork;
      return <ChatView key={activeWork.id} repo={activeWork} workMode={props.workMode}
      settings={settings} update={settingsStore.update} presets={settingsStore}
      setCover={props.setCover} setGeneratedCover={props.setGeneratedCover}
      onBranch={async (binding, messages, isLatest) => {
        const parentId = activeWork.parentId;
        if (!parentId) return;
        try {
          const result = await createScenarioBranch({
            outputDir: settings.outputDir, sourceRepoId: activeWork.id, parentId,
            binding, messages, isLatest,
          }, {
            saveMessages: saveSnapshot,
            listSnapshots: listScenarioSnapshots,
            createSnapshot: createScenarioSnapshot,
            forkSnapshot: forkScenarioSnapshot,
            addBranch: props.addBranch,
            persistMessages: (repoId, value) => {
              try { localStorage.setItem(`laf_chat_${repoId}`, JSON.stringify(value)); } catch { /* 超额忽略 */ }
            },
            openWork: props.onOpenWork,
            newId: () => crypto.randomUUID(),
          });
          if (result.status === "missing_snapshot") {
            window.alert("该历史回合没有完整世界状态快照，已拒绝创建状态错位的分支。");
          }
        } catch (error) {
          window.alert(`完整分支创建失败：${error instanceof Error ? error.message : String(error)}`);
        }
      }} />;
    }
    if (props.selectedRepo) {
      const selectedRepo = props.selectedRepo;
      return <RepoDetailView repo={selectedRepo} children={props.childrenOf(selectedRepo.id)}
        coverOf={props.coverOf} settings={settings} onBack={props.onClearRepo}
        onOpen={(work) => props.onOpenWork(selectedRepo.id, work.id)}
        onRename={props.onRename} onBind={props.onBind} onDelete={props.onDelete}
        onNewSub={() => props.onNewSub(selectedRepo.id)} />;
    }
    return <HomeRecentWorks works={props.recentWorks} parents={props.topRepos}
      coverOf={props.coverOf} settings={settings} childrenOf={props.childrenOf}
      onOpen={(work) => work.parentId && props.onOpenWork(work.parentId, work.id)}
      onRename={props.onRename} onBind={props.onBind} onDelete={props.onDelete} />;
  }

  if (props.section === "assets") {
    if (props.subView === "works") {
      const browseRepo = props.browseRepoId
        ? props.childrenOf(undefined).find((repo) => repo.id === props.browseRepoId)
        : null;
      if (browseRepo) {
        return <RepoDetailView repo={browseRepo} children={props.childrenOf(browseRepo.id)}
          coverOf={props.coverOf} settings={settings} onBack={() => props.setBrowseRepoId(null)}
          onOpen={(repo) => repo.parentId
            ? props.onOpenWork(repo.parentId, repo.id)
            : props.setBrowseRepoId(repo.id)}
          onRename={props.onRename} onBind={props.onBind} onDelete={props.onDelete}
          onNewSub={() => props.onNewSub(browseRepo.id)} />;
      }
      return <ReposView repos={props.childrenOf(undefined)} title="作品仓库"
        coverOf={props.coverOf} onOpen={(repo) => props.setBrowseRepoId(repo.id)}
        onRename={props.onRename} onBind={props.onBind} onDelete={props.onDelete}
        onNew={props.onNewRepo} childrenOf={props.childrenOf} settings={settings} />;
    }
    if (props.subView === "generations") return <AssetsView onSendToChat={() => {}} />;
    if (props.subView === "character-cards") {
      const persona = activeUserPersona(settings);
      return <CharacterCards characterDir={settings.characterDir} outputDir={settings.outputDir}
        worldbookDir={settings.worldbookDir} persona={{ name: persona.name, content: persona.content }}
        onOpenCard={(cardName) => {
          const { parentId, childId } = props.addCardWork(cardName);
          props.onOpenWork(parentId, childId);
        }} />;
    }
    if (props.subView === "worldbook") {
      return <WorldBook characterDir={settings.characterDir} worldbookDir={settings.worldbookDir} />;
    }
    return <SectionPlaceholder section={props.section} subView={props.subView} />;
  }

  if (props.section === "workflows") {
    if (props.subView === "templates") return <WorkflowTemplates settings={settings} />;
    if (props.subView === "ai-build") return <AIBuildView onInstallNode={props.setMarketSearch} />;
    if (props.subView === "node-index") return <NodeIndexView />;
  }
  if (props.section === "system") {
    if (props.subView === "models") return <ModelDownload settings={settings} />;
    if (props.subView === "lora-data") return <LoraTriggersTab />;
    if (props.subView === "node-manager") {
      return <NodeManagerView initialSearch={props.marketSearch}
        onSearchConsumed={() => props.setMarketSearch("")} />;
    }
    if (props.subView === "tools") return <ToolsView repoId={props.activeWork?.id || "home"} />;
  }
  return <SectionPlaceholder section={props.section} subView={props.subView} />;
}
