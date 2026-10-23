// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  attachSceneBackdrop,
  createSceneResources,
  weSceneFitFor,
  type SceneBackdropState,
} from "./sceneBackdrop";
import type { SceneDecodeSuccess } from "./sceneWorker";
import type { ScenePlayerHandle, WeSceneFit } from "./scenePlayer";

const SCENE_PREFIX = "/wallpaper-engine/scene-resource/demiurge/";

type PlayerCallbacks = {
  onUnavailable(reason: string): void;
  onNeedsReload(): void;
};

function decodedFixture(overrides: Partial<SceneDecodeSuccess> = {}): SceneDecodeSuccess {
  return {
    id: 1,
    ok: true,
    manifest: {
      width: 1920,
      height: 1080,
      layers: [{ name: "bg", texUrl: `${SCENE_PREFIX}materials/bg.tex` }],
    } as unknown as Record<string, unknown>,
    resources: { "materials/bg.tex": new Uint8Array([137, 80, 78, 71]) },
    textureCount: 1,
    elapsedMs: 632,
    ...overrides,
  };
}

function stubObjectUrls() {
  const created: string[] = [];
  const revoked: string[] = [];
  let n = 0;
  vi.spyOn(URL, "createObjectURL").mockImplementation(() => {
    n += 1;
    const url = `blob:tex/${n}`;
    created.push(url);
    return url;
  });
  vi.spyOn(URL, "revokeObjectURL").mockImplementation((url) => { revoked.push(url); });
  return { created, revoked };
}

function fakePlayer() {
  const calls = { fit: [] as WeSceneFit[], paused: [] as boolean[], disposed: 0 };
  const handle: ScenePlayerHandle = {
    element: document.createElement("iframe"),
    setFit: (fit) => { calls.fit.push(fit); },
    setPaused: (paused) => { calls.paused.push(paused); },
    dispose: () => { calls.disposed += 1; },
  };
  return { handle, calls };
}

const flush = () => new Promise<void>((resolve) => { setTimeout(resolve, 0); });

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("weSceneFitFor", () => {
  it("播放器只有 cover/contain/fill，center 落到 cover 而不是原尺寸放大", () => {
    expect(weSceneFitFor("cover")).toBe("cover");
    expect(weSceneFitFor("contain")).toBe("contain");
    expect(weSceneFitFor("fill")).toBe("fill");
    expect(weSceneFitFor("center")).toBe("cover");
  });
});

describe("createSceneResources", () => {
  it("每条纹理换一个 blob，清单里的引用同步替换", () => {
    const urls = stubObjectUrls();
    const resources = createSceneResources(decodedFixture(), 4096);

    expect(urls.created).toHaveLength(1);
    const payload = JSON.parse(resources.manifestJson);
    expect(payload.manifest.layers[0].texUrl).toBe("blob:tex/1");
    expect(resources.textureCount).toBe(1);
    expect(resources.elapsedMs).toBe(632);
    expect(resources.pkgBytes).toBe(4096);

    resources.revoke();
    expect(urls.revoked).toEqual(["blob:tex/1"]);
  });

  it("产出上游要求的 {ok, manifest} 信封，不是裸清单", () => {
    stubObjectUrls();
    const payload = JSON.parse(createSceneResources(decodedFixture(), 1).manifestJson);
    expect(payload.ok).toBe(true);
    expect(payload.manifest.width).toBe(1920);
  });
});

