// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { RepoCover } from "./RepoViews";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeAll(() => {
  // jsdom 没有媒体解码：把 play/pause 打桩，测试只验渲染与错误回退。
  HTMLMediaElement.prototype.play = vi.fn(async () => {});
  HTMLMediaElement.prototype.pause = vi.fn();
});

let container: HTMLDivElement;
let root: Root;

function mount(src?: string) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => { root.render(<RepoCover src={src} name="作品甲" />); });
}

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
});

describe("RepoCover 视频动态封面", () => {
  it("图片封面仍用 img", () => {
    mount("/local-view?path=a.png");
    expect(container.querySelector("img")).toBeTruthy();
    expect(container.querySelector("video")).toBeNull();
    expect(container.textContent).not.toContain("动态");
  });

  it("视频直链与 local-view 视频地址都渲染静音循环 video", () => {
    mount("http://x/v.mp4");
    const video = container.querySelector("video") as HTMLVideoElement;
    expect(video).toBeTruthy();
    expect(video.muted).toBe(true);
    expect(video.loop).toBe(true);
    expect(video.getAttribute("preload")).toBe("metadata");
    expect(video.getAttribute("src")).toBe("http://x/v.mp4");
    expect(container.textContent).toContain("动态");
  });

  it("local-view 查询串里的视频也识别为动态封面", () => {
    mount("/local-view?path=D%3A%5Cwork%5Cv.mp4");
    expect(container.querySelector("video")).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
  });

  it("视频加载失败回退「暂无封面」占位", () => {
    mount("http://x/dead.mp4");
    const video = container.querySelector("video")!;
    act(() => { video.dispatchEvent(new Event("error", { bubbles: true })); });
    expect(container.querySelector("video")).toBeNull();
    expect(container.textContent).toContain("暂无封面");
  });
});
