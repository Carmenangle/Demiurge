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
