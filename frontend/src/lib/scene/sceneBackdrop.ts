/**
 * 场景壁纸实时渲染的编排层：取包字节 → Worker 解码 → blob 纹理 → 挂载隔离播放器。
 *
 * 线程分工是刻意的：
 * - 主线程只做 HTTP 与 blob URL（生命周期跟着宿主组件走，卸载即 revoke，不漏内存）；
 * - 解码（PKGV/LZ4/TEX + PNG 重编码）全在 Worker，100MB 级包不能碰主线程；
 * - 渲染在同源 blob: 文档 iframe，播放器崩溃只回落静态预览，不带倒整个应用。
 */
import type { LiquidGlassFit } from "../../stores/settings";
import { fetchWallpaperScenePkg } from "../../api/userState";
import { rewriteSceneResourceUrls } from "./decodeScenePackage";
import type { SceneDecodeRequest, SceneDecodeResponse, SceneDecodeSuccess } from "./sceneWorker";
import type { WeSceneManifest } from "./vendor/we-scene-manifest";
import { mountScenePlayer, type ScenePlayerHandle, type WeSceneFit } from "./scenePlayer";

/** 解码结果换成 blob 之后的句柄：manifestJson 可直接喂播放器，revoke 负责收尾。 */
export interface SceneResources {
  manifestJson: string;
  textureCount: number;
  elapsedMs: number;
  pkgBytes: number;
  revoke(): void;
}

export type SceneBackdropState =
  | { status: "loading" }
  | { status: "ready"; textureCount: number; elapsedMs: number; pkgBytes: number }
  | { status: "fallback"; reason: string };

export interface SceneBackdropMount {
  update(next: { fit: LiquidGlassFit; paused: boolean }): void;
  dispose(): void;
}

let worker: Worker | null = null;
let seq = 0;
const pending = new Map<number, { resolve(v: SceneDecodeSuccess): void; reject(e: Error): void }>();

function settleAll(error: Error): void {
  for (const settle of pending.values()) settle.reject(error);
  pending.clear();
}

/** 懒建单个共享 Worker：解码是串行的重活，开多个只会抢同一份 CPU。 */
function decodeWorker(): Worker {
  if (worker) return worker;
  const next = new Worker(new URL("./sceneWorker.ts", import.meta.url), { type: "module" });
  next.onmessage = (event: MessageEvent<SceneDecodeResponse>) => {
    const settle = pending.get(event.data.id);
    if (!settle) return;
    pending.delete(event.data.id);
    if (event.data.ok) settle.resolve(event.data);
    else settle.reject(new Error(event.data.error));
  };
  // Worker 被 OOM 之类打死时不会有 onmessage；必须让等待方拿到明确失败，否则壁纸层永远停在 loading。
  next.onerror = (event) => {
    settleAll(new Error(`场景解码线程异常退出：${event.message || "未知原因"}`));
    next.terminate();
    if (worker === next) worker = null;
  };
  worker = next;
  return worker;
}

export function decodeSceneInWorker(pkgBytes: ArrayBuffer): Promise<SceneDecodeSuccess> {
  return new Promise<SceneDecodeSuccess>((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    try {
      const request: SceneDecodeRequest = { id, pkg: pkgBytes };
      decodeWorker().postMessage(request, [pkgBytes]);
    } catch (error) {
      pending.delete(id);
      reject(error instanceof Error ? error : new Error(String(error)));
    }
  });
}

/** 把解码出来的纹理字节换成 blob URL，并产出一份可直接渲染的清单 JSON。 */
export function createSceneResources(decoded: SceneDecodeSuccess, pkgBytes: number): SceneResources {
  const urls = new Map<string, string>();
  for (const [path, bytes] of Object.entries(decoded.resources)) {
    urls.set(path, URL.createObjectURL(new Blob([bytes], { type: "image/png" })));
  }
  const manifest = decoded.manifest as unknown as WeSceneManifest;
  const rewritten = rewriteSceneResourceUrls(manifest, (path) => urls.get(path) ?? null);
  // 播放器读的是上游路由的响应体，形状必须是信封 `{ok, manifest}`（vendored 播放器判 `data.ok && data.manifest`），
  // 直接给裸清单会被当成「清单不可用」而回落。
  const manifestJson = JSON.stringify({ ok: true, manifest: rewritten });
  return {
    manifestJson,
    textureCount: decoded.textureCount,
    elapsedMs: decoded.elapsedMs,
    pkgBytes,
    revoke() {
      for (const url of urls.values()) URL.revokeObjectURL(url);
      urls.clear();
    },
  };
}

