// 「上次选中的仓库/作品」的本地 + 后端双份记忆。
//
// 本地 localStorage 只在同一个 WebView2 数据目录（或同一浏览器）里有效；桌面壳在
// 受限上下文下会换用别的数据目录，于是每次启动都回到选仓库选作品的页面。后端的
// user_state.selection 是跨目录/跨浏览器的真源，localStorage 只是先手兜底。
//
// 纯逻辑：解析、较新者胜、按真实仓库表校正。存储与网络都由调用方完成。

export interface LastSelection {
  repoId: string;
  workId: string;
  savedAt: number;
}

/** 校正只需要仓库表的最小子集，避免 lib 反向依赖 stores。 */
export interface RepoRef {
  id: string;
  parentId?: string;
}

export interface SelectionStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export const LAST_REPO_KEY = "laf_current_repo";
export const LAST_WORK_KEY = "laf_current_work";
export const LAST_SAVED_AT_KEY = "laf_current_saved_at";

export function emptySelection(): LastSelection {
  return { repoId: "", workId: "", savedAt: 0 };
}

/** 宽松解析后端字段：结构不对返回 null；空 id 但带时间戳=用户主动清空，必须保留。 */
export function parseSelection(raw: unknown): LastSelection | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const value = raw as Record<string, unknown>;
  const repoId = typeof value.repoId === "string" ? value.repoId.trim() : "";
  const workId = typeof value.workId === "string" ? value.workId.trim() : "";
  const savedAtRaw = Number(value.savedAt);
  const savedAt = Number.isFinite(savedAtRaw) && savedAtRaw > 0 ? savedAtRaw : 0;
  return { repoId, workId, savedAt };
}

export function readLocalSelection(
  storage: SelectionStorage | null = browserLocalStorage(),
): LastSelection {
  if (!storage) return emptySelection();
  try {
    const savedAt = Number(storage.getItem(LAST_SAVED_AT_KEY) || 0);
    return {
      repoId: storage.getItem(LAST_REPO_KEY) || "",
      workId: storage.getItem(LAST_WORK_KEY) || "",
      savedAt: Number.isFinite(savedAt) && savedAt > 0 ? savedAt : 0,
    };
  } catch {
    return emptySelection();
  }
}

export function writeLocalSelection(
  selection: LastSelection,
  storage: SelectionStorage | null = browserLocalStorage(),
): void {
  if (!storage) return;
  try {
    if (selection.repoId) storage.setItem(LAST_REPO_KEY, selection.repoId);
    else storage.removeItem(LAST_REPO_KEY);
    if (selection.workId) storage.setItem(LAST_WORK_KEY, selection.workId);
    else storage.removeItem(LAST_WORK_KEY);
    if (selection.savedAt > 0) storage.setItem(LAST_SAVED_AT_KEY, String(selection.savedAt));
    else storage.removeItem(LAST_SAVED_AT_KEY);
  } catch {
    /* 存储不可用：后端 selection 仍能兜底 */
  }
}

/** 本地与后端都有记录时较新者胜；本地为空直接采用后端；无时间戳的本地按 0 计。 */
export function pickSelection(
  local: LastSelection,
  remote: LastSelection | null | undefined,
): LastSelection {
  if (!remote) return local;
  if (!local.repoId && !local.workId) return remote;
  return remote.savedAt > local.savedAt ? remote : local;
}

/**
 * 按真实仓库表校正选择，防止「上次选的作品已被删除」把界面卡在空页。
 * 返回 null＝没有可用选择（回首页）；
 * 返回 workId="" 但 repoId 有效＝只恢复到大仓库（作品列表页）。
 */
export function reconcileSelection(
  selection: LastSelection,
  repos: readonly RepoRef[],
): LastSelection | null {
  const byId = new Map(repos.map((repo) => [repo.id, repo]));
  let repoId = selection.repoId.trim();
  let workId = selection.workId.trim();

  let repo = repoId ? byId.get(repoId) : undefined;
  if (repo?.parentId) {
    // 旧数据把子仓库记进了大仓库位：上提到父仓库，子仓库保留为当前作品。
    if (!workId) workId = repo.id;
    repoId = repo.parentId;
    repo = byId.get(repoId);
  }
  if (!repo && workId) {
    const work = byId.get(workId);
    if (work?.parentId) {
      const parent = byId.get(work.parentId);
      if (parent) {
        repoId = parent.id;
        repo = parent;
      }
    }
  }
  if (!repo) return null;

  const work = workId ? byId.get(workId) : undefined;
  const resolvedWorkId = work && work.parentId === repo.id ? work.id : "";
  return { ...selection, repoId: repo.id, workId: resolvedWorkId };
}

function browserLocalStorage(): SelectionStorage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}
