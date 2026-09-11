import { useCallback, useEffect, useRef, useState } from "react";
import {
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

  useEffect(() => {
    const onMove = (event: MouseEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const next = clampChatInputHeight(drag.startHeight + drag.startY - event.clientY);
      drag.height = next;
      setHeight(next);
    };
    const onUp = () => {
      const drag = dragRef.current;
      if (!drag) return;
      try { saveChatInputHeight(storage, drag.height); } catch { /* ignore */ }
      dragRef.current = null;
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
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

  return { height, beginResize, resizeByKey };
}
