// canvas/ToastLayer.tsx — Toast 弹窗层（2s 自动渐隐消失）
// position="fixed" 供非画布页面复用（资产库/仓库页等无 stageWrap 定位祖先的容器）。
import type { ToastItem } from "./CanvasTypes";

export function ToastLayer({ toasts, position = "absolute" }: { toasts: ToastItem[]; position?: "absolute" | "fixed" }) {
  if (toasts.length === 0) return null;
  return (
    <div className={`canvas-toast-container ${position === "fixed" ? "fixed" : ""}`}>
      {toasts.map((t) => (
        <div key={t.id} className={`canvas-toast ${t.kind === "info" ? "" : t.kind}`}>
          {t.msg}
        </div>
      ))}
    </div>
  );
}
