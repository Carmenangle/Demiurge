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
  createdAt: number;
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

  // 新建：重名则拒绝，返回 false
  const addRepo = (name: string, parentId?: string): boolean => {
    if (!nameAvailable(name, parentId)) return false;
    setRepos((prev) => [
      ...prev,
      { id: crypto.randomUUID(), name, parentId, createdAt: Date.now() },
    ]);
    return true;
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

  // 手动选择旧图作为封面时不能改变“最新生成图”排序时间。
  const setCover = (id: string, cover: string) => {
    setRepos((prev) => replaceRepoCover(prev, id, cover));
  };

  // 只有真实生成结果落盘时更新生成图时间。
  const setGeneratedCover = (id: string, cover: string) => {
    setRepos((prev) => recordGeneratedRepoCover(prev, id, cover, Date.now()));
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
    repos, addRepo, renameRepo, setCover, setGeneratedCover, relocateOutputPath,
    coverOf, deleteRepo, childrenOf,
  };
}
