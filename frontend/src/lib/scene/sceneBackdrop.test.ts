// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  attachSceneBackdrop,
  createSceneResources,
  SceneSessionStaleError,
  weSceneFitFor,
  type SceneBackdropState,
  type SceneResources,
} from "./sceneBackdrop";
import type { SceneDecodeSuccess } from "./sceneWorker";
import type { ScenePlayerHandle, WeSceneFit } from "./scenePlayer";
import type { SceneUserProperties } from "./vendor/we-scene-manifest";

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
      userBindings: [],
    } as unknown as Record<string, unknown>,
    resources: { "materials/bg.tex": new Uint8Array([137, 80, 78, 71]) },
    paths: ["materials/bg.tex"],
    textureCount: 1,
    elapsedMs: 632,
    session: 1,
    ...overrides,
  };
}

function nightFixture(overrides: Partial<SceneDecodeSuccess> = {}): SceneDecodeSuccess {
  return decodedFixture({
    manifest: {
      width: 1920,
      height: 1080,
      layers: [
        { name: "bg", texUrl: `${SCENE_PREFIX}materials/bg.tex` },
        { name: "night", texUrl: `${SCENE_PREFIX}materials/night.tex` },
      ],
      userBindings: ["display"],
    } as unknown as Record<string, unknown>,
    resources: { "materials/night.tex": new Uint8Array([1, 2, 3]) },
    paths: ["materials/bg.tex", "materials/night.tex"],
    textureCount: 2,
    elapsedMs: 18,
    session: 1,
    ...overrides,
  });
}

function stubObjectUrls() {
  const created: string[] = [];
  const revoked: string[] = [];
  const types: string[] = [];
  let n = 0;
  vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => {
    n += 1;
    const url = `blob:tex/${n}`;
    created.push(url);
    types.push((blob as Blob).type);
    return url;
  });
  vi.spyOn(URL, "revokeObjectURL").mockImplementation((url) => { revoked.push(url); });
  return { created, revoked, types };
}

