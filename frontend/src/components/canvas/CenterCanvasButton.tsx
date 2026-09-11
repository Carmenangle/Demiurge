// canvas/CenterCanvasButton.tsx — ReactFlow 内部「回到画布中心」按钮
// 渲染为 <Controls> 面板内的 <ControlButton>（必须是 <Controls> 的 children），
// 与缩放/适配/锁定按钮同一款式（react-flow__controls-button）、同一图层。
// useReactFlow 需要 Provider 上下文，本组件仍须在 <ReactFlow> 内渲染。
// 有 newNodeIds 时以新节点为中心 fitView；否则全量 fitView。
import { useReactFlow, ControlButton } from "@xyflow/react";
import { Crosshair } from "lucide-react";

export function CenterCanvasButton({ newNodeIds }: { newNodeIds?: string[] }) {
  const { fitView, getNodes } = useReactFlow();
  return (
    <ControlButton
      type="button"
      onClick={() => {
        try {
          if (newNodeIds && newNodeIds.length > 0) {
            const targets = getNodes().filter((n) => newNodeIds.includes(n.id));
            if (targets.length > 0) {
              fitView({ nodes: targets, duration: 300, padding: 0.18 });
              return;
            }
          }
          fitView({ duration: 300, padding: 0.12 });
        } catch { /* ignore */ }
      }}
      title="回到画布中心"
      aria-label="回到画布中心"
    >
      <Crosshair size={15} />
    </ControlButton>
  );
}
