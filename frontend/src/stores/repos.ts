import { useEffect, useRef, useState } from "react";
import { getUserState, renameRepoFolder } from "../api/userState";
import { pushRepos, USER_STATE_RECONNECT_EVENT, clearUserStateFallback, markUserStateFallback } from "../lib/userStateSync";
import { remoteActiveOnce } from "../lib/mobileProfile";
import { relocateLocalViewUrl } from "../lib/outputPathMigration";
import {
  orderReposByLatestGeneration,
  recordGeneratedRepoCover,
  replaceRepoCover,
} from "../lib/repoOrdering";
import type { CharacterRosterOverrides } from "../lib/characterRoster";

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
  /** 用户显式解除角色卡绑定（2026-11-09 修复）：为真时不再从父仓库继承角色卡。 */
  cardsUnbound?: boolean;
  /** 角色卡来自父仓库快照（父改/父解绑会同步）；子仓库自己绑卡后为 false。 */
  cardsInherited?: boolean;
  /** 独立世界书来自父仓库快照（父改会同步）；子仓库自己选书后为 false。 */
  worldbookInherited?: boolean;
  /** 用户人设来自父仓库快照（父改会同步）；子仓库自己选人设后为 false。 */
  personaInherited?: boolean;
  /** 偏置预设来自父仓库快照（父改会同步）；子仓库自己选预设后为 false。 */
  presetInherited?: boolean;
  openingCardName?: string; // 首次空会话使用哪张卡的 first_mes
  worldbookName?: string; // 绑定的独立世界书名（worldbookDir 下的 .json 名，不含扩展名）；空=不绑独立书
  personaId?: string; // 绑定的用户人设档 id（settings.userPersonas）；空=用全局选中档
  presetName?: string; // 绑定的偏置预设名（presetDir 下的 .json 名，不含扩展名）；空=用全局选中档
  /** 聊天/多元数据插入的角色名册覆盖（2026-11-10）：合集卡从世界书条目识别角色后的手动增删/改名/排序。 */
  characterRoster?: CharacterRosterOverrides;
  /** 聊天头像覆盖（角色名 → 图片 URL）：聊天自拍自动更新时写入，空白回退角色卡/定妆底图。 */
  characterAvatars?: Record<string, string>;
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

/** 后端明确拒绝改名（HTTP 错误体带 detail）⇒ 返回可读原因；网络层失败（后端离线）⇒ null。
 *  改名是乐观更新：离线时保留新名字等下次落盘用新名；后端明确拒绝必须回滚并提示用户。 */
export function folderRenameRefusal(error: unknown): string | null {
  if (error instanceof TypeError) return null;
  const message = error instanceof Error ? error.message : String(error);
  if (/failed to fetch|networkerror|load failed|network request failed|fetch failed/i.test(message)) {
    return null;
  }
  return message || "后端拒绝改名，但未返回原因";
}

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

