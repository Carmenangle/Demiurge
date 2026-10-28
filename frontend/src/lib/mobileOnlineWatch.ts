// 手机端「联机检测」的判定与文案（纯逻辑，组件只做展示）。
//
// 需求口径（2026-10-27 用户定案）：电脑端有 ComfyUI 状态横幅，手机端对应物是一个
// **联机检测弹窗**——前提是「填了电脑地址并开始联机」，连着就绝不出现，中断了才跳出。
// 数据只来自 `/api/mobile/online-status`（`/api/mobile` 前缀永不被反向代理，所以电脑
// 关机/换网时这条仍答得出原因；那时其余 `/api/*` 只剩代理的 502）。
import type { MobileOnlineStatus } from "../api/mobileRemote";

export type OnlineWatchPhase =
  | "checking"    // 首轮还没回来
  | "off"         // 没启用联机：不检测也不弹
  | "linked"      // 电脑可达且电脑的 ComfyUI 开着
  | "pc_down"     // 联机开着，但电脑没应答
  | "comfy_down"; // 电脑在线，但它那个地址上的 ComfyUI 没开着

export interface OnlineWatchState {
  phase: OnlineWatchPhase;
  /** 后端给的原因（已含 ComfyUI 地址这类事实，前端不自拼）。 */
  detail: string;
  /** 电脑地址：中断时要点名「连的是哪台机器」，否则用户无从下手。 */
  target: string;
  /** 点「知道了」后同一次故障不再重复弹；下一次新故障沿自动复位（与 PC 横幅同纪律）。 */
  dismissed: boolean;
}

export const INITIAL_ONLINE_WATCH: OnlineWatchState = {
  phase: "checking", detail: "", target: "", dismissed: false,
};

export type OnlineWatchAction =
  | { type: "probe"; status: MobileOnlineStatus }
  | { type: "local-error" }
  | { type: "dismiss" };

function phaseOf(status: MobileOnlineStatus): { phase: OnlineWatchPhase; detail: string } {
  if (!status.reachable) {
    return { phase: "pc_down", detail: status.detail || "电脑没有应答" };
  }
  if (!status.comfyui_running) {
    return { phase: "comfy_down", detail: status.detail || "电脑上的 ComfyUI 没有应答" };
  }
  return { phase: "linked", detail: "" };
}

export function reduceOnlineWatch(
  state: OnlineWatchState,
  action: OnlineWatchAction,
): OnlineWatchState {
  switch (action.type) {
    case "probe": {
      const { status } = action;
      if (!status.active) return { ...INITIAL_ONLINE_WATCH, phase: "off" };
      const next = phaseOf(status);
      if (next.phase === "linked") return { ...INITIAL_ONLINE_WATCH, phase: "linked" };
      // 同一次故障里已经点过「知道了」⇒ 保持静默；原因变了（电脑断了 → 只是 ComfyUI 断了）
      // 算一次新故障，重新提醒——用户需要知道断点换了。
      if (state.dismissed && state.phase === next.phase) return state;
      return { phase: next.phase, detail: next.detail, target: status.base_url, dismissed: false };
    }
    case "local-error":
      // 连手机自己的探针都问不通（后端在重启等）：保持原状，不制造假警。
      return state;
    case "dismiss":
      return { ...state, dismissed: true };
    default:
      return state;
  }
}

export function onlineWatchVisible(state: OnlineWatchState): boolean {
  if (state.dismissed) return false;
  return state.phase === "pc_down" || state.phase === "comfy_down";
}

export function onlineWatchView(state: OnlineWatchState): { title: string; message: string } {
  if (state.phase === "pc_down") {
    return {
      title: "与电脑的联机中断了",
      message: `${state.detail}（电脑地址：${state.target}）。`
        + "在电脑上把后端开机、确认同一 Wi‑Fi，或在「设置 → 离线资源同步」断开联机回到本机数据。",
    };
  }
  return {
    title: "电脑在线，但 ComfyUI 没开着",
    message: `${state.detail}。`
      + "手机上的生成请求由电脑执行：在电脑上启动 ComfyUI（或把它的地址改成正在运行的那个），"
      + "这里会自动恢复。",
  };
}