describe("attachSceneBackdrop", () => {
  it("解码完成后报 ready，并用 cover 语义挂上播放器", async () => {
    stubObjectUrls();
    const host = document.createElement("div");
    document.body.appendChild(host);
    const states: SceneBackdropState[] = [];
    const mounted: { fit: WeSceneFit; paused: boolean; textureCount: number }[] = [];

    const attach = attachSceneBackdrop({
      host,
      source: "123456",
      fit: "center",
      paused: false,
      onState: (state) => { states.push(state); },
      load: async () => ({ decoded: decodedFixture(), pkgBytes: 4096 }),
      mount: (_host, resources, fit, paused) => {
        mounted.push({ fit, paused, textureCount: resources.textureCount });
        return fakePlayer().handle;
      },
    });

    await flush();
    expect(mounted).toEqual([{ fit: "cover", paused: false, textureCount: 1 }]);
    expect(states).toEqual([{ status: "ready", textureCount: 1, elapsedMs: 632, pkgBytes: 4096 }]);
    attach.dispose();
  });

  it("update 只把变化转发给播放器", async () => {
    stubObjectUrls();
    const player = fakePlayer();
    const attach = attachSceneBackdrop({
      host: document.createElement("div"),
      source: "1",
      fit: "cover",
      paused: false,
      onState: () => {},
      load: async () => ({ decoded: decodedFixture(), pkgBytes: 1 }),
      mount: () => player.handle,
    });
    await flush();

    attach.update({ fit: "cover", paused: false });
    expect(player.calls.fit).toEqual([]);
    expect(player.calls.paused).toEqual([]);

    attach.update({ fit: "contain", paused: false });
    attach.update({ fit: "contain", paused: true });
    expect(player.calls.fit).toEqual(["contain"]);
    expect(player.calls.paused).toEqual([true]);

    attach.dispose();
    expect(player.calls.disposed).toBe(1);
  });

  it("解码失败回落静态预览，带上可读原因", async () => {
    const states: SceneBackdropState[] = [];
    attachSceneBackdrop({
      host: document.createElement("div"),
      source: "1",
      fit: "cover",
      paused: false,
      onState: (state) => { states.push(state); },
      load: async () => { throw new Error("scene.pkg 里没有可用的 scene.json 场景结构"); },
    });
    await flush();

    expect(states).toEqual([
      { status: "fallback", reason: "scene.pkg 里没有可用的 scene.json 场景结构" },
    ]);
  });

  it("播放器报「清单不可用」即回落", async () => {
    stubObjectUrls();
    const states: SceneBackdropState[] = [];
    const captured = {} as { callbacks: PlayerCallbacks };
    const attach = attachSceneBackdrop({
      host: document.createElement("div"),
      source: "1",
      fit: "cover",
      paused: false,
      onState: (state) => { states.push(state); },
      load: async () => ({ decoded: decodedFixture(), pkgBytes: 1 }),
      mount: (_host, _resources, _fit, _paused, callbacks) => {
        captured.callbacks = callbacks;
        return fakePlayer().handle;
      },
    });
    await flush();

    captured.callbacks.onUnavailable("场景清单不可用");
    expect(states[states.length - 1]).toEqual({ status: "fallback", reason: "场景清单不可用" });
    attach.dispose();
  });

  it("上下文丢失可重建，反复丢失（上限 2 次）才回落", async () => {
    stubObjectUrls();
    const states: SceneBackdropState[] = [];
    const captured = {} as { callbacks: PlayerCallbacks };
    let mounts = 0;
    const attach = attachSceneBackdrop({
      host: document.createElement("div"),
      source: "1",
      fit: "cover",
      paused: false,
      onState: (state) => { states.push(state); },
      load: async () => ({ decoded: decodedFixture(), pkgBytes: 1 }),
      mount: (_host, _resources, _fit, _paused, callbacks) => {
        mounts += 1;
        captured.callbacks = callbacks;
        return fakePlayer().handle;
      },
    });
    await flush();
    expect(mounts).toBe(1);

    captured.callbacks.onNeedsReload();
    captured.callbacks.onNeedsReload();
    expect(mounts).toBe(3);

    captured.callbacks.onNeedsReload();
    expect(mounts).toBe(3);
    expect(states[states.length - 1]).toEqual({
      status: "fallback",
      reason: "渲染上下文反复丢失，已回落静态预览",
    });
    attach.dispose();
  });

  it("dispose 之后的迟到结果不再挂载，也不换 blob", async () => {
    const urls = stubObjectUrls();
    const states: SceneBackdropState[] = [];
    let resolveLoad: (v: { decoded: SceneDecodeSuccess; pkgBytes: number }) => void = () => {};
    const mounted: number[] = [];
    const attach = attachSceneBackdrop({
      host: document.createElement("div"),
      source: "1",
      fit: "cover",
      paused: false,
      onState: (state) => { states.push(state); },
      load: () => new Promise((resolve) => { resolveLoad = resolve; }),
      mount: (_host, resources) => {
        mounted.push(resources.textureCount);
        return fakePlayer().handle;
      },
    });

    attach.dispose();
    resolveLoad({ decoded: decodedFixture(), pkgBytes: 1 });
    await flush();

    expect(mounted).toEqual([]);
    expect(states).toEqual([]);
    expect(urls.revoked).toEqual([]);
  });
});

describe("场景来源标识（A 刀：不再只认纯数字工坊 id）", () => {
  it("来源原样交给取包通道——工坊与本地工程共用同一条路", async () => {
    stubObjectUrls();
    const seen: string[] = [];
    const attach = attachSceneBackdrop({
      host: document.createElement("div"),
      source: "local:D:/wallpaper/we-project",
      fit: "cover",
      paused: false,
      onState: () => {},
      load: async (source) => {
        seen.push(source);
        return { decoded: decodedFixture(), pkgBytes: 1 };
      },
      mount: () => fakePlayer().handle,
    });

    await flush();
    expect(seen).toEqual(["local:D:/wallpaper/we-project"]);
    attach.dispose();
  });

  it("工坊来源仍按 workshop:<id> 形态透传，不在前端拆解", async () => {
    stubObjectUrls();
    const seen: string[] = [];
    const attach = attachSceneBackdrop({
      host: document.createElement("div"),
      source: "workshop:200",
      fit: "cover",
      paused: false,
      onState: () => {},
      load: async (source) => {
        seen.push(source);
        return { decoded: decodedFixture(), pkgBytes: 1 };
      },
      mount: () => fakePlayer().handle,
    });

    await flush();
    expect(seen).toEqual(["workshop:200"]);
    attach.dispose();
  });
});