// 规范化绑定（幂等）：只整理本仓库的 cardName/cardNames 一致性，**不再做父子传播**。
// 2026-11-09 修复：旧实现把子仓库卡上提到父、再把父卡下沉给所有子仓库，导致
// 「给一个子作品绑卡 → 整个父仓库和所有子作品都被写死」以及「取消绑定又被继承回来」。
// 父子继承只在 resolveBinding 运行时发生，且显式 cardsUnbound 优先级最高。
export function normalizeBindings(repos: Repo[]): Repo[] | null {
  let changed = false;
  const withParents = repos.map((r) => {
    if (r.cardsUnbound) {
      if (r.cardName || r.cardNames?.length || r.openingCardName) {
        changed = true;
        return { ...r, cardName: undefined, cardNames: undefined, openingCardName: undefined };
      }
      return r;
    }
    const current = normalizedCards(r);
    if (JSON.stringify(current.cardNames) !== JSON.stringify(r.cardNames || [])
      || current.openingCardName !== (r.openingCardName || "")
      || current.openingCardName !== (r.cardName || "")) {
      changed = true;
      return {
        ...r,
        cardName: current.openingCardName || undefined,
        cardNames: current.cardNames.length ? current.cardNames : undefined,
        openingCardName: current.openingCardName || undefined,
      };
    }
    return r;
  });
  // 父仓库绑定作为默认值向子仓库快照下沉；子仓库自己改过/显式解绑时不被覆盖。
  // 带 inherited 标记的子仓库在父仓库改绑或解绑时会跟着更新，保证「父改子跟着变」。
  const next = withParents.map((r) => {
    if (!r.parentId) return r;
    const parent = withParents.find((p) => p.id === r.parentId);
    if (!parent) return r;
    const patch: Partial<Repo> = {};
    const ownCards = normalizedCards(r);
    const parentCards = normalizedCards(parent);
    if (r.cardsUnbound) {
      // 显式解绑：卡字段已由第一轮清空，这里只保证标记不被继承覆盖。
    } else if (r.cardsInherited) {
      const nextCards = parentCards.cardNames.length ? parentCards : { cardNames: [], openingCardName: "" };
      if (JSON.stringify(ownCards.cardNames) !== JSON.stringify(nextCards.cardNames)
        || ownCards.openingCardName !== nextCards.openingCardName) {
        patch.cardNames = nextCards.cardNames.length ? nextCards.cardNames : undefined;
        patch.openingCardName = nextCards.openingCardName || undefined;
        patch.cardName = nextCards.openingCardName || undefined;
      }
    } else if (!ownCards.cardNames.length && parentCards.cardNames.length) {
      patch.cardNames = parentCards.cardNames;
      patch.openingCardName = parentCards.openingCardName;
      patch.cardName = parentCards.openingCardName;
      patch.cardsInherited = true;
    } else if (ownCards.cardNames.length && parentCards.cardNames.length
      && JSON.stringify(ownCards.cardNames) === JSON.stringify(parentCards.cardNames)
      && ownCards.openingCardName === parentCards.openingCardName) {
      // 旧版快照复制出来的“与父完全相同”按继承处理，父改/父解绑时能继续跟随。
      patch.cardsInherited = true;
    }
    if (r.worldbookInherited) {
      if ((r.worldbookName || "") !== (parent.worldbookName || "")) {
        patch.worldbookName = parent.worldbookName || undefined;
      }
    } else if (!r.worldbookName && parent.worldbookName) {
      patch.worldbookName = parent.worldbookName;
      patch.worldbookInherited = true;
    } else if (r.worldbookName && r.worldbookName === parent.worldbookName) {
      patch.worldbookInherited = true;
    }
    if (r.personaInherited) {
      if ((r.personaId || "") !== (parent.personaId || "")) {
        patch.personaId = parent.personaId || undefined;
      }
    } else if (!r.personaId && parent.personaId) {
      patch.personaId = parent.personaId;
      patch.personaInherited = true;
    } else if (r.personaId && r.personaId === parent.personaId) {
      patch.personaInherited = true;
    }
    if (r.presetInherited) {
      if ((r.presetName || "") !== (parent.presetName || "")) {
        patch.presetName = parent.presetName || undefined;
      }
    } else if (!r.presetName && parent.presetName) {
      patch.presetName = parent.presetName;
      patch.presetInherited = true;
    } else if (r.presetName && r.presetName === parent.presetName) {
      patch.presetInherited = true;
    }
    if (Object.keys(patch).length) { changed = true; return { ...r, ...patch }; }
    return r;
  });
  return changed ? next : null;
}

