import { useEffect, useRef, useState } from "react";

// 全屏看图：点击任意缩略图触发 'lightbox' 事件，由根部的 Lightbox 监听显示。
// 2026-10-16：加滚轮缩放（以鼠标为焦点）/ 放大后拖拽平移 / 双击复位，与市场大图交互一致。
export function openLightbox(url: string) {
  window.dispatchEvent(new CustomEvent("lightbox", { detail: url }));
}

type View = { scale: number; x: number; y: number };
const RESET: View = { scale: 1, x: 0, y: 0 };

export function Lightbox() {
  const [url, setUrl] = useState<string | null>(null);
  const [view, setView] = useState<View>(RESET);
  const maskRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ sx: number; sy: number; vx: number; vy: number } | null>(null);
  const movedRef = useRef(false);

  useEffect(() => {
    const h = (e: Event) => {
      setUrl((e as CustomEvent).detail as string);
      setView(RESET); // 每次打开复位视图
    };
    window.addEventListener("lightbox", h);
    return () => window.removeEventListener("lightbox", h);
  }, []);

  useEffect(() => {
    if (!url) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setUrl(null);
      else if (e.key === "0") setView(RESET); // 0 键复位缩放
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [url]);

  // 滚轮缩放（以鼠标位置为焦点）：React 的 onWheel 是 passive，必须原生监听才能 preventDefault
  useEffect(() => {
    const el = maskRef.current;
    if (!el || !url) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const cx = e.clientX - (rect.left + rect.width / 2);
      const cy = e.clientY - (rect.top + rect.height / 2);
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      setView((v) => {
        const next = Math.min(8, Math.max(0.3, v.scale * factor));
        const k = next / v.scale;
        return { scale: next, x: cx - (cx - v.x) * k, y: cy - (cy - v.y) * k };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [url]);

  if (!url) return null;
  return (
    <div
      ref={maskRef}
      className="lightbox-mask"
      style={{ cursor: view.scale > 1 ? "grab" : "zoom-out" }}
      title="滚轮缩放 · 放大后拖拽平移 · 双击复位 · ESC / 点击背景关闭"
      onClick={() => {
        // 拖拽结束的那一次 click 不算「点背景」，否则移动图片会误关闭
        if (movedRef.current) {
          movedRef.current = false;
          return;
        }
        setUrl(null);
      }}
      onMouseDown={(e) => {
        if (view.scale <= 1) return;
        dragRef.current = { sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y };
        movedRef.current = false;
      }}
      onMouseMove={(e) => {
        const d = dragRef.current;
        if (!d) return;
        const dx = e.clientX - d.sx;
        const dy = e.clientY - d.sy;
        if (Math.abs(dx) + Math.abs(dy) > 3) movedRef.current = true;
        setView((v) => ({ ...v, x: d.vx + dx, y: d.vy + dy }));
      }}
      onMouseUp={() => { dragRef.current = null; }}
      onDoubleClick={() => setView(RESET)}
    >
      <img
        src={url}
        alt="大图"
        className="lightbox-img"
        draggable={false}
        onClick={(e) => e.stopPropagation()}
        style={{
          transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`,
          willChange: "transform",
        }}
      />
    </div>
  );
}
