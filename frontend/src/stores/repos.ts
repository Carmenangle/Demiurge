import { useEffect, useState } from "react";
import { getUserState, renameRepoFolder } from "../api/userState";
import { pushRepos } from "../lib/userStateSync";
import { relocateLocalViewUrl } from "../lib/outputPathMigration";
import {
  orderReposByLatestGeneration,
  recordGeneratedRepoCover,
  replaceRepoCover,
} from "../lib/repoOrdering";

export interface Repo {
  id: string;
  name: string;
  parentId?: string; // 为空=顶层仓库；有值=某仓库下的小仓库
  cover?: string; // 该仓库最新生成图片
  coverAt?: number; // 封面更新时间戳（用于父仓库取子仓库里最新的一张）
  lastUsedAt?: number; // 最近进入该作品的时间；首页最近作品与卡片展示的唯一来源
  createdAt: number;
  cardName?: string; // 绑定的角色卡名（=character_store 文件夹名）；有值=剧情扮演作品
  cardNames?: string[]; // 绑定的全部角色卡；cardName 保留为开场卡兼容别名
  openingCardName?: string; // 首次空会话使用哪张卡的 first_mes
  worldbookName?: string; // 绑定的独立世界书名（worldbookDir 下的 .json 名，不含扩展名）；空=不绑独立书
  personaId?: string; // 绑定的用户人设档 id（settings.userPersonas）；空=用全局选中档
  presetName?: string; // 绑定的偏置预设名（presetDir 下的 .json 名，不含扩展名）；空=用全局选中档
}

// 一个仓库的有效绑定：自身字段优先，缺则继承父仓库，皆空则回退全局（由调用方处理全局回退）。
export interface RepoBinding {
  cardName: string;
  cardNames: string[];
  openingCardName: string;
  worldbookName: string;
  personaId: string;
  presetName: string;
}

const KEY = "laf_repos";

function load(): Repo[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "[]");
  } catch {
    return [];
  }
}

function save(repos: Repo[]) {
  localStorage.setItem(KEY, JSON.stringify(repos));
}

function normalizedCards(repo: Pick<Repo, "cardName" | "cardNames" | "openingCardName">): {
  cardNames: string[]; openingCardName: string;
} {
  const cardNames = [...new Set((repo.cardNames || []).map((name) => name.trim()).filter(Boolean))];
  if (repo.cardName?.trim() && !cardNames.includes(repo.cardName.trim())) cardNames.unshift(repo.cardName.trim());
  const openingCardName = cardNames.includes(repo.openingCardName || "")
    ? repo.openingCardName! : (cardNames.includes(repo.cardName || "") ? repo.cardName! : cardNames[0] || "");
  return { cardNames, openingCardName };
}

// 规范化绑定（幂等）：①旧数据 cardName 误挂子仓库→上提到父；②子仓库三项绑定为空则从父下沉一份到自身字段，
// 使子仓库 UI 直接显示「已绑定」（快照式：之后父改绑定不回灌，子可单独修改）。无变化则返回 null（不触发写回）。
function normalizeBindings(repos: Repo[]): Repo[] | null {
  const liftCard = new Map<string, string>(); // parentId -> cardName（从子上提）
  for (const r of repos) {
    if (r.parentId && r.cardName) {
      const parent = repos.find((p) => p.id === r.parentId);
      if (parent && !parent.cardName) liftCard.set(r.parentId, r.cardName);
    }
  }
  let changed = false;
  const withParents = repos.map((r) => {
    const lifted = liftCard.has(r.id) && !r.cardName ? liftCard.get(r.id) : r.cardName;
    const current = normalizedCards({ ...r, cardName: lifted });
    if (lifted !== r.cardName || JSON.stringify(current.cardNames) !== JSON.stringify(r.cardNames || [])
      || current.openingCardName !== (r.openingCardName || "")) {
      changed = true;
      return { ...r, cardName: current.openingCardName || undefined,
        cardNames: current.cardNames.length ? current.cardNames : undefined,
        openingCardName: current.openingCardName || undefined };
    }
    return r;
  });
  const next = withParents.map((r) => {
    if (!r.parentId) return r;
    const parent = withParents.find((p) => p.id === r.parentId);
    if (!parent) return r;
    const patch: Partial<Repo> = {};
    const ownCards = normalizedCards(r);
    const parentCards = normalizedCards(parent);
    if (!ownCards.cardNames.length && parentCards.cardNames.length) {
      patch.cardNames = parentCards.cardNames;
      patch.openingCardName = parentCards.openingCardName;
      patch.cardName = parentCards.openingCardName;
    }
    if (!r.worldbookName && parent.worldbookName) patch.worldbookName = parent.worldbookName;
    if (!r.personaId && parent.personaId) patch.personaId = parent.personaId;
    if (!r.presetName && parent.presetName) patch.presetName = parent.presetName;
    if (Object.keys(patch).length) { changed = true; return { ...r, ...patch }; }
    return r;
  });
  return changed ? next : null;
}

