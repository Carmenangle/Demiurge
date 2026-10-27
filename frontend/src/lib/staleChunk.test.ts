import { describe, expect, it } from "vitest";
import {
  STALE_CHUNK_RELOAD_GUARD_MS,
  cacheBustedUrl,
  isStaleChunkError,
  shouldAutoReload,
  type KeyValueStorage,
} from "./staleChunk";

class MemoryStorage implements KeyValueStorage {
  private data = new Map<string, string>();
  getItem(key: string): string | null {
    return this.data.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.data.set(key, value);
  }
}

describe("staleChunk 旧资源识别", () => {
  it("识别 Vite 动态导入 404（本次实锤原文）", () => {
    expect(
      isStaleChunkError(
        new Error(
          "Failed to fetch dynamically imported module: http://127.0.0.1:8010/assets/NodeManagerView-BDZquKu_.js",
        ),
      ),
    ).toBe(true);
  });

  it("识别其它浏览器/打包器的 chunk 失效措辞", () => {
    expect(isStaleChunkError(new Error("Importing a module script failed."))).toBe(true);
    expect(isStaleChunkError(new Error("Loading chunk 42 failed."))).toBe(true);
    expect(isStaleChunkError("ChunkLoadError: Loading chunk vendors failed")).toBe(true);
  });

  it("普通渲染异常不误判", () => {
    expect(isStaleChunkError(new Error("Cannot read properties of undefined"))).toBe(false);
    expect(isStaleChunkError(null)).toBe(false);
    expect(isStaleChunkError("boom")).toBe(false);
  });
});

describe("staleChunk 重载限频", () => {
  it("首次允许，守护窗口内拒绝，过窗后再次允许", () => {
    const storage = new MemoryStorage();
    const t0 = 1_000_000;
    expect(shouldAutoReload(t0, storage)).toBe(true);
    expect(shouldAutoReload(t0 + 1_000, storage)).toBe(false);
    expect(shouldAutoReload(t0 + STALE_CHUNK_RELOAD_GUARD_MS - 1, storage)).toBe(false);
    expect(shouldAutoReload(t0 + STALE_CHUNK_RELOAD_GUARD_MS, storage)).toBe(true);
  });

  it("拿不到 storage 时不做自动重载（宁可展示错误卡片）", () => {
    expect(shouldAutoReload(1_000, null)).toBe(false);
    const broken: KeyValueStorage = {
      getItem: () => {
        throw new Error("SecurityError");
      },
      setItem: () => undefined,
    };
    expect(shouldAutoReload(1_000, broken)).toBe(false);
  });
});

describe("staleChunk 重载绕过缓存", () => {
  it("换查询串才是真绕过：路径与 hash 都保住", () => {
    const next = cacheBustedUrl("http://127.0.0.1:8010/?x=1#/assets/works", 1_700_000_000_000);
    expect(next).toBe(
      "http://127.0.0.1:8010/?x=1&_r=1700000000000#/assets/works",
    );
  });

  it("已有 _r 时改写而不是叠加（多次自救不会把 URL 越滚越长）", () => {
    expect(cacheBustedUrl("http://127.0.0.1:8010/?_r=1", 2)).toBe("http://127.0.0.1:8010/?_r=2");
  });
});
