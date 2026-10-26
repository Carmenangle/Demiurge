// 手机端独立构建的运行形态开关与联机接线（M7）。
// 出包必须带 VITE_MOBILE_PROFILE=1（单一入口：mobile/spike/build-frontend-mobile.sh）；
// PC 构建不设 ⇒ 开关全关、零行为变化。
import { apiUrl } from "../api/client";

export const MOBILE_PROFILE = import.meta.env.VITE_MOBILE_PROFILE === "1";

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