export function useRepos() {
  const [repos, setRepos] = useState<Repo[]>(load);
  const [hydrated, setHydrated] = useState(false); // 后端为准：拉取回填完成前，不把本地值回写后端

  // 启动时拉后端存档，有数据则以后端为准覆盖本地（跨浏览器/换机恢复）
  useEffect(() => {
    let alive = true;
    getUserState()
      .then((s) => {
        if (alive && s.repos) {
          setRepos(s.repos);
          save(s.repos);
        }
      })
      .catch(() => { /* 后端离线：沿用 localStorage */ })
      .finally(() => { if (alive) setHydrated(true); });
    return () => { alive = false; };
  }, []);

  // 惰性迁移：①旧数据 cardName 上提到父；②子仓库空绑定从父下沉一份（快照式）。幂等，回填完成后跑到稳定。
  useEffect(() => {
    if (!hydrated) return;
    const fixed = normalizeBindings(repos);
    if (fixed) setRepos(fixed);
  }, [hydrated, repos]);

  useEffect(() => {
    save(repos);
    if (hydrated) pushRepos(repos); // 回填完成后，本地变更（及升级时的本地存量）镜像到后端
  }, [repos, hydrated]);

  // 同层级重名校验（同一 parentId 下不允许同名）。返回 true=可用
  const nameAvailable = (name: string, parentId?: string, excludeId?: string) => {
    const n = name.trim();
    return !repos.some(
      (r) => r.id !== excludeId && r.parentId === parentId && r.name.trim() === n,
    );
  };

  // 子仓库（存档）命名：SAVE01、SAVE02…按 parentId 下已有最大 SAVE 索引往后推，便于分支递推按索引号。
  // 只认 SAVE\d+ 形式（重命名过的自定义名不参与计数），最大索引 +1，两位补零。
  const nextSaveName = (parentId: string): string => {
    let max = 0;
    for (const r of repos) {
      if (r.parentId !== parentId) continue;
      const m = /^SAVE(\d+)$/.exec(r.name.trim());
      if (m) max = Math.max(max, parseInt(m[1], 10));
    }
    return `SAVE${String(max + 1).padStart(2, "0")}`;
  };

  // 新建：重名则拒绝，返回 false
  const addRepo = (name: string, parentId?: string): boolean => {
    if (!nameAvailable(name, parentId)) return false;
    setRepos((prev) => [
      ...prev,
      { id: crypto.randomUUID(), name, parentId, createdAt: Date.now() },
    ]);
    return true;
  };

  // 卡即作品：导入角色卡时建「大仓库(卡名) + 子仓库(对话记录)」，对话线挂在子仓库上。
  // 已存在同名大仓库则复用；缺子仓库则补建。返回 { parentId, childId }，childId 用于进对话。
  const addCardWork = (cardName: string): { parentId: string; childId: string } => {
    const name = cardName.trim();
    const parent = repos.find((r) => !r.parentId && r.name.trim() === name);
    if (parent) {
      // 复用大仓库：cardName 绑在父仓库上，子仓库靠 resolveBinding 继承。补建父绑定（兼容旧数据）。
      const child = repos.find((r) => r.parentId === parent.id);
      if (child) {
        if (!parent.cardName) {
          setRepos((prev) => prev.map((r) => (r.id === parent.id ? {
            ...r, cardName, cardNames: [cardName], openingCardName: cardName,
          } : r)));
        }
        return { parentId: parent.id, childId: child.id };
      }
      const childId = crypto.randomUUID();
      const saveName = nextSaveName(parent.id);
      // 新子仓库复制父当前绑定到自身字段（快照），UI 直接显示已绑定。
      setRepos((prev) => [
        ...prev.map((r) => (r.id === parent.id ? {
          ...r, cardName, cardNames: [cardName], openingCardName: cardName,
        } : r)),
        {
          id: childId, name: saveName, parentId: parent.id, cardName,
          cardNames: [cardName], openingCardName: cardName,
          worldbookName: parent.worldbookName, personaId: parent.personaId,
          presetName: parent.presetName, createdAt: Date.now(),
        },
      ]);
      return { parentId: parent.id, childId };
    }
    const parentId = crypto.randomUUID();
    const childId = crypto.randomUUID();
    // cardName 绑在父仓库；SAVE01 创建时即复制一份父绑定到自身（此刻父仅有 cardName），UI 直接显示已绑定。
    setRepos((prev) => [
      ...prev,
      { id: parentId, name: cardName, cardName, cardNames: [cardName], openingCardName: cardName, createdAt: Date.now() },
      { id: childId, name: "SAVE01", parentId, cardName, cardNames: [cardName], openingCardName: cardName, createdAt: Date.now() },
    ]);
    return { parentId, childId };
  };

  // 分支：在 parentId 下建一个新兄弟子仓库，返回新子仓库 id。名称按当前最大 SAVE 索引递推（SAVE02…）。
  // 复制父仓库当前绑定（卡/世界书/人设）到新子仓库自身字段（快照式，之后可单独改）。传入 cardName 优先。
  const addBranch = (parentId: string, binding?: Partial<RepoBinding>, branchId?: string): string => {
    const id = branchId || crypto.randomUUID();
    const name = nextSaveName(parentId);
    const parent = repos.find((r) => r.id === parentId);
    const parentCards = parent ? normalizedCards(parent) : { cardNames: [], openingCardName: "" };
    const branchCards = binding?.cardNames?.length ? binding.cardNames : parentCards.cardNames;
    const openingCardName = branchCards.includes(binding?.openingCardName || "")
      ? binding!.openingCardName! : parentCards.openingCardName || branchCards[0] || "";
    setRepos((prev) => [...prev, {
      id, name, parentId, createdAt: Date.now(),
      cardName: openingCardName || undefined,
      cardNames: branchCards.length ? branchCards : undefined,
      openingCardName: openingCardName || undefined,
      worldbookName: binding?.worldbookName || parent?.worldbookName,
      personaId: binding?.personaId || parent?.personaId,
      presetName: binding?.presetName || parent?.presetName,
    }]);
    return id;
  };

  // 改名：重名则拒绝返回 false；成功则同步后端重命名文件夹+重写图片路径
  const renameRepo = (id: string, name: string): boolean => {
    const target = repos.find((r) => r.id === id);
    if (!target) return false;
    if (!nameAvailable(name, target.parentId, id)) return false;
    const oldName = target.name;
    setRepos((prev) => prev.map((r) => (r.id === id ? { ...r, name } : r)));
    try {
      const settings = JSON.parse(localStorage.getItem("laf_settings") || "{}");
      const output_dir = settings.outputDir || "";
      if (output_dir) {
        renameRepoFolder({ repo_id: id, old_name: oldName, new_name: name, output_dir })
          .catch(() => { /* 后端离线：文件夹下次落盘会用新名，旧图路径可能失效 */ });
      }
    } catch { /* ignore */ }
    return true;
  };

  // 绑定角色卡/独立世界书/用户人设。patch 里字段=新值；传 "" 或 undefined 表示解绑该项。
  // 三样独立，互不影响；大仓库、小仓库都可绑。
  const bindRepo = (id: string, patch: Partial<RepoBinding>) => {
    setRepos((prev) => prev.map((r) => {
      if (r.id !== id) return r;
      const next = { ...r };
      if ("cardNames" in patch || "openingCardName" in patch || "cardName" in patch) {
        const requested = "cardNames" in patch ? patch.cardNames || [] : normalizedCards(next).cardNames;
        const cardNames = [...new Set(requested.map((name) => name.trim()).filter(Boolean))];
        const requestedOpening = patch.openingCardName || patch.cardName || "";
        const openingCardName = cardNames.includes(requestedOpening) ? requestedOpening : cardNames[0] || "";
        next.cardNames = cardNames.length ? cardNames : undefined;
        next.openingCardName = openingCardName || undefined;
        next.cardName = openingCardName || undefined;
      }
      if ("worldbookName" in patch) next.worldbookName = patch.worldbookName || undefined;
      if ("personaId" in patch) next.personaId = patch.personaId || undefined;
      if ("presetName" in patch) next.presetName = patch.presetName || undefined;
      return next;
    }));
  };

  // 解析某仓库的有效绑定：自身字段优先，缺则继承父仓库字段。全局回退（人设/世界书目录）交给调用方。
  const resolveBinding = (repo: Repo): RepoBinding => {
    const parent = repo.parentId ? repos.find((r) => r.id === repo.parentId) : undefined;
    const ownCards = normalizedCards(repo);
    const inheritedCards = ownCards.cardNames.length || !parent ? ownCards : normalizedCards(parent);
    return {
      cardName: inheritedCards.openingCardName,
      cardNames: inheritedCards.cardNames,
      openingCardName: inheritedCards.openingCardName,
      worldbookName: repo.worldbookName || parent?.worldbookName || "",
      personaId: repo.personaId || parent?.personaId || "",
      presetName: repo.presetName || parent?.presetName || "",
    };
  };

  // 手动选择旧图作为封面时不能改变“最新生成图”排序时间。
  const setCover = (id: string, cover: string) => {
    setRepos((prev) => replaceRepoCover(prev, id, cover));
  };

  // 只有真实生成结果落盘时更新生成图时间。
  const setGeneratedCover = (id: string, cover: string) => {
    setRepos((prev) => recordGeneratedRepoCover(prev, id, cover, Date.now()));
  };

  const touchRepo = (id: string) => {
    const now = Date.now();
    setRepos((prev) => {
      const target = prev.find((repo) => repo.id === id);
      return prev.map((repo) => repo.id === id || (target?.parentId && repo.id === target.parentId)
        ? { ...repo, lastUsedAt: now }
        : repo);
    });
  };

  const relocateOutputPath = (oldDir: string, newDir: string) => {
    setRepos((prev) => prev.map((repo) => ({
      ...repo,
      cover: relocateLocalViewUrl(repo.cover, oldDir, newDir),
    })));
  };

  // 删除仓库时一并删除其下所有小仓库
  const deleteRepo = (id: string) => {
    setRepos((prev) => prev.filter((r) => r.id !== id && r.parentId !== id));
  };

  const childrenOf = (parentId?: string) => orderReposByLatestGeneration(
    repos.filter((r) => r.parentId === parentId),
    repos,
  );

  // 取仓库展示封面：小仓库用自身；顶层仓库用其子仓库里 coverAt 最新的一张
  const coverOf = (r: Repo): string | undefined => {
    if (r.parentId) return r.cover; // 小仓库：自身封面
    const kids = repos.filter((x) => x.parentId === r.id && x.cover);
    if (kids.length === 0) return r.cover; // 没有带图的子仓库则用自身（通常为空）
    kids.sort((a, b) => (b.coverAt || 0) - (a.coverAt || 0));
    return kids[0].cover;
  };

  return {
    repos, addRepo, addCardWork, addBranch, renameRepo, bindRepo, resolveBinding,
    setCover, setGeneratedCover, touchRepo, relocateOutputPath,
    coverOf, deleteRepo, childrenOf,
  };
}
