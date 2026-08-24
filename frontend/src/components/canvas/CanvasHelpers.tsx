// canvas/CanvasHelpers.tsx — ReactFlow 内部辅助子组件（必须在 <ReactFlow> Provider 内渲染）
import { useEffect, useRef } from "react";
import { useReactFlow, useStore, useViewport } from "@xyflow/react";
import type { Guide } from "./CanvasTypes";

// ===== 一次性 fitView：等节点就绪后执行一次（解决"进入画布不在中心"）=====
// ★ 原 delay=80 版本在 gens 异步加载完成前就 fitView → 节点为空 → fitView 无效 →
//   之后节点出现但 fitView 已跑完 → 停在旧 viewport（不在中心）。
//   现在订阅 useStore(s => s.nodes.length)，节点首次非空才 fitView 一次（ref 防重复）。
export function InitFitView({ delay = 80 }: { delay?: number }) {
  const { fitView } = useReactFlow();
  const nodeCount = useStore((s) => s.nodes.length);
  const doneRef = useRef(false);
  useEffect(() => {
    if (doneRef.current || nodeCount === 0) return;
    doneRef.current = true;
    const t = setTimeout(() => {
      try { fitView({ duration: 0, padding: 0.12 }); } catch { /* ignore */ }
    }, delay);
    return () => clearTimeout(t);
  }, [nodeCount, delay, fitView]);
  return null;
}

// 桥接 useReactFlow 的 screenToFlowPosition 到主组件
export function FlowBridge({ onReady }: { onReady: (fn: (p: { x: number; y: number }) => { x: number; y: number }) => void }) {
  const { screenToFlowPosition } = useReactFlow();
  useEffect(() => { onReady(screenToFlowPosition); }, [onReady, screenToFlowPosition]);
  return null;
}

// 实时订阅 viewport → 主组件 ref：fitView/panning/zoom 都同步，
// 否则 GuidesOverlay 屏幕坐标换算基于过期 viewport 会偏位到看不见
//（useViewport 只能在 ReactFlow 子组件内调用——同 useReactFlow 限制）。
export function ViewportBridge({ onViewport }: { onViewport: (vp: { x: number; y: number; zoom: number }) => void }) {
  const viewport = useViewport();
  useEffect(() => {
    onViewport({ x: viewport.x, y: viewport.y, zoom: viewport.zoom });
  }, [viewport.x, viewport.y, viewport.zoom, onViewport]);
  return null;
}

// ===== 实时吸附辅助线 overlay（CSS div + border dashed，渲染最可靠）=====
// 用户拍板：中心线只在「选中节点 + 拖拽 + 其余节点中心对齐」时出现，贯穿整画布。
// snap=true（吸附贴住）：蓝色虚线（dashed）+ 光晕；
// snap=false（接近提示）：更细的浅蓝虚线（1px）。
// ★ 用 CSS border 而非 SVG：SVG filter 会因 id 冲突整个 line 消失（历史根因），
//   CSS border dashed 不依赖 SVG 坐标系/filter，任何浏览器都可靠渲染。
export function GuidesOverlay({ guides, viewport }: { guides: Guide; viewport: { x: number; y: number; scale: number } }) {
  if (guides.x === undefined && guides.y === undefined) return null;
  const { x: vx, y: vy, scale: zoom } = viewport;
  const boxShadow = "0 0 6px rgba(59,130,246,0.8), 0 0 2px rgba(59,130,246,1)";
  return (
    <div style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: 2147483000, overflow: "visible" }}>
      {guides.x !== undefined && (
        <div
          style={{
            position: "absolute",
            left: guides.x * zoom + vx,
            top: 0,
            bottom: 0,
            width: 0,
            borderLeft: guides.snapX
              ? "2px dashed #3b82f6"
              : "1px dashed #60a5fa",
            opacity: guides.snapX ? 1 : 0.6,
            boxShadow: guides.snapX ? boxShadow : undefined,
          }}
        />
      )}
      {guides.y !== undefined && (
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            top: guides.y * zoom + vy,
            height: 0,
            borderTop: guides.snapY
              ? "2px dashed #3b82f6"
              : "1px dashed #60a5fa",
            opacity: guides.snapY ? 1 : 0.6,
            boxShadow: guides.snapY ? boxShadow : undefined,
          }}
        />
      )}
    </div>
  );
}