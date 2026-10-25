import { describe, expect, it } from "vitest";
import {
  LAST_REPO_KEY,
  LAST_SAVED_AT_KEY,
  LAST_WORK_KEY,
  parseSelection,
  pickSelection,
  readLocalSelection,
  reconcileSelection,
  writeLocalSelection,
  type SelectionStorage,
} from "./lastSelection";

class MemoryStorage implements SelectionStorage {
  private data = new Map<string, string>();
  getItem(key: string): string | null {
    return this.data.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.data.set(key, value);
  }
  removeItem(key: string): void {
    this.data.delete(key);
  }
}

describe("lastSelection 解析与本地读写", () => {
  it("宽松解析后端 selection：非法结构返回 null，空 id 但带时间戳表示主动清空", () => {
    expect(parseSelection(null)).toBeNull();
    expect(parseSelection("repo-1")).toBeNull();
    expect(parseSelection([])).toBeNull();
    expect(parseSelection({ repoId: " r1 ", workId: " w1 ", savedAt: 12 })).toEqual({
      repoId: "r1", workId: "w1", savedAt: 12,
    });
    expect(parseSelection({ repoId: "", workId: "", savedAt: 99 })).toEqual({
      repoId: "", workId: "", savedAt: 99,
    });
    expect(parseSelection({ repoId: "r1" })?.savedAt).toBe(0);
  });

  it("本地的仓库/作品/时间戳三键读写一致，清空时删键", () => {
    const storage = new MemoryStorage();
    writeLocalSelection({ repoId: "r1", workId: "w1", savedAt: 123 }, storage);
    expect(storage.getItem(LAST_REPO_KEY)).toBe("r1");
    expect(storage.getItem(LAST_WORK_KEY)).toBe("w1");
    expect(storage.getItem(LAST_SAVED_AT_KEY)).toBe("123");
    expect(readLocalSelection(storage)).toEqual({ repoId: "r1", workId: "w1", savedAt: 123 });

    writeLocalSelection({ repoId: "", workId: "", savedAt: 0 }, storage);
    expect(storage.getItem(LAST_REPO_KEY)).toBeNull();
    expect(storage.getItem(LAST_WORK_KEY)).toBeNull();
    expect(storage.getItem(LAST_SAVED_AT_KEY)).toBeNull();
    expect(readLocalSelection(storage)).toEqual({ repoId: "", workId: "", savedAt: 0 });
  });

  it("旧版本 localStorage 没有时间戳时按 0 计，仍可读出选择", () => {
    const storage = new MemoryStorage();
    storage.setItem(LAST_REPO_KEY, "legacy-repo");
    storage.setItem(LAST_WORK_KEY, "legacy-work");
    expect(readLocalSelection(storage)).toEqual({
      repoId: "legacy-repo", workId: "legacy-work", savedAt: 0,
    });
  });
});

describe("lastSelection 合并（较新者胜）", () => {
  it("本地为空采用后端；后端为空保留本地", () => {
    const remote = { repoId: "r1", workId: "w1", savedAt: 10 };
    expect(pickSelection({ repoId: "", workId: "", savedAt: 99 }, remote)).toEqual(remote);
    const local = { repoId: "r2", workId: "w2", savedAt: 10 };
    expect(pickSelection(local, null)).toEqual(local);
  });

  it("两侧都有记录时按 savedAt 取较新，相等取本地", () => {
    const local = { repoId: "r-local", workId: "w-local", savedAt: 100 };
    expect(pickSelection(local, { repoId: "r-remote", workId: "w-remote", savedAt: 99 }))
      .toEqual(local);
    expect(pickSelection(local, { repoId: "r-remote", workId: "w-remote", savedAt: 101 }))
      .toEqual({ repoId: "r-remote", workId: "w-remote", savedAt: 101 });
  });
});

describe("lastSelection 按真实仓库表校正", () => {
  const repos = [
    { id: "r1" },
    { id: "w1", parentId: "r1" },
    { id: "w2", parentId: "r1" },
    { id: "r2" },
    { id: "w3", parentId: "r2" },
  ];

  it("仓库与作品都存在时原样恢复", () => {
    expect(reconcileSelection({ repoId: "r1", workId: "w2", savedAt: 1 }, repos))
      .toEqual({ repoId: "r1", workId: "w2", savedAt: 1 });
  });

  it("作品被删除时只恢复到大仓库，不丢仓库选择", () => {
    expect(reconcileSelection({ repoId: "r1", workId: "gone", savedAt: 1 }, repos))
      .toEqual({ repoId: "r1", workId: "", savedAt: 1 });
  });

  it("作品不属于所选仓库时丢弃作品", () => {
    expect(reconcileSelection({ repoId: "r1", workId: "w3", savedAt: 1 }, repos))
      .toEqual({ repoId: "r1", workId: "", savedAt: 1 });
  });

  it("只记得作品时从作品反推大仓库", () => {
    expect(reconcileSelection({ repoId: "", workId: "w3", savedAt: 1 }, repos))
      .toEqual({ repoId: "r2", workId: "w3", savedAt: 1 });
  });

  it("旧数据把子仓库记进大仓库位时上提到父仓库", () => {
    expect(reconcileSelection({ repoId: "w2", workId: "", savedAt: 1 }, repos))
      .toEqual({ repoId: "r1", workId: "w2", savedAt: 1 });
  });

  it("仓库与作品都不存在时返回 null（回首页，不卡空页）", () => {
    expect(reconcileSelection({ repoId: "gone", workId: "gone", savedAt: 1 }, repos)).toBeNull();
  });
});
