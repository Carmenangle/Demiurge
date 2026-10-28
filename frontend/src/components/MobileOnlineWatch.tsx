// 手机端联机检测弹窗：联机中断（电脑没应答 / 电脑在线但它的 ComfyUI 没开着）时跳出提示。
//
// 电脑端的对应物是 `ComfyStatusMonitor` 那条常驻横幅；手机上横幅被明确隐藏（用户不要占屏），
// 改成「连着不出现、断了才弹」的弹窗。轮询纪律照抄 PC 横幅：setTimeout 链、请求失败不断链、
// 关掉本轮提示后同一次故障不再重复弹。
import { useEffect, useReducer } from "react";
import { getMobileOnlineStatus } from "../api/mobileRemote";
import { MOBILE_PROFILE } from "../lib/mobileProfile";
import { notifyUserStateReconnect, userStateFellBack } from "../lib/userStateSync";
import {
  INITIAL_ONLINE_WATCH,
  onlineWatchView,
  onlineWatchVisible,
  reduceOnlineWatch,
} from "../lib/mobileOnlineWatch";
import { AlertModal } from "./Modal";

export function MobileOnlineWatch({ pollMs = 5000 }: { pollMs?: number }) {
  const [state, dispatch] = useReducer(reduceOnlineWatch, INITIAL_ONLINE_WATCH);

  useEffect(() => {
    // PC 构建不轮询：/api/mobile/* 这组端点只在手机独立模式注册（LAF_MOBILE_REMOTE=1）。
    if (!MOBILE_PROFILE) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      try {
        const status = await getMobileOnlineStatus();
        if (!cancelled) {
          dispatch({ type: "probe", status });
          // 断线期间两 store 保持「未水合」（不回写电脑）。电脑回来、或用户退出联机改由
          // 本机后端应答时，重新拉一次存档把界面换成真值。
          if (userStateFellBack() && (!status.active || status.reachable)) notifyUserStateReconnect();
        }
      } catch {
        if (!cancelled) dispatch({ type: "local-error" });
      }
      if (!cancelled) timer = setTimeout(poll, pollMs);
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [pollMs]);

  if (!MOBILE_PROFILE || !onlineWatchVisible(state)) return null;
  const view = onlineWatchView(state);
  return (
    <AlertModal
      title={view.title}
      message={view.message}
      onClose={() => dispatch({ type: "dismiss" })}
    />
  );
}