function fakePlayer() {
  const calls = {
    fit: [] as WeSceneFit[],
    paused: [] as boolean[],
    manifests: [] as string[],
    recovers: 0,
    disposed: 0,
  };
  const handle: ScenePlayerHandle = {
    element: document.createElement("iframe"),
    setFit: (fit) => { calls.fit.push(fit); },
    setPaused: (paused) => { calls.paused.push(paused); },
    setManifest: (manifestJson) => { calls.manifests.push(manifestJson); },
    recover: () => { calls.recovers += 1; },
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

  it("MP4 纹理的 blob 标 video/mp4（清单后缀仍叫 .tex，不能按后缀猜）", () => {
    const urls = stubObjectUrls();
    const mp4 = new Uint8Array([0, 0, 0, 24, 0x66, 0x74, 0x79, 0x70, 1, 2, 3, 4]);
    createSceneResources(decodedFixture({ resources: { "materials/bg.tex": mp4 } }), 128);
    expect(urls.types).toEqual(["video/mp4"]);
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
    expect(states).toEqual([
      {
        status: "ready", textureCount: 1, elapsedMs: 632, pkgBytes: 4096,
        bindings: [], presetApplied: true,
      },
    ]);
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

    attach.recover();                            // 合成层重建 ⇒ 原样转给播放器，不做去重
    expect(player.calls.recovers).toBe(1);

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

describe("预设热更新（同一壁纸切 WE 选项不重解包）", () => {
  it("setProperties 只给新纹理建 blob，复用旧 URL，并把新清单推给播放器", async () => {
    const urls = stubObjectUrls();
    const player = fakePlayer();
    const states: SceneBackdropState[] = [];
    const rebuilds: Array<{
      session: number;
      properties: SceneUserProperties;
      knownPaths: string[];
    }> = [];
    const attach = attachSceneBackdrop({
      host: document.createElement("div"),
      source: "1",
      fit: "cover",
      paused: false,
      properties: { display: 0 },
      onState: (state) => { states.push(state); },
      load: async () => ({ decoded: decodedFixture(), pkgBytes: 4096 }),
      rebuild: async (session, properties, knownPaths) => {
        rebuilds.push({ session, properties, knownPaths });
        return nightFixture({ session });
      },
      mount: () => player.handle,
    });
    await flush();

    expect(urls.created).toEqual(["blob:tex/1"]);
    attach.setProperties({ display: 0 });
    await flush();
    expect(rebuilds).toEqual([]);              // 值没变不发重建

    attach.setProperties({ display: 1 });
    await flush();

    expect(rebuilds).toEqual([
      { session: 1, properties: { display: 1 }, knownPaths: ["materials/bg.tex"] },
    ]);
    expect(urls.created).toHaveLength(2);      // 只为新纹理建 blob
    expect(player.calls.manifests).toHaveLength(1);
    expect(JSON.parse(player.calls.manifests[0]).manifest.layers).toEqual([
      { name: "bg", texUrl: "blob:tex/1" },
      { name: "night", texUrl: "blob:tex/2" },
    ]);
    expect(states[states.length - 1]).toEqual({
      status: "ready",
      textureCount: 2,
      elapsedMs: 18,
      pkgBytes: 4096,
      bindings: ["display"],
      presetApplied: true,
    });

    // 再切回白天：两条路径都已有 blob，Worker 不需要回传任何纹理。
    attach.setProperties({ display: 0 });
    await flush();
    expect(rebuilds[1].knownPaths).toEqual(["materials/bg.tex", "materials/night.tex"]);

    attach.dispose();
    expect([...urls.revoked].sort()).toEqual([...urls.created].sort());
  });

  it("连续拖动滑杆：同一时刻只跑一次重建，中间结果不推给播放器", async () => {
    stubObjectUrls();
    const player = fakePlayer();
    const states: SceneBackdropState[] = [];
    const requested: SceneUserProperties[] = [];
    const gates: Array<(value: SceneDecodeSuccess) => void> = [];
    const attach = attachSceneBackdrop({
      host: document.createElement("div"),
      source: "1",
      fit: "cover",
      paused: false,
      properties: { display: 0 },
      onState: (state) => { states.push(state); },
      load: async () => ({ decoded: decodedFixture(), pkgBytes: 1 }),
      rebuild: (_session, properties) => {
        requested.push(properties);
        return new Promise((resolve) => { gates.push((value) => resolve(value)); });
      },
      mount: () => player.handle,
    });
    await flush();

    attach.setProperties({ display: 1 });
    await flush();
    attach.setProperties({ display: 0 });
    await flush();
    expect(requested).toEqual([{ display: 1 }]);

    gates[0](nightFixture({ session: 2 }));
    await flush();
    expect(requested).toEqual([{ display: 1 }, { display: 0 }]);
    expect(player.calls.manifests).toEqual([]);   // 中间态被丢弃

    gates[1](nightFixture({ session: 2 }));
    await flush();
    expect(player.calls.manifests).toHaveLength(1);
    expect(states[states.length - 1]).toMatchObject({ status: "ready", bindings: ["display"] });

    attach.dispose();
  });

  it("热重建期间预设状态是 pending，清单真落到播放器后才转 applied", async () => {
    stubObjectUrls();
    const player = fakePlayer();
    const states: SceneBackdropState[] = [];
    const gates: Array<(value: SceneDecodeSuccess) => void> = [];
    const attach = attachSceneBackdrop({
      host: document.createElement("div"),
      source: "1",
      fit: "cover",
      paused: false,
      properties: { display: 0 },
      onState: (state) => { states.push(state); },
      load: async () => ({ decoded: decodedFixture(), pkgBytes: 1 }),
      rebuild: () => new Promise<SceneDecodeSuccess>((resolve) => { gates.push(resolve); }),
      mount: () => player.handle,
    });
    await flush();
    expect(states).toHaveLength(1);
    expect(states[0]).toMatchObject({ status: "ready", presetApplied: true });

    attach.setProperties({ display: 1 });
    // 面板已经换了值、画布还是旧清单：这段窗口期必须看得见，否则真机验证会拍到旧画面
    expect(states[states.length - 1]).toMatchObject({ status: "ready", presetApplied: false });

    gates[0](nightFixture({ session: 1 }));
    await flush();
    expect(states[states.length - 1]).toMatchObject({ status: "ready", presetApplied: true });
    attach.dispose();
  });

  it("会话过期（壁纸已切换）静默丢弃，不把正在显示的壁纸判死", async () => {
    stubObjectUrls();
    const states: SceneBackdropState[] = [];
    const attach = attachSceneBackdrop({
      host: document.createElement("div"),
      source: "1",
      fit: "cover",
      paused: false,
      onState: (state) => { states.push(state); },
      load: async () => ({ decoded: decodedFixture(), pkgBytes: 1 }),
      rebuild: async () => { throw new SceneSessionStaleError(); },
      mount: () => fakePlayer().handle,
    });
    await flush();

    attach.setProperties({ display: 1 });
    await flush();
    expect(states.filter((state) => state.status === "fallback")).toEqual([]);
    attach.dispose();
  });
});

describe("WE 内置资产（清单引用、但 scene.pkg 里没有的字体/贴图）", () => {
  const ASSET_REL = "fonts/spincycle_3d_ot.otf";
  const ASSET_KEY = `we-asset/${ASSET_REL}`;

  function clockFixture(overrides: Partial<SceneDecodeSuccess> = {}): SceneDecodeSuccess {
    return decodedFixture({
      manifest: {
        width: 1920,
        height: 1080,
        layers: [
          { name: "bg", texUrl: `${SCENE_PREFIX}materials/bg.tex` },
          { name: "Clock", kind: "text", font: `${SCENE_PREFIX}${ASSET_KEY}`, x: 960, y: 540, fontSize: 20 },
        ],
        userBindings: ["display"],
      } as unknown as Record<string, unknown>,
      paths: ["materials/bg.tex", ASSET_KEY],
      ...overrides,
    });
  }

  it("补到字节的内置资产走包内纹理同一条通道：建 blob、重写 URL、MIME 按后缀", async () => {
    const urls = stubObjectUrls();
    const fetched: string[] = [];
    let applied: SceneResources | null = null;
    const states: SceneBackdropState[] = [];
    const attach = attachSceneBackdrop({
      host: document.createElement("div"),
      source: "1",
      fit: "cover",
      paused: false,
      onState: (state) => { states.push(state); },
      load: async () => ({ decoded: clockFixture(), pkgBytes: 4096 }),
      mount: (_host, resources) => {
        applied = resources;
        return fakePlayer().handle;
      },
      fetchBuiltIn: async (path) => {
        fetched.push(path);
        return { bytes: new Uint8Array([79, 84, 84, 79]) };
      },
    });
    await flush();

    // 路由要的是 assets 目录内的相对路径，清单里的 we-asset/ 命名空间前缀在这一层剥掉。
    expect(fetched).toEqual([ASSET_REL]);
    expect(applied).not.toBeNull();
    const manifest = JSON.parse(applied!.manifestJson).manifest;
    expect(manifest.layers[1].font).toBe("blob:tex/2");
    expect(urls.types[1]).toBe("font/otf");
    expect(applied!.paths).toContain(ASSET_KEY);
    expect(states[states.length - 1]).toMatchObject({ status: "ready", textureCount: 1 });

    attach.dispose();
  });

  it("取不到（没装 WE）只缺这只字体：清单保留原 URL，画面照常 ready", async () => {
    const urls = stubObjectUrls();
    const states: SceneBackdropState[] = [];
    let applied: SceneResources | null = null;
    const attach = attachSceneBackdrop({
      host: document.createElement("div"),
      source: "1",
      fit: "cover",
      paused: false,
      onState: (state) => { states.push(state); },
      load: async () => ({ decoded: clockFixture(), pkgBytes: 4096 }),
      mount: (_host, resources) => {
        applied = resources;
        return fakePlayer().handle;
      },
      fetchBuiltIn: async () => null,
    });
    await flush();

    expect(urls.created).toHaveLength(1);          // 只有包内纹理拿到 blob
    const manifest = JSON.parse(applied!.manifestJson).manifest;
    expect(manifest.layers[1].font).toBe(`${SCENE_PREFIX}${ASSET_KEY}`);  // 未重写，播放器按加载失败处理
    expect(states[states.length - 1]).toMatchObject({ status: "ready" });
    attach.dispose();
  });

  it("取字节抛错只丢这只字体，不判死；已在手的不再重复取", async () => {
    stubObjectUrls();
    const states: SceneBackdropState[] = [];
    const fetched: string[] = [];
    const attach = attachSceneBackdrop({
      host: document.createElement("div"),
      source: "1",
      fit: "cover",
      paused: false,
      properties: { display: 0 },
      onState: (state) => { states.push(state); },
      // 白天预设里没有文字图层，切到夜晚才第一次引用内置字体。
      load: async () => ({ decoded: decodedFixture(), pkgBytes: 4096 }),
      rebuild: async (session, properties) =>
        (properties.display === 1 ? clockFixture({ session }) : decodedFixture({ session })),
      mount: () => fakePlayer().handle,
      fetchBuiltIn: async (path) => {
        fetched.push(path);
        if (fetched.length === 1) throw new Error("网络中断");
        return { bytes: new Uint8Array([1, 2, 3]) };
      },
    });
    await flush();
    expect(fetched).toEqual([]);

    attach.setProperties({ display: 1 });
    await flush();
    expect(fetched).toEqual([ASSET_REL]);
    expect(states[states.length - 1]).toMatchObject({ status: "ready", presetApplied: true });

    // 第一次取失败 ⇒ 没有 blob ⇒ 下一次仍会尝试；成功后再切来切去命中 knownPaths。
    attach.setProperties({ display: 0 });
    await flush();
    attach.setProperties({ display: 1 });
    await flush();
    expect(fetched).toHaveLength(2);
    expect(states[states.length - 1]).toMatchObject({ status: "ready", presetApplied: true });

    attach.setProperties({ display: 0 });
    await flush();
    attach.setProperties({ display: 1 });
    await flush();
    expect(fetched).toHaveLength(2);

    attach.dispose();
  });

  it("内置序列贴图的帧表落到消费它的粒子系统上（不知道格宽，六团雾画成一团）", async () => {
    stubObjectUrls();
    let applied: SceneResources | null = null;
    // 官方雾贴图在 WE 安装目录里，包内没有 ⇒ 清单建的时候无从知道它是一张 8×8 序列页。
    const sheetKey = "we-asset/particle/fog/fog2.tex";
    const attach = attachSceneBackdrop({
      host: document.createElement("div"),
      source: "1",
      fit: "cover",
      paused: false,
      onState: () => {},
      load: async () => ({
        decoded: decodedFixture({
          manifest: {
            width: 1920,
            height: 1080,
            layers: [{ name: "bg", texUrl: `${SCENE_PREFIX}materials/bg.tex` }],
            userBindings: [],
            particles3d: [
              { name: "fog", texUrl: `${SCENE_PREFIX}${sheetKey}` },
              // 包内纹理靠文件名已经知道格宽：那是作者写死的，帧表不得覆盖。
              { name: "birds", texUrl: `${SCENE_PREFIX}bird/birds_128x120x16.tex`, frames: { w: 128, h: 120, count: 16 } },
            ],
          } as unknown as Record<string, unknown>,
          paths: ["materials/bg.tex", sheetKey],
        }),
        pkgBytes: 4096,
      }),
      mount: (_host, resources) => {
        applied = resources;
        return fakePlayer().handle;
      },
      fetchBuiltIn: async () => ({ bytes: new Uint8Array([137, 80, 78, 71]), frames: { w: 128, h: 128, count: 64 } }),
    });
    await flush();

    const manifest = JSON.parse(applied!.manifestJson).manifest;
    expect(manifest.particles3d[0].frames).toEqual({ w: 128, h: 128, count: 64 });
    expect(manifest.particles3d[1].frames).toEqual({ w: 128, h: 120, count: 16 });
    attach.dispose();
  });
});
