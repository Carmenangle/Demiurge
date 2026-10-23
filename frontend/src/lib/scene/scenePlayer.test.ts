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
