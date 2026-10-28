import { useCallback, useEffect, useRef, useState } from "react";
import {
  DEFAULT_CHAT_INPUT_HEIGHT,
  clampChatInputHeight,
  loadChatInputHeight,
  saveChatInputHeight,
} from "./contextManagement";

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function useResizableChatInput(storage: StorageLike = localStorage) {
  const [height, setHeight] = useState(() => loadChatInputHeight(storage));
  const dragRef = useRef<{ startY: number; startHeight: number; height: number } | null>(null);
  const pendingRef = useRef<number | null>(null);
  const rafRef = useRef(0);

  useEffect(() => {
    const flush = () => {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = 0;
      }
      if (pendingRef.current != null) {
        setHeight(pendingRef.current);
        pendingRef.current = null;
      }
    };
    const onMove = (event: MouseEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      drag.height = clampChatInputHeight(drag.startHeight + drag.startY - event.clientY);
      pendingRef.current = drag.height;
      // 高频鼠标每帧会产生多次 mousemove：合并成每帧一次 setState，
      // 否则会话视图会跟着指针事件频率重渲染，拖拽手感发飘。
      if (rafRef.current) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = 0;
        if (pendingRef.current != null) {
          setHeight(pendingRef.current);
          pendingRef.current = null;
        }
      });
    };
    const onUp = () => {
      const drag = dragRef.current;
      if (!drag) return;
      flush();
      try { saveChatInputHeight(storage, drag.height); } catch { /* ignore */ }
      dragRef.current = null;
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
      pendingRef.current = null;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [storage]);

  const beginResize = useCallback((clientY: number) => {
    dragRef.current = { startY: clientY, startHeight: height, height };
  }, [height]);

  const resizeByKey = useCallback((key: string): boolean => {
    if (key !== "ArrowUp" && key !== "ArrowDown") return false;
    const next = clampChatInputHeight(height + (key === "ArrowUp" ? 8 : -8));
    setHeight(next);
    try { saveChatInputHeight(storage, next); } catch { /* ignore */ }
    return true;
  }, [height, storage]);

  // 双击手柄回默认高度（不新增设置项）：清掉 localStorage 里的历史拖拽值。
  const resetHeight = useCallback(() => {
    setHeight(DEFAULT_CHAT_INPUT_HEIGHT);
    try { saveChatInputHeight(storage, DEFAULT_CHAT_INPUT_HEIGHT); } catch { /* ignore */ }
  }, [storage]);

  return { height, beginResize, resizeByKey, resetHeight };
}
