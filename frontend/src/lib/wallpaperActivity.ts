import { useEffect, useState } from "react";

/** 交互停止多久后恢复壁纸动画。太短会在滚轮惯性期反复启停，太长会让壁纸看起来“卡住”。 */
export const WALLPAPER_INTERACTION_IDLE_MS = 180;

/** 窗口拖动/缩放由壳侧转发成这个事件（页面内收不到原生标题栏的 mousemove）。 */
export const WINDOW_MOVE_EVENT = "demiurge:window-move";

export interface WallpaperInteractionTracker {
  dispose(): void;
  isActive(): boolean;
}

/**
 * 跟踪“用户正在滚动/拖动”。只认会真正移动内容或窗口的输入：
 * 滚轮、滚动、按下按键时的指针移动、触摸。空手移动鼠标不触发，
 * 否则壁纸会跟着鼠标一直停。
 */
export function createWallpaperInteractionTracker(
  onChange: (active: boolean) => void,
  delayMs = WALLPAPER_INTERACTION_IDLE_MS,
  target: Window = window,
): WallpaperInteractionTracker {
  let timer = 0;
  let active = false;

  const mark = () => {
    if (!active) {
      active = true;
      onChange(true);
    }
    if (timer) target.clearTimeout(timer);
    timer = target.setTimeout(() => {
      timer = 0;
      active = false;
      onChange(false);
    }, delayMs);
  };

  const onPointerMove = (event: Event) => {
    if ((event as MouseEvent).buttons) mark();
  };
  const onWindowMove = () => mark();

  const options: AddEventListenerOptions = { passive: true, capture: true };
  target.addEventListener("wheel", mark, options);
  target.addEventListener("scroll", mark, options);
  target.addEventListener("resize", mark, options);
  target.addEventListener("pointerdown", mark, options);
  target.addEventListener("pointermove", onPointerMove, options);
  target.addEventListener("touchmove", mark, options);
  target.addEventListener(WINDOW_MOVE_EVENT, onWindowMove, options);

  return {
    isActive: () => active,
    dispose() {
      if (timer) target.clearTimeout(timer);
      timer = 0;
      const removeOptions: EventListenerOptions = { capture: true };
      target.removeEventListener("wheel", mark, removeOptions);
      target.removeEventListener("scroll", mark, removeOptions);
      target.removeEventListener("resize", mark, removeOptions);
      target.removeEventListener("pointerdown", mark, removeOptions);
      target.removeEventListener("pointermove", onPointerMove, removeOptions);
      target.removeEventListener("touchmove", mark, removeOptions);
      target.removeEventListener(WINDOW_MOVE_EVENT, onWindowMove, removeOptions);
    },
  };
}

/** true＝正在滚动/拖动；壁纸层据此暂停实时渲染，松手后自动恢复。 */
export function useWallpaperInteraction(delayMs = WALLPAPER_INTERACTION_IDLE_MS): boolean {
  const [interacting, setInteracting] = useState(false);
  useEffect(() => createWallpaperInteractionTracker(setInteracting, delayMs).dispose, [delayMs]);
  return interacting;
}