// 纯函数：应用一次绑定 patch。空卡选择＝显式解绑（cardsUnbound=true），非空＝重新绑定。
export function applyBindingPatch(repo: Repo, patch: Partial<RepoBinding>): Repo {
  const next = { ...repo };
  if ("cardNames" in patch || "openingCardName" in patch || "cardName" in patch) {
    const requested = "cardNames" in patch ? patch.cardNames || [] : normalizedCards(next).cardNames;
    const cardNames = [...new Set(requested.map((name) => name.trim()).filter(Boolean))];
    const requestedOpening = patch.openingCardName || patch.cardName || "";
    const openingCardName = cardNames.includes(requestedOpening) ? requestedOpening : cardNames[0] || "";
    const before = normalizedCards(next);
    const sameSelection = JSON.stringify(cardNames) === JSON.stringify(before.cardNames)
      && openingCardName === before.openingCardName;
    next.cardNames = cardNames.length ? cardNames : undefined;
    next.openingCardName = openingCardName || undefined;
    next.cardName = openingCardName || undefined;
    // 选择没变＝保留原有继承/解绑状态（空表单原样保存不会误变成显式解绑）；
    // 选择变了才重新判定：清空＝显式解绑，选中＝子仓库自己绑卡。
    next.cardsUnbound = sameSelection ? Boolean(repo.cardsUnbound) : cardNames.length === 0;
    next.cardsInherited = sameSelection ? Boolean(repo.cardsInherited) : false;
  }
  if ("worldbookName" in patch) {
    const sameBook = (patch.worldbookName || "") === (repo.worldbookName || "");
    next.worldbookName = patch.worldbookName || undefined;
    next.worldbookInherited = sameBook ? Boolean(repo.worldbookInherited) : false;
  }
  if ("personaId" in patch) {
    const samePersona = (patch.personaId || "") === (repo.personaId || "");
    next.personaId = patch.personaId || undefined;
    next.personaInherited = samePersona ? Boolean(repo.personaInherited) : false;
  }
  if ("presetName" in patch) {
    const samePreset = (patch.presetName || "") === (repo.presetName || "");
    next.presetName = patch.presetName || undefined;
    next.presetInherited = samePreset ? Boolean(repo.presetInherited) : false;
  }
  return next;
}

// 纯函数：解析某仓库的有效绑定（自身优先，缺则继承父仓库；显式解绑不继承）。
export function resolveRepoBinding(repo: Repo, repos: readonly Repo[]): RepoBinding {
  const parent = repo.parentId ? repos.find((r) => r.id === repo.parentId) : undefined;
  const ownCards = repo.cardsUnbound
    ? { cardNames: [], openingCardName: "" }
    : normalizedCards(repo);
  const inheritedCards = repo.cardsInherited && parent
    ? normalizedCards(parent)
    : (repo.cardsUnbound || ownCards.cardNames.length || !parent
      ? ownCards
      : normalizedCards(parent));
  return {
    cardName: inheritedCards.openingCardName,
    cardNames: inheritedCards.cardNames,
    openingCardName: inheritedCards.openingCardName,
    worldbookName: repo.worldbookInherited && parent
      ? parent.worldbookName || ""
      : repo.worldbookName || parent?.worldbookName || "",
    personaId: repo.personaInherited && parent
      ? parent.personaId || ""
      : repo.personaId || parent?.personaId || "",
    presetName: repo.presetInherited && parent
      ? parent.presetName || ""
      : repo.presetName || parent?.presetName || "",
  };
}

