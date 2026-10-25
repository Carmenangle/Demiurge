import { useEffect, useReducer } from "react";
import { AlertTriangle, CheckCircle2, Loader2, X } from "lucide-react";
import { comfyStatus } from "../api/comfyui";

export type ComfyMonitorPhase = "checking" | "down" | "unknown" | "up";

export interface ComfyMonitorState {
  phase: ComfyMonitorPhase;
  /** 从「未启动/未知」变成「已启动」时的短暂提示；初始就绪不弹。 */
  upNotice: boolean;
  /** 用户点 X 后本轮不再打扰；掉线重新发生时自动复位。 */
  dismissed: boolean;
}

export const INITIAL_COMFY_MONITOR: ComfyMonitorState = {
  phase: "checking",
  upNotice: false,
  dismissed: false,
};

export type ComfyMonitorAction =
  | { type: "reset" }
  | { type: "probe"; running: boolean }
  | { type: "offline" }
  | { type: "notice-elapsed" }
  | { type: "dismiss" };

/** 轮询结果 → 浮层状态。纯函数，便于钉住「关闭后何时恢复提醒」的边界。 */
export function reduceComfyMonitor(state: ComfyMonitorState, action: ComfyMonitorAction): ComfyMonitorState {
  switch (action.type) {
    case "reset":
      return INITIAL_COMFY_MONITOR;
    case "probe": {
      if (!action.running) {
        // 只在新一轮掉线时复位关闭状态：关掉本轮提示后，同一次故障里不再反复弹。
        return state.phase === "down"
          ? { ...state, upNotice: false }
          : { phase: "down", upNotice: false, dismissed: false };
      }
      if (state.phase === "up") return state;
      const woke = state.phase === "down" || state.phase === "unknown";
      return {
        phase: "up",
        upNotice: woke && !state.dismissed,
        dismissed: false,
      };
    }
    case "offline":
      return { ...state, phase: "unknown", upNotice: false };
    case "notice-elapsed":
      return { ...state, upNotice: false };
    case "dismiss":
      return { ...state, upNotice: false, dismissed: true };
    default:
      return state;
  }
}

export function comfyMonitorVisible(state: ComfyMonitorState): boolean {
  if (state.dismissed) return false;
  return state.phase !== "up" || state.upNotice;
}

export function comfyMonitorView(phase: ComfyMonitorPhase): {
  tone: string;
  title: string;
  hint: string;
} {
  if (phase === "down") {
    return {
      tone: "down",
      title: "ComfyUI 未启动",
      hint: "图像生成暂不可用，检测到启动后自动收起",
    };
  }
  if (phase === "unknown") {
    return {
      tone: "unknown",
      title: "ComfyUI 状态未知",
      hint: "无法连接后端，正在重试…",
    };
  }
  if (phase === "up") {
    return {
      tone: "up",
      title: "ComfyUI 已启动",
      hint: "图像服务已就绪",
    };
  }
  return {
    tone: "checking",
    title: "正在检测 ComfyUI…",
    hint: "启动完成前会一直显示在这里",
  };
}

const PHASE_ICON = {
  checking: Loader2,
  down: AlertTriangle,
  unknown: AlertTriangle,
  up: CheckCircle2,
} as const;

export function ComfyStatusMonitor({
  comfyuiUrl,
  pollMs = 5000,
  upNoticeMs = 4000,
}: {
  comfyuiUrl: string;
  pollMs?: number;
  upNoticeMs?: number;
}) {
  const [state, dispatch] = useReducer(reduceComfyMonitor, INITIAL_COMFY_MONITOR);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    dispatch({ type: "reset" });
    const poll = async () => {
      try {
        const st = await comfyStatus(comfyuiUrl);
        if (!cancelled) dispatch({ type: "probe", running: Boolean(st?.running) });
      } catch {
        if (!cancelled) dispatch({ type: "offline" });
      }
      if (!cancelled) timer = setTimeout(poll, pollMs);
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [comfyuiUrl, pollMs]);

  useEffect(() => {
    if (!state.upNotice) return;
    const timer = setTimeout(() => dispatch({ type: "notice-elapsed" }), upNoticeMs);
    return () => clearTimeout(timer);
  }, [state.upNotice, upNoticeMs]);

  if (!comfyMonitorVisible(state)) return null;

  const view = comfyMonitorView(state.phase);
  const Icon = PHASE_ICON[state.phase];
  return (
    <div className={`comfy-monitor comfy-monitor--${view.tone}`} role="status" aria-live="polite">
      <Icon size={16} className="comfy-monitor__icon" aria-hidden />
      <div className="comfy-monitor__text">
        <strong>{view.title}</strong>
        <span>{view.hint}</span>
      </div>
      <button
        type="button"
        className="comfy-monitor__close"
        aria-label="关闭 ComfyUI 状态提示"
        title="关闭"
        onClick={() => dispatch({ type: "dismiss" })}
      >
        <X size={14} />
      </button>
    </div>
  );
}