/**
 * 应用壁纸的 fit 是 CSS 语义，播放器只有 cover/contain/fill。
 * `center`（= object-fit:none，原尺寸居中）对 3840×2160 的场景等于放大裁切，
 * 落到 cover 才是「按窗口等比铺满」，这也是 Wallpaper Engine 桌面的默认表现。
 */
export function weSceneFitFor(fit: LiquidGlassFit): WeSceneFit {
  return fit === "center" ? "cover" : fit;
}

const MAX_PLAYER_REBUILDS = 2;

/**
 * 把一个 scene 作品挂到 host 里；失败/取消都只回报状态，不抛给调用方。
 *
 * @returns 释放句柄（dispose 必须调用，否则 Worker 与 blob URL 会留着）
 */
export function attachSceneBackdrop(options: {
  host: HTMLElement;
  /**
   * 场景来源标识（后端 `scene_package` 的入参）：
   * - `workshop:<id>` / 纯数字 → 创意工坊作品，包在 `<工坊根>/<id>/scene.pkg`
   * - `local:<绝对路径>` → 本地文件夹里的 Wallpaper Engine 工程（含 project.json + scene.pkg）
   * 这一层刻意不认识「工坊」这个概念，只把来源原样交给后端解析。
   */
  source: string;
  fit: LiquidGlassFit;
  paused: boolean;
  onState(state: SceneBackdropState): void;
  /** 测试注入口：真机走 fetch + Worker，单测传桩件即可跑完整挂载链。 */
  /** 是否把宿主鼠标位置转发给播放器做视差（默认开）。 */
  pointer?: boolean;
  load?(source: string, signal: AbortSignal): Promise<{ decoded: SceneDecodeSuccess; pkgBytes: number }>;
  mount?(host: HTMLElement, resources: SceneResources, fit: WeSceneFit, paused: boolean,
    callbacks: { onUnavailable(reason: string): void; onNeedsReload(): void }): ScenePlayerHandle;
}): SceneBackdropMount {
  const controller = new AbortController();
  let disposed = false;
  let resources: SceneResources | null = null;
  let player: ScenePlayerHandle | null = null;
  let rebuilds = 0;
  let currentFit = weSceneFitFor(options.fit);
  let currentPaused = options.paused;

  const mountPlayer = () => {
    if (!resources || disposed) return;
    const handle = options.mount
      ? options.mount(options.host, resources, currentFit, currentPaused, {
          onUnavailable: (reason) => finish({ status: "fallback", reason }),
          onNeedsReload: () => rebuild(),
        })
      : mountScenePlayer(options.host, {
          manifestJson: resources.manifestJson,
          fit: currentFit,
          paused: currentPaused,
          pointer: options.pointer,
          onUnavailable: (reason) => finish({ status: "fallback", reason }),
          onNeedsReload: () => rebuild(),
        });
    player = handle;
  };

  const rebuild = () => {
    if (disposed) return;
    if (rebuilds >= MAX_PLAYER_REBUILDS || !resources) {
      finish({ status: "fallback", reason: "渲染上下文反复丢失，已回落静态预览" });
      return;
    }
    rebuilds += 1;
    player?.dispose();
    player = null;
    mountPlayer();
  };

  const finish = (state: SceneBackdropState) => {
    if (disposed) return;
    options.onState(state);
  };

  const load = options.load
    ?? (async (source: string, signal: AbortSignal) => {
      const bytes = await fetchWallpaperScenePkg(source, signal);
      const decoded = await decodeSceneInWorker(bytes);
      return { decoded, pkgBytes: bytes.byteLength };
    });

  load(options.source, controller.signal)
    .then(({ decoded, pkgBytes }) => {
      if (disposed) return;
      resources = createSceneResources(decoded, pkgBytes);
      mountPlayer();
      finish({
        status: "ready",
        textureCount: resources.textureCount,
        elapsedMs: resources.elapsedMs,
        pkgBytes: resources.pkgBytes,
      });
    })
    .catch((error: unknown) => {
      if (disposed || controller.signal.aborted) return;
      finish({
        status: "fallback",
        reason: error instanceof Error ? error.message : String(error),
      });
    });

  return {
    update(next) {
      const fit = weSceneFitFor(next.fit);
      if (fit !== currentFit) {
        currentFit = fit;
        player?.setFit(fit);
      }
      if (next.paused !== currentPaused) {
        currentPaused = next.paused;
        player?.setPaused(next.paused);
      }
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      controller.abort();
      player?.dispose();
      player = null;
      resources?.revoke();
      resources = null;
    },
  };
}
