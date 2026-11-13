// ComfyUI 就绪等待队列（2026-11-10 用户口径）：
// 刚重启后端 / 进入项目时 ComfyUI 可能还没启动。历史行为是「探活失败 → 直接判死，
// 任务不再生成」；这里改成：所有依赖 ComfyUI 的任务先排进同一个等待队列，
// 能自动拉起就自动拉起，不能就等用户手动启动；就绪后大家按顺序继续提交。
// 真源分工：探活/启动仍归 api/comfyui；本模块只做「等待、共享一次启动、状态广播」。
import { comfyStatus, probeComfyHosts, startComfy } from "../api/comfyui";

export interface ComfyWaitState {
  /** 是否有任务正在等待 ComfyUI。 */
  waiting: boolean;
  /** 是否已经发起自动启动。 */
  starting: boolean;
  /** 面向用户的说明（可直接显示在按钮/提示里）。 */
  message: string;
}

type Listener = (state: ComfyWaitState) => void;

const listeners = new Set<Listener>();
let state: ComfyWaitState = { waiting: false, starting: false, message: "" };
let autoStartPath = "";
let autoStartPython = "";
// 同一条 ComfyUI 地址只保留一个等待 Promise：并发任务共享一次启动与轮询，
// 就绪后各自继续提交（ComfyUI 自己的 FIFO 队列负责排序）。
let activeWait: Promise<boolean> | null = null;
// 刚探活成功后的短缓存：一键补齐会连续提交十几条，不必每条都再探一次。
let readyUntil = 0;

function emit(next: ComfyWaitState): void {
  state = next;
  for (const listener of listeners) {
    try {
      listener(state);
    } catch {
      // 监听方异常不得影响等待链。
    }
  }
}

/** 由 ChatView（持有设置）注入 ComfyUI 启动路径；空路径＝只等待，不自动启动。 */
export function configureComfyAutoStart(path: string, python = ""): void {
  autoStartPath = (path || "").trim();
  autoStartPython = (python || "").trim();
}

export function getComfyWaitState(): ComfyWaitState {
  return state;
}

export function subscribeComfyWait(listener: Listener): () => void {
  listeners.add(listener);
  listener(state);
  return () => { listeners.delete(listener); };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => { globalThis.setTimeout(resolve, ms); });
}

export interface EnsureComfyReadyOptions {
  /** 最长等待时间；超时返回 false（调用方决定如何提示/排队）。 */
  timeoutMs?: number;
  /** 轮询间隔，测试可调小。 */
  pollMs?: number;
  /** 状态回调：调用方用来把「已排队 / 正在启动」显示在按钮附近。 */
  onState?: (state: ComfyWaitState) => void;
}

async function waitForComfy(
  url: string,
  options: EnsureComfyReadyOptions,
): Promise<boolean> {
  const timeoutMs = Math.max(0, options.timeoutMs ?? 10 * 60 * 1000);
  const pollMs = Math.max(50, options.pollMs ?? 2500);
  const startedAt = Date.now();
  let startAttempted = false;
  let announced = false;

  const publish = (next: ComfyWaitState) => {
    emit(next);
    options.onState?.(next);
  };

  while (Date.now() - startedAt < timeoutMs) {
    try {
      const status = await comfyStatus(url);
      if (status.running) {
        readyUntil = Date.now() + 5000;
        publish({ waiting: false, starting: false, message: "" });
        return true;
      }
    } catch {
      // 后端刚重启/网络未就绪：继续等下一轮。
    }

    // 只有第一次探活确认没启动后才亮「已排队」，避免 ComfyUI 明明在跑时闪一下排队文案。
    if (!announced) {
      announced = true;
      publish({
        waiting: true,
        starting: false,
        message: "ComfyUI 未启动，任务已排队…",
      });
    }

    if (!startAttempted && autoStartPath) {
      startAttempted = true;
      publish({
        waiting: true,
        starting: true,
        message: "ComfyUI 正在启动，任务已排队…",
      });
      try {
        await startComfy(autoStartPath, url, autoStartPython);
      } catch {
        // 启动失败不丢任务：继续轮询，等用户手动启动或下次重试。
      }
    }

    if (!startAttempted) {
      publish({
        waiting: true,
        starting: false,
        message: "ComfyUI 未启动，任务已排队；启动后自动提交…",
      });
    }
    await sleep(pollMs);
  }

  publish({
    waiting: false,
    starting: false,
    message: "ComfyUI 启动超时，任务未提交",
  });
  return false;
}

/**
 * 等待 ComfyUI 就绪；并发调用共享同一次等待与自动启动。
 * 返回 false 表示超时（调用方应保留任务/提示稍后重试，而不是把它当普通失败删掉）。
 */
export async function ensureComfyReady(
  url: string,
  options: EnsureComfyReadyOptions = {},
): Promise<boolean> {
  if (Date.now() < readyUntil) return true;
  if (activeWait) {
    // 后来者共享同一次启动，但仍要看到「已排队 / 正在启动」的状态。
    const unsubscribe = options.onState ? subscribeComfyWait(options.onState) : () => {};
    try {
      return await activeWait;
    } finally {
      unsubscribe();
    }
  }
  activeWait = waitForComfy(url, options).finally(() => {
    activeWait = null;
  });
  return activeWait;
}

export interface ComfyIdleState {
  running: number;
  pending: number;
}

export interface WaitForComfyIdleOptions {
  /** 最长等待时间；超时返回 false（调用方降级成一句短提示）。 */
  timeoutMs?: number;
  /** 轮询间隔，测试可调小。 */
  pollMs?: number;
  /** 队列仍忙时的状态回调。 */
  onWait?: (state: ComfyIdleState) => void;
  /** 取消检查：返回 true 立即结束等待（用户取消拍摄/切走）。 */
  shouldStop?: () => boolean;
}

/**
 * 等 ComfyUI 队列清空（running/pending 都为 0）再重拍。
 * 探活失败或队列仍忙都继续等；只有队列真正空闲才返回 true。
 */
export async function waitForComfyIdle(
  url: string,
  options: WaitForComfyIdleOptions = {},
): Promise<boolean> {
  const timeoutMs = Math.max(0, options.timeoutMs ?? 10 * 60 * 1000);
  const pollMs = Math.max(50, options.pollMs ?? 5000);
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (options.shouldStop?.()) return false;
    try {
      const first = (await probeComfyHosts([url])).items?.[0];
      if (first?.online && first.running === 0 && first.pending === 0) return true;
      options.onWait?.({ running: first?.running ?? 0, pending: first?.pending ?? 0 });
    } catch {
      // 后端/ComfyUI 暂不可达：继续等下一轮，超时由调用方降级。
    }
    await sleep(pollMs);
  }
  return false;
}

/** 测试/排障用：清空等待链与自动启动配置。 */
export function resetComfyReadinessForTests(): void {
  listeners.clear();
  state = { waiting: false, starting: false, message: "" };
  autoStartPath = "";
  autoStartPython = "";
  activeWait = null;
  readyUntil = 0;
}
