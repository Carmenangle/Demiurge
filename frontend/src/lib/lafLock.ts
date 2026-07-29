// laf_lock 子帧（锁定的 ComfyUI 画布）postMessage 协议原语，单点。
// 四处（WorkflowCard/NodePickerModal/WorkflowTemplates/workflowOrchestration）此前各自
// 重造 URL 拼接、post 信封、来源守卫。握手时序各页不同故不强合并，仅收口这三个原语。

// 锁定画布的 iframe 地址：ComfyUI 带 ?laf_lock=1 进入受控模式。
export function lockUrl(comfyUrl: string): string {
  return `${comfyUrl.replace(/\/$/, "")}/?laf_lock=1`;
}

// 完整功能画布地址：ComfyUI 带 ?laf_full=1，保留全部原生交互 + 父页面双向同步协议。
// 用于 AI 搭工作流页右侧（load 写入整图 / request_api_prompt 读回画布）。
export function fullUrl(comfyUrl: string): string {
  return `${comfyUrl.replace(/\/$/, "")}/?laf_full=1`;
}

// 从 ComfyUI URL 推算 origin（用于 postMessage targetOrigin 和 ev.origin 校验）。
// ComfyUI 始终是本机 http，所以不需要 https 考虑。
// 返回 null 表示 URL 无效，调用方应退化为 "*"（仅在解析失败时）。
export function frameOrigin(comfyUrl: string | null | undefined): string | null {
  if (!comfyUrl) return null;
  try {
    const { origin } = new URL(comfyUrl.replace(/\/$/, ""));
    return origin === "null" ? null : origin;
  } catch {
    return null;
  }
}

// 向子帧发送一条 laf_lock 消息。
// comfyUrl 非空时用其 origin 作 targetOrigin，否则退化为 "*"。
export function postToFrame(win: Window | null | undefined, type: string, payload?: unknown, comfyUrl?: string): void {
  if (!win) return;
  const target = comfyUrl ? (frameOrigin(comfyUrl) ?? "*") : "*";
  win.postMessage({ target: "laf_lock", type, payload }, target);
}

// 是否是来自 laf_lock 子帧的消息；给 type 则同时校验类型。
export function isLafMessage(data: any, type?: string): boolean {
  if (!data || data.source !== "laf_lock") return false;
  return type === undefined || data.type === type;
}

export function isLafMessageFrom(
  event: MessageEvent,
  frameWindow: Window | null | undefined,
  type?: string,
): boolean {
  return !!frameWindow && event.source === frameWindow && isLafMessage(event.data, type);
}

// origin 感知版：同时校验 ev.origin，防止其他页面伪造消息。
// comfyUrl 非空时严格校验 origin；为空则只校验 source 与 data（宽松模式，保持向后兼容）。
export function isLafMessageFromStrict(
  event: MessageEvent,
  frameWindow: Window | null | undefined,
  comfyUrl: string | null | undefined,
  type?: string,
): boolean {
  if (!frameWindow || event.source !== frameWindow) return false;
  if (!isLafMessage(event.data, type)) return false;
  if (comfyUrl) {
    const expected = frameOrigin(comfyUrl);
    if (expected && event.origin !== expected) return false;
  }
  return true;
}
