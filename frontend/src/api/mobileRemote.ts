// 手机端联机同库（设置 → 离线资源同步）：配置手机本机后端的反向代理目标。
// 这组端点在 PC 后端不存在（`LAF_MOBILE_REMOTE=1` 才注册），只能从手机独立模式调用。
import { apiGet, apiPost } from "./client";

export interface MobileRemoteConfig {
  supported: boolean;
  enabled: boolean;
  /** enabled 且地址/令牌齐全（后端据此决定是否代理 /api/*）。 */
  active: boolean;
  base_url: string;
  token_set: boolean;
}

export interface MobileRemoteProbe {
  reachable: boolean;
  detail: string;
  latency_ms: number | null;
  health: Record<string, unknown> | null;
}

export function loadMobileRemote(): Promise<MobileRemoteConfig> {
  return apiGet<MobileRemoteConfig>("/mobile/remote");
}

/** 保存并探测；探测失败也会保存（用户可先填好、下次再连）。 */
export function saveMobileRemote(
  baseUrl: string,
  token: string,
  enabled: boolean,
): Promise<{ config: MobileRemoteConfig; probe: MobileRemoteProbe | null }> {
  return apiPost("/mobile/remote", { base_url: baseUrl, token, enabled });
}

export function testMobileRemote(): Promise<MobileRemoteProbe> {
  return apiPost("/mobile/remote/test", {});
}

/** 联机体检聚合（手机端「联机检测」弹窗的唯一数据源）。
 *  端点在 `/api/mobile` 前缀下 ⇒ 电脑掉线时它仍答得出原因（其余 `/api/*` 那时只会拿到代理的 502）。 */
export interface MobileOnlineStatus {
  /** 已启用联机且地址/令牌齐全；false 时后端不探测，弹窗也不该出现。 */
  active: boolean;
  base_url: string;
  /** 电脑后端可达（`/api/health` 有应答）。 */
  reachable: boolean;
  latency_ms: number | null;
  /** 电脑里存的那个 ComfyUI 地址（联机时地址真源在电脑，不是手机自己那份）。 */
  comfyui_url: string;
  comfyui_running: boolean;
  /** 断在哪、为什么；全绿时为空。 */
  detail: string;
}

/** 轮询接口必须带超时：后端最坏三跳（health → user-state → comfyui/status）各 4~6 秒。 */
export function getMobileOnlineStatus(): Promise<MobileOnlineStatus> {
  return apiGet<MobileOnlineStatus>("/mobile/online-status", 15000);
}

/** 在线同步（联机同库）：从电脑拉一份资源包导入本机。
 *  端点只在手机壳的本机后端注册（/api/mobile/* 永不被反向代理）。 */
export interface OnlineSyncStatus {
  state: "idle" | "downloading" | "importing" | "done" | "error";
  stage?: string;
  bytes?: number;
  total?: number;
  overwrite?: boolean;
  result?: import("./configTransfer").ConfigImportResult | null;
  error?: string;
  started_at?: number;
  finished_at?: number | null;
}

export function getOnlineSyncStatus(): Promise<OnlineSyncStatus> {
  return apiGet<OnlineSyncStatus>("/mobile/sync");
}

export function startOnlineSync(overwrite: boolean): Promise<OnlineSyncStatus> {
  return apiPost<OnlineSyncStatus>("/mobile/sync", { overwrite });
}
