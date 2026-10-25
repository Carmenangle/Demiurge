// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import { WE_SCENE_PLAYER_HTML } from "./vendor/we-scene-player-html";
import { buildScenePlayerSrcdoc, mountScenePlayer } from "./scenePlayer";

const SHIM_RE = /<script>\(function \(\) \{[\s\S]*?\}\)\(\);<\/script>/;

const mountOptions = (overrides: Partial<Parameters<typeof mountScenePlayer>[1]> = {}) => ({
  manifestJson: '{"ok":true,"manifest":{"width":1,"height":1,"layers":[]}}',
  fit: "cover" as const,
  paused: false,
  onUnavailable: vi.fn(),
  onNeedsReload: vi.fn(),
  ...overrides,
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("buildScenePlayerSrcdoc", () => {
  it("只注入一段垫片，且注入点在上游脚本之前", () => {
    const src = buildScenePlayerSrcdoc("blob:http://localhost/manifest");
    expect(src.match(/<script>/g)).toHaveLength(2);
    expect(SHIM_RE.test(src)).toBe(true);
    expect(src.indexOf(SHIM_RE.exec(src)![0])).toBeLessThan(src.indexOf("dsh-scene-needs-reload"));
  });

  it("除垫片外上游 HTML 本体一字未改（再同步上游时 diff 只有这一处）", () => {
    const src = buildScenePlayerSrcdoc("blob:http://localhost/manifest");
    expect(src.replace(SHIM_RE, "")).toBe(WE_SCENE_PLAYER_HTML);
  });

  it("垫片只改清单请求，纹理走 blob 不受影响", () => {
    const src = buildScenePlayerSrcdoc("blob:http://localhost/m");
    expect(src).toContain("'/wallpaper-engine/scene-manifest/'");
    expect(src).toContain('href.indexOf("/wallpaper-engine/scene-manifest/") === 0');
    expect(src).toContain('nativeFetch(MANIFEST_URL, init)');
  });

  it("blob URL 里的 $& 不被当成替换模式", () => {
    const src = buildScenePlayerSrcdoc("blob:http://localhost/$&not-a-pattern");
    expect(src).toContain("blob:http://localhost/$&not-a-pattern");
  });

  it("播放器认识清单热更新消息，并按 meshKey 复用 GPU 缓冲", () => {
    const src = buildScenePlayerSrcdoc("blob:http://localhost/m");
    expect(src).toContain("dsh-set-manifest");
    expect(src).toContain("swapSceneManifest");
    expect(src).toContain("meshCacheKey");
  });
});

describe("垫片：暂停 = 定格到内容成型（vendor 播放器本体不动）", () => {
  /**
   * 垫片靠「真实 draw call 数在连续两帧里相等、且这一帧窗口内没有新贴图上传」判定画面已
   * 成型，jsdom 里没有 WebGL，所以造一个最小 prototype 给它挂钩，再由假播放器按
   * `draws(frame)` 调用 drawArrays。暂停期间 `frames` 里排的是垫片自己的补帧回调，
   * 逐帧 step() 即可推进；`upload()` 模拟 img.onload 里的那次纹理上传（帧间隙发生）。
   */
  const runShim = (draws: (frame: number) => number = () => 0) => {
    const src = buildScenePlayerSrcdoc("blob:http://localhost/m");
    const body = SHIM_RE.exec(src)![0].replace(/^<script>/, "").replace(/<\/script>$/, "");
    const frames: FrameRequestCallback[] = [];
    const originalRaf = window.requestAnimationFrame;
    const originalFetch = window.fetch;
    const originalGl = (window as unknown as { WebGLRenderingContext?: unknown }).WebGLRenderingContext;
    // 垫片自己往 window 上挂了 message 监听器且无从注销；多个用例共用一个 jsdom window，
    // 不摘掉的话后一个用例发的消息会同时喂给前一个垫片，它的补帧定时器就会往当前
    // requestAnimationFrame（已是后一个用例的垫片）里塞回调，帧数凭空多一。
    const added: Array<[string, EventListener]> = [];
    const originalAdd = window.addEventListener.bind(window);
    const originalRemove = window.removeEventListener.bind(window);
    window.addEventListener = ((type: string, listener: EventListenerOrEventListenerObject) => {
      added.push([type, listener as EventListener]);
      originalAdd(type, listener);
    }) as typeof window.addEventListener;
    const glProto = { drawArrays() {}, drawElements() {}, texImage2D() {} };
    function FakeGL() {}
    (FakeGL as unknown as { prototype: unknown }).prototype = glProto;
    (window as unknown as { WebGLRenderingContext: unknown }).WebGLRenderingContext = FakeGL;
    (window as unknown as { fetch: unknown }).fetch = () => Promise.resolve({ json: async () => ({}) });
    window.requestAnimationFrame = ((cb: FrameRequestCallback) => {
      frames.push(cb);
      return frames.length;
    }) as typeof window.requestAnimationFrame;
    new Function(body)();

    const rendered: number[] = [];
    const player = (now: number) => {
      const count = draws(rendered.length);
      for (let i = 0; i < count; i += 1) glProto.drawArrays();
      rendered.push(now);
      window.requestAnimationFrame(player);
    };
    return {
      frames,
      rendered,
      player,
      /** 推进一帧（未暂停时是播放器帧，暂停时是垫片的补帧）；返回是否真的推进了。 */
      step(now = 16) {
        const cb = frames.shift();
        if (!cb) return false;
        cb(now);
        return true;
      },
      restore() {
        for (const [type, listener] of added) originalRemove(type, listener);
        window.addEventListener = originalAdd as typeof window.addEventListener;
        window.requestAnimationFrame = originalRaf;
        (window as unknown as { fetch: unknown }).fetch = originalFetch;
        (window as unknown as { WebGLRenderingContext: unknown }).WebGLRenderingContext = originalGl;
      },
      /** 模拟一次纹理上传：真机里它发生在 img.onload（不在 rAF 回调里），故可在帧间隙调用。 */
      upload() {
        glProto.texImage2D();
      },
    };
  };

  const sendPause = (paused: boolean) => {
    window.dispatchEvent(new MessageEvent("message", {
      data: { type: "dsh-set-pause", paused },
      source: window,
    }));
  };

  const sendManifest = () => {
    window.dispatchEvent(new MessageEvent("message", {
      data: { type: "dsh-set-manifest", manifest: { layers: [] } },
      source: window,
    }));
  };

  it("暂停后补到内容成型才停帧，恢复时补跑挂起帧并继续调度", () => {
    const shim = runShim(() => 6);
    try {
      window.requestAnimationFrame(shim.player);
      expect(shim.frames).toHaveLength(1);

      shim.step();                                  // 模拟第一帧落地
      expect(shim.rendered).toEqual([16]);

      sendPause(true);
      shim.step();                                  // 已在原生队列里的那一帧仍会画完
      expect(shim.rendered).toHaveLength(2);
      expect(shim.frames).toHaveLength(1);          // 但它末尾的再调度被挂起、改由垫片补

      expect(shim.step()).toBe(true);               // 补帧：第一帧没有历史可比，继续
      expect(shim.step()).toBe(true);               // 第二帧绘制数与上一帧相等 ⇒ 定格
      expect(shim.frames).toHaveLength(0);
      expect(shim.rendered).toHaveLength(4);        // 定格前只多画了两帧
      expect(window.requestAnimationFrame(() => {})).toBe(0);

      sendPause(false);
      expect(shim.rendered).toHaveLength(5);        // 恢复时补跑挂起的那一帧
      expect(shim.frames).toHaveLength(1);          // 补跑的帧重新排进原生队列
    } finally {
      shim.restore();
    }
  });

  it("只认宿主发来的暂停消息", () => {
    const shim = runShim();
    try {
      window.dispatchEvent(new MessageEvent("message", {
        data: { type: "dsh-set-pause", paused: true },
        source: null as unknown as Window,
      }));
      expect(window.requestAnimationFrame(() => {})).not.toBe(0);
      expect(shim.frames).toHaveLength(1);
    } finally {
      shim.restore();
    }
  });

  it("开局即暂停：清单没到位前一直补帧，画出了才定格（否则画布全白）", () => {
    // 前三帧上游还没拿到清单（一次绘制都没有），之后两帧画满 4 个图元
    const shim = runShim((frame) => (frame < 3 ? 0 : 4));
    try {
      sendPause(true);
      window.requestAnimationFrame(shim.player);
      expect(shim.rendered).toHaveLength(0);        // 同步阶段一帧都不画

      for (let i = 0; i < 3; i += 1) {
        expect(shim.step()).toBe(true);             // 空帧 ⇒ 内容还没成型，继续按 vsync 出帧
      }
      expect(shim.rendered).toHaveLength(3);
      expect(shim.frames).toHaveLength(1);

      shim.step();                                  // 第 4 帧：4 次绘制，但上一帧是 0
      shim.step();                                  // 第 5 帧：又是 4 次 ⇒ 内容稳定，定格
      expect(shim.rendered).toHaveLength(5);
      expect(shim.frames).toHaveLength(0);
    } finally {
      shim.restore();
    }
  });

  it("暂停中补帧吃同一个时间戳：着色器相位不随补帧前进", () => {
    const shim = runShim(() => 3);
    try {
      sendPause(true);
      window.requestAnimationFrame(shim.player);
      for (let i = 0; i < 4; i += 1) shim.step(1000 + i); // 每帧故意喂不同的 now
      expect(shim.rendered.length).toBeGreaterThan(1);
      // 上游拿 elapsed = now - startTime 喂 u_time（云/水/霓虹不吃 animTime），
      // 时间戳若跟着真实时钟走，暂停中每补一帧就换一个姿态 ⇒ 重画不幂等。
      expect(new Set(shim.rendered).size).toBe(1);
    } finally {
      shim.restore();
    }
  });

  it("暂停中宿主请补帧，仍走钉住时间戳的那条路（不把着色器相位推走）", () => {
    const shim = runShim(() => 5);
    try {
      sendPause(true);
      window.requestAnimationFrame(shim.player);
      shim.step(); shim.step();                     // 预热到定格
      const frozen = shim.rendered[0];
      expect(shim.frames).toHaveLength(0);

      window.dispatchEvent(new MessageEvent("message", {
        data: { type: "dsh-recover-renderer" },
        source: window,
      }));
      expect(shim.frames).toHaveLength(1);          // 合成层重建 ⇒ 立刻补一帧
      shim.step(); shim.step();
      expect(shim.rendered.every((t) => t === frozen)).toBe(true);
      expect(shim.frames).toHaveLength(0);          // 补完照旧定格
    } finally {
      shim.restore();
    }
  });

  it("暂停中换清单会重新预热到新画面成型，不会停在半张画面上", () => {
    let swapped = false;
    const shim = runShim((frame) => (frame < 4 ? 6 : swapped ? 2 : 6));
    try {
      window.requestAnimationFrame(shim.player);
      shim.step();                                  // 未暂停，正常画一帧
      sendPause(true);
      shim.step(); shim.step(); shim.step();        // 预热两帧后定格
      expect(shim.frames).toHaveLength(0);
      expect(shim.rendered).toHaveLength(4);

      swapped = true;                               // 新清单只画得出 2 个图元
      sendManifest();
      expect(shim.frames).toHaveLength(1);          // 换清单立刻重新起帧
      shim.step();                                  // 新内容第一帧（与旧历史不等 ⇒ 继续）
      expect(shim.frames).toHaveLength(1);
      shim.step();                                  // 连续两帧都是 2 ⇒ 新画面成型
      expect(shim.frames).toHaveLength(0);
      expect(shim.rendered).toHaveLength(6);
      expect(swapped).toBe(true);                   // 定格的这一帧吃的就是新清单

      sendPause(false);
      expect(shim.rendered).toHaveLength(7);        // 恢复时不会把补过的帧重复画
      expect(shim.frames).toHaveLength(1);
    } finally {
      shim.restore();
    }
  });

  it("每帧图元数一直在变的场景预热到上限就定格，不会把暂停过成满帧重绘", () => {
    const shim = runShim((frame) => frame + 1);     // 永远在变 ⇒ 等不到「连续两帧相等」
    try {
      sendPause(true);
      window.requestAnimationFrame(shim.player);
      let pumped = 0;
      while (shim.step() && pumped < 400) pumped += 1;
      expect(shim.frames).toHaveLength(0);          // 上限处收手
      expect(pumped).toBeLessThanOrEqual(130);      // 只多跑一轮预热，不是无限
      expect(pumped).toBeGreaterThan(100);
    } finally {
      shim.restore();
    }
  });

  it("预热期内贴图还在解码：绘制数已经相等也不许定格", () => {
    const shim = runShim(() => 6);
    try {
      sendPause(true);
      window.requestAnimationFrame(shim.player);
      shim.step();                                  // 第一帧：没有历史可比
      shim.upload();                                // 一张天空贴图在这帧之后才解码完
      shim.step();                                  // 绘制数与上一帧相等，但这一帧窗口里有上传
      expect(shim.frames).toHaveLength(1);          // ⇒ 内容还没齐，继续起帧
      shim.step();                                  // 再无新贴图 ⇒ 定格
      expect(shim.frames).toHaveLength(0);
    } finally {
      shim.restore();
    }
  });

  it("已经定格了才有新贴图解码完 ⇒ 自动补一帧，不停在半张画面上", () => {
    const shim = runShim(() => 6);
    try {
      sendPause(true);
      window.requestAnimationFrame(shim.player);
      shim.step(); shim.step();                     // 预热到定格
      expect(shim.frames).toHaveLength(0);

      shim.upload();                                // 迟到的贴图（img.onload 不在 rAF 里）
      expect(shim.frames).toHaveLength(1);          // ⇒ 立刻再补一帧，把它画上去
      shim.step();
      expect(shim.rendered).toHaveLength(3);
      expect(shim.frames).toHaveLength(1);          // 这一帧看到了新贴图 ⇒ 再确认一帧
      shim.step();
      expect(shim.frames).toHaveLength(0);          // 补完照旧定格，不退化成满帧重绘
    } finally {
      shim.restore();
    }
  });
});

describe("mountScenePlayer", () => {
  const withObjectUrls = () => {
    const created: string[] = [];
    const revoked: string[] = [];
    const blobs: Blob[] = [];
    let n = 0;
    vi.spyOn(URL, "createObjectURL").mockImplementation((obj: Blob | MediaSource) => {
      n += 1;
      const url = `blob:mock/${n}`;
      created.push(url);
      blobs.push(obj as Blob);
      return url;
    });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation((url) => { revoked.push(url); });
    return { created, revoked, blobs };
  };

  it("挂出一个无 sandbox 的同源 blob: iframe，文档带垫片与清单 URL", async () => {
    const urls = withObjectUrls();
    const parent = document.createElement("div");
    document.body.appendChild(parent);
    const handle = mountScenePlayer(parent, mountOptions());

    const frame = parent.querySelector("iframe");
    expect(frame).toBe(handle.element);
    expect(frame!.className).toBe("app-backdrop-scene");
    expect(frame!.getAttribute("sandbox")).toBeNull();
    // about:srcdoc 的 location.origin 是字符串 'null'，播放器的 origin 校验会丢消息 ⇒ 必须用 blob: 文档
    expect(frame!.getAttribute("srcdoc")).toBeNull();
    expect(frame!.getAttribute("src")).toBe(urls.created[1]);
    const playerDoc = await urls.blobs[1].text();
    expect(playerDoc).toContain(`var MANIFEST_URL = ${JSON.stringify(urls.created[0])}`);
    expect(playerDoc).toContain("dsh-scene-needs-reload");   // 上游播放器本体仍在
    expect(playerDoc.indexOf("var MANIFEST_URL")).toBeLessThan(playerDoc.indexOf("dsh-scene-needs-reload"));

    handle.dispose();
    expect(parent.children).toHaveLength(0);
    expect([...urls.revoked].sort()).toEqual([...urls.created].sort());
  });

  it("load 之后把初始 fit / paused 发给播放器", () => {
    withObjectUrls();
    const parent = document.createElement("div");
    document.body.appendChild(parent);
    const handle = mountScenePlayer(parent, mountOptions({ fit: "contain", paused: true }));
    const posted: Record<string, unknown>[] = [];
    Object.defineProperty(handle.element.contentWindow, "postMessage", {
      configurable: true,
      writable: true,
      value: (message: Record<string, unknown>) => { posted.push(message); },
    });

    handle.element.dispatchEvent(new Event("load"));
    expect(posted).toEqual([
      { type: "dsh-set-fit", fit: "contain" },
      { type: "dsh-set-pause", paused: true },
    ]);

    posted.length = 0;
    handle.setFit("fill");
    handle.setPaused(false);
    expect(posted).toEqual([
      { type: "dsh-set-fit", fit: "fill" },
      { type: "dsh-set-pause", paused: false },
    ]);
    handle.dispose();
  });

  it("setManifest 在 load 之前排队、load 之后直接推送裸清单", () => {
    withObjectUrls();
    const parent = document.createElement("div");
    document.body.appendChild(parent);
    const handle = mountScenePlayer(parent, mountOptions());
    const posted: Record<string, unknown>[] = [];
    Object.defineProperty(handle.element.contentWindow, "postMessage", {
      configurable: true,
      writable: true,
      value: (message: Record<string, unknown>) => { posted.push(message); },
    });

    handle.setManifest(JSON.stringify({ ok: true, manifest: { layers: [{ name: "night" }] } }));
    expect(posted).toEqual([]);                // 播放器脚本还没注册监听，先存着

    handle.element.dispatchEvent(new Event("load"));
    expect(posted).toEqual([
      { type: "dsh-set-fit", fit: "cover" },
      { type: "dsh-set-pause", paused: false },
      { type: "dsh-set-manifest", manifest: { layers: [{ name: "night" }] } },
    ]);

    posted.length = 0;
    handle.setManifest(JSON.stringify({ ok: true, manifest: { layers: [] } }));
    expect(posted).toEqual([{ type: "dsh-set-manifest", manifest: { layers: [] } }]);

    handle.setManifest("{not json");
    expect(posted).toHaveLength(1);            // 坏 JSON 不打断既有画面
    handle.dispose();
  });

  it("recover 请播放器立刻补一帧（合成层重建后暂停态不留空白画布）", () => {
    withObjectUrls();
    const parent = document.createElement("div");
    document.body.appendChild(parent);
    const handle = mountScenePlayer(parent, mountOptions({ paused: true }));
    const posted: Record<string, unknown>[] = [];
    Object.defineProperty(handle.element.contentWindow, "postMessage", {
      configurable: true,
      writable: true,
      value: (message: Record<string, unknown>) => { posted.push(message); },
    });
    handle.element.dispatchEvent(new Event("load"));
    posted.length = 0;

    handle.recover();
    expect(posted).toEqual([{ type: "dsh-recover-renderer" }]);
    handle.dispose();
  });

  it("只认这个 iframe 发来的消息，并区分不可用 / 需重载", () => {
    withObjectUrls();
    const parent = document.createElement("div");
    document.body.appendChild(parent);
    const options = mountOptions();
    const handle = mountScenePlayer(parent, options);

    window.dispatchEvent(new MessageEvent("message", {
      data: { type: "dsh-scene-unavailable" },
      source: window,
    }));
    expect(options.onUnavailable).not.toHaveBeenCalled();

    window.dispatchEvent(new MessageEvent("message", {
      data: { type: "dsh-scene-unavailable" },
      source: handle.element.contentWindow,
    }));
    expect(options.onUnavailable).toHaveBeenCalledWith("场景清单不可用");

    window.dispatchEvent(new MessageEvent("message", {
      data: { type: "dsh-scene-needs-reload" },
      source: handle.element.contentWindow,
    }));
    expect(options.onNeedsReload).toHaveBeenCalledTimes(1);

    handle.dispose();
    window.dispatchEvent(new MessageEvent("message", {
      data: { type: "dsh-scene-needs-reload" },
      source: handle.element.contentWindow,
    }));
    expect(options.onNeedsReload).toHaveBeenCalledTimes(1);
  });
});

describe("鼠标视差转发（背景层 pointer-events:none 时仍能驱动画面）", () => {
  const withUrls = () => {
    vi.spyOn(URL, "createObjectURL").mockImplementation(() => "blob:test/doc");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  };

  const mountWithFrames = (options: Partial<Parameters<typeof mountScenePlayer>[1]> = {}) => {
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      frames.push(cb);
      return frames.length;
    });
    vi.stubGlobal("cancelAnimationFrame", () => {});
    const parent = document.createElement("div");
    document.body.appendChild(parent);
    const handle = mountScenePlayer(parent, {
      manifestJson: "{}",
      fit: "cover",
      paused: false,
      onUnavailable: () => {},
      onNeedsReload: () => {},
      ...options,
    });
    const posted: Record<string, unknown>[] = [];
    Object.defineProperty(handle.element.contentWindow, "postMessage", {
      configurable: true,
      writable: true,
      value: (message: Record<string, unknown>) => { posted.push(message); },
    });
    return { handle, posted, frames };
  };

  it("宿主 mousemove 归一化坐标，按帧节流后才发给 iframe", () => {
    withUrls();
    const { handle, posted, frames } = mountWithFrames();
    try {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 1000 });
      Object.defineProperty(window, "innerHeight", { configurable: true, value: 500 });

      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 250, clientY: 125 }));
      expect(posted).toEqual([]);                 // 未到帧不发，避免洪水
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 750, clientY: 250 }));
      frames.splice(0).forEach((cb) => cb(0));
      expect(posted).toEqual([{ type: "dsh-pointer", x: 0.75, y: 0.5 }]);  // 只发最后一帧位置
    } finally {
      handle.dispose();
      vi.unstubAllGlobals();
    }
  });

  it("pointer:false 时不监听宿主鼠标；dispose 后也不再转发", () => {
    withUrls();
    const off = mountWithFrames({ pointer: false });
    try {
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 100, clientY: 100 }));
      off.frames.splice(0).forEach((cb) => cb(0));
      expect(off.posted).toEqual([]);
    } finally {
      off.handle.dispose();
    }

    const on = mountWithFrames();
    try {
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 100, clientY: 100 }));
      on.handle.dispose();
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 200, clientY: 200 }));
      on.frames.splice(0).forEach((cb) => cb(0));
      expect(on.posted).toEqual([]);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("垫片负责把 dsh-pointer 还原成播放器自己的 mousemove", () => {
    const src = buildScenePlayerSrcdoc("blob:http://localhost/m");
    expect(src).toContain("dsh-pointer");
    expect(src).toContain("new MouseEvent('mousemove'");
  });
});
