// ComfyUI 实时进度：默认本机回环直连 ComfyUI 原生 /ws；非回环目标经同源后端中继
// （原因见下方 CSP 注释）。提交时把 clientId 回传给后端 → ComfyUI 只把该任务进度
// 推给同 clientId 的连接。
// 只负责“进度显示”，完成判定仍以 /history 轮询为准（WS 可能断，轮询是可靠兜底）。

import { isLoopbackComfyUrl } from "./comfyMachines";

// 稳定的浏览器会话 clientId：整页共用一个，提交与 WS 连接用同一个才能对上。
let cachedClientId = "";
export function comfyClientId(): string {
  if (cachedClientId) return cachedClientId;
  try {
    const k = "laf_comfy_client_id";
    let v = localStorage.getItem(k) || "";
    if (!v) { v = crypto.randomUUID(); localStorage.setItem(k, v); }
    cachedClientId = v;
  } catch {
    cachedClientId = crypto.randomUUID();
  }
  return cachedClientId;
}

// 进度 WS 走哪条路：
// - 回环明文（默认本机 ComfyUI）→ 直连，PC 构建行为逐字不变；
// - 其余目标（云端 https/wss、局域网机台）→ 同源后端中继。后端 CSP `connect-src`
//   只放行 `ws://` 回环，浏览器直连非回环目标会被 CSP 拦掉（表现为全程无进度、完成
//   判定仍靠轮询）；Android 明文配置也只放行回环且只约束 WebView 栈。中继走 Python
//   socket，不受两者约束（实现：backend/app/services/comfyui_ws_proxy.py，目标地址按
//   comfy_hosts 白名单校验）。手机代理构建（VITE_COMFY_PROGRESS_PROXY=1）恒走中继。
const PROGRESS_PROXY = import.meta.env.VITE_COMFY_PROGRESS_PROXY === "1";

function canConnectDirect(comfyUrl: string): boolean {
  if (!isLoopbackComfyUrl(comfyUrl)) return false;
  return !/^(https|wss):\/\//i.test(comfyUrl.trim());
}

// 把 http(s):// 的 ComfyUI 地址转成 ws(s):// 的 /ws 地址（代理模式下转同源后端端点，
// 目标 ComfyUI 地址以 url 参数携带，由后端按白名单校验后中继）
export function wsUrl(comfyUrl: string, clientId: string): string {
  if (PROGRESS_PROXY || !canConnectDirect(comfyUrl)) {
    const self = location.origin.replace(/^http/i, "ws");
    return `${self}/api/comfyui/ws?clientId=${encodeURIComponent(clientId)}`
      + `&url=${encodeURIComponent(comfyUrl.trim())}`;
  }
  let u = comfyUrl.trim();
  if (!/^https?:\/\//i.test(u)) u = "http://" + u;
  u = u.replace(/\/$/, "");
  const ws = u.replace(/^http/i, "ws");
  return `${ws}/ws?clientId=${encodeURIComponent(clientId)}`;
}

export interface Progress {
  value: number;      // 当前步（采样步或节点序）
  max: number;        // 总步
  node?: string;      // 正在执行的节点 id
}

// 订阅某 prompt 的进度。onProgress 收到 0~100 的百分比与原始步数；onNode 收到节点切换。
// 返回停止函数（关闭 WS）。WS 建立失败/断开都静默——完成判定另有轮询兜底。
export function subscribeProgress(
  comfyUrl: string,
  promptId: string,
  cbs: {
    onProgress?: (pct: number, p: Progress) => void;
    onNode?: (nodeId: string) => void;   // 当前正在执行的节点 id（切换时触发）
    onDone?: () => void;
  },
): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let reconnected = false;   // 只重连一次，避免任务结束后无限重连

  const handle = (ev: MessageEvent) => {
    if (closed || typeof ev.data !== "string") return;
    let msg: { type?: string; data?: Record<string, unknown> };
    try { msg = JSON.parse(ev.data); } catch { return; }
    const d = msg.data || {};
    // 只认属于本任务的消息（ComfyUI 进度消息带 prompt_id；早期版本 progress 无 prompt_id 则放行）
    const pid = d.prompt_id as string | undefined;
    if (pid && pid !== promptId) return;
    if (msg.type === "progress") {
      const value = Number(d.value ?? 0);
      const max = Number(d.max ?? 0);
      const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
      cbs.onProgress?.(pct, { value, max, node: d.node as string | undefined });
    } else if (msg.type === "executing" && pid === promptId) {
      if (d.node == null) {
        cbs.onDone?.();   // node=null 表示该 prompt 执行结束
      } else {
        cbs.onNode?.(String(d.node));  // 节点切换：当前正在执行此节点
      }
    }
  };

  const connect = () => {
    try {
      ws = new WebSocket(wsUrl(comfyUrl, comfyClientId()));
    } catch {
      return;
    }
    ws.onmessage = handle;
    ws.onerror = () => { /* 静默：轮询兜底 */ };
    // 非主动关闭时断线：一次性重连（长任务中途断网仍能恢复进度；完成判定另有轮询兜底）
    ws.onclose = () => {
      if (closed || reconnected) return;
      reconnected = true;
      setTimeout(() => { if (!closed) connect(); }, 1000);
    };
  };
  connect();

  return () => {
    closed = true;
    try { ws?.close(); } catch { /* ignore */ }
  };
}
