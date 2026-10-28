// 手机端独立构建的运行形态开关与联机接线（M7）。
// 出包必须带 VITE_MOBILE_PROFILE=1（单一入口：mobile/spike/build-frontend-mobile.sh）；
// PC 构建不设 ⇒ 开关全关、零行为变化。
import { apiUrl } from "../api/client";
import { loadMobileRemote } from "../api/mobileRemote";

export const MOBILE_PROFILE = import.meta.env.VITE_MOBILE_PROFILE === "1";

/** 联机同库是否开着（store 水合前问一句「谁是真值」）。
 *  查不到配置时按「联机中」处理：宁可少写一次，也不拿本机快照覆盖电脑的仓库/模型。 */
let remoteActiveInFlight: Promise<boolean> | null = null;
export function remoteActiveOnce(): Promise<boolean> {
  if (!MOBILE_PROFILE) return Promise.resolve(false);
  if (!remoteActiveInFlight) {
    remoteActiveInFlight = loadMobileRemote()
      .then((c) => c.active)
      .catch(() => true)
      .finally(() => { remoteActiveInFlight = null; });
  }
  return remoteActiveInFlight;
}

/** 设置页保存 ComfyUI 地址时：把 PC 主机登记进后端白名单（运行时注册 + 落盘复播，
 *  下次开机由 mobile_server 自动播种）。仅手机端独立模式调用；失败静默——保存流程
 *  不因此中断（与 saveComfyConfig 同口径），登记失败的效果只是提交时 403，可重存。 */
export function syncMobileComfyHost(comfyUrl: string): void {
  if (!MOBILE_PROFILE) return;
  fetch(apiUrl("/mobile/comfy-host"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: comfyUrl }),
  }).catch(() => {});
}
