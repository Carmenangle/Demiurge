// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  WALLPAPER_INTERACTION_IDLE_MS,
  WINDOW_MOVE_EVENT,
  createWallpaperInteractionTracker,
} from "./wallpaperActivity";

const fire = (target: Window, type: string, init: EventInit = {}) =>
  target.dispatchEvent(new Event(type, init));

describe("壁纸交互追踪", () => {
  let tracker: ReturnType<typeof createWallpaperInteractionTracker> | null = null;
  let states: boolean[] = [];

  beforeEach(() => {
    vi.useFakeTimers();
    states = [];
  });

  afterEach(() => {
    tracker?.dispose();
    tracker = null;
    vi.useRealTimers();
  });

  const mount = (delayMs = WALLPAPER_INTERACTION_IDLE_MS) => {
    tracker = createWallpaperInteractionTracker((active) => states.push(active), delayMs);
    return tracker;
  };

  it("滚轮进入交互态，停手 180ms 后自动恢复", () => {
    mount();
    fire(window, "wheel");

    expect(states).toEqual([true]);
    expect(tracker!.isActive()).toBe(true);

    vi.advanceTimersByTime(WALLPAPER_INTERACTION_IDLE_MS - 1);
    expect(states).toEqual([true]);

    vi.advanceTimersByTime(1);
    expect(states).toEqual([true, false]);
    expect(tracker!.isActive()).toBe(false);
  });

  it("连续滚动只在开始/结束各通知一次，且每次事件都重置空闲计时", () => {
    mount();
    fire(window, "wheel");
    vi.advanceTimersByTime(150);
    fire(window, "wheel");
    vi.advanceTimersByTime(150);
    expect(states).toEqual([true]);          // 惯性滚动期间不闪回

    vi.advanceTimersByTime(WALLPAPER_INTERACTION_IDLE_MS - 150);
    expect(states).toEqual([true, false]);
  });

  it("滚动、按下、按住拖动、触摸与窗口拖动都算交互", () => {
    mount();
    for (const trigger of [
      () => fire(window, "scroll"),
      () => fire(window, "resize"),
      () => fire(window, "pointerdown"),
      () => window.dispatchEvent(new MouseEvent("pointermove", { buttons: 1 })),
      () => fire(window, "touchmove"),
      () => fire(window, WINDOW_MOVE_EVENT),
    ]) {
      states = [];
      trigger();
      expect(states, String(trigger)).toEqual([true]);
      vi.advanceTimersByTime(WALLPAPER_INTERACTION_IDLE_MS);
      expect(states).toEqual([true, false]);
    }
  });

  it("不按鼠标键的移动不算交互（避免鼠标划过就停壁纸）", () => {
    mount();
    window.dispatchEvent(new MouseEvent("pointermove", { buttons: 0 }));
    expect(states).toEqual([]);
    expect(tracker!.isActive()).toBe(false);
  });

  it("dispose 后不再响应事件，也不补发恢复通知", () => {
    const current = mount();
    fire(window, "wheel");
    expect(states).toEqual([true]);

    current.dispose();
    tracker = null;
    vi.advanceTimersByTime(WALLPAPER_INTERACTION_IDLE_MS * 2);
    expect(states).toEqual([true]);

    fire(window, "wheel");
    expect(states).toEqual([true]);
  });
});