export function useRepos() {
  const [repos, setRepos] = useState<Repo[]>(load);
  const reposRef = useRef(repos);
  reposRef.current = repos;
  const [hydrated, setHydrated] = useState(false); // 后端为准：拉取回填完成前，不把本地值回写后端

  // 启动时拉后端存档，有数据则以后端为准覆盖本地（跨浏览器/换机恢复）。
  // 联机同库开着却连不上电脑时保持「未水合」：界面跑本机缓存，但绝不把这份快照回写
  // 电脑（后端 set_state 整体替换 repos，会抹掉电脑的作品列表）。恢复后由
  // MobileOnlineWatch 广播 USER_STATE_RECONNECT_EVENT，这里重新水合一次。
  useEffect(() => {
    let alive = true;
    const hydrate = async () => {
      const remote = await remoteActiveOnce();
      if (!alive) return;
      try {
        const s = await getUserState();
        if (!alive) return;
        if (s.repos) {
          setRepos(s.repos);
          save(s.repos);
        }
        clearUserStateFallback();
        setHydrated(true);
      } catch {
        if (!alive) return;
        if (remote) { markUserStateFallback(); return; } // 电脑的真值还没拿到，禁止回写
        setHydrated(true); // 电脑端/手机本机模式：后端离线沿用 localStorage（原行为）
      }
    };
    void hydrate();
    const onReconnect = () => { void hydrate(); };
    window.addEventListener(USER_STATE_RECONNECT_EVENT, onReconnect);
    return () => {
      alive = false;
      window.removeEventListener(USER_STATE_RECONNECT_EVENT, onReconnect);
    };
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

  // 改名：重名则拒绝返回 false；成功先乐观上屏，再同步后端重命名文件夹+重写图片路径。
  // 后端明确拒绝（归属冲突/目标已存在/搬运失败）⇒ 回滚显示名并回调原因；
  // 后端离线 ⇒ 保留乐观改名（文件夹下次落盘会用新名）。
  const renameRepo = (id: string, name: string, onFolderError?: (message: string) => void): boolean => {
    const target = repos.find((r) => r.id === id);
    if (!target) return false;
    if (!nameAvailable(name, target.parentId, id)) return false;
    const oldName = target.name;
    setRepos((prev) => prev.map((r) => (r.id === id ? { ...r, name } : r)));
    const rollback = (message: string) => {
      // 响应回来前用户可能又改了一次名：只回滚仍是本次名字的那一次，过时失败不弹错
      const stillCurrent = reposRef.current.some((r) => r.id === id && r.name === name);
      setRepos((prev) => prev.map((r) => (
        r.id === id && r.name === name ? { ...r, name: oldName } : r
      )));
      if (stillCurrent) onFolderError?.(message);
    };
    try {
      const settings = JSON.parse(localStorage.getItem("laf_settings") || "{}");
      const output_dir = settings.outputDir || "";
      if (output_dir) {
        renameRepoFolder({ repo_id: id, old_name: oldName, new_name: name, output_dir })
          .then((res) => { if (res.error) rollback(res.error); })
          .catch((error) => {
            const refusal = folderRenameRefusal(error);
            if (refusal) rollback(refusal);
          });
      }
    } catch { /* ignore */ }
    return true;
  };

  // 绑定角色卡/独立世界书/用户人设。patch 里字段=新值；传 "" 或 undefined 表示解绑该项。
  // 三样独立，互不影响；大仓库、小仓库都可绑。
  const bindRepo = (id: string, patch: Partial<RepoBinding>) => {
    setRepos((prev) => prev.map((r) => (r.id === id ? applyBindingPatch(r, patch) : r)));
  };

  // 解析某仓库的有效绑定：自身字段优先，缺则继承父仓库字段。全局回退（人设/世界书目录）交给调用方。
  const resolveBinding = (repo: Repo): RepoBinding => resolveRepoBinding(repo, repos);

  // 手动选择旧图作为封面时不能改变“最新生成图”排序时间。
  const setCover = (id: string, cover: string) => {
    setRepos((prev) => replaceRepoCover(prev, id, cover));
  };

  // 只有真实生成结果落盘时更新生成图时间；图片/视频都按「最新一条」成为封面。
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

  /** 通用仓库字段补丁（如 characterRoster 名册覆盖）；不触碰父子继承规则。 */
  const updateRepo = (id: string, patch: Partial<Repo>) => {
    setRepos((prev) => prev.map((repo) => (repo.id === id ? { ...repo, ...patch } : repo)));
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
    repos, hydrated, addRepo, addCardWork, addBranch, renameRepo, bindRepo, resolveBinding,
    setCover, setGeneratedCover, touchRepo, updateRepo, relocateOutputPath,
    coverOf, deleteRepo, childrenOf,
  };
}
