/**
 * 场景壁纸实时渲染的编排层：取包字节 → Worker 解码 → blob 纹理 → 挂载隔离播放器。
 *
 * 线程分工是刻意的：
 * - 主线程只做 HTTP 与 blob URL（生命周期跟着宿主组件走，卸载即 revoke，不漏内存）；
 * - 解码（PKGV/LZ4/TEX + PNG 重编码）全在 Worker，100MB 级包不能碰主线程；
 * - 渲染在同源 blob: 文档 iframe，播放器崩溃只回落静态预览，不带倒整个应用。
 */
import type { LiquidGlassFit } from "../../stores/settings";
import { fetchWallpaperSceneAsset, fetchWallpaperScenePkg } from "../../api/userState";
import { rewriteSceneResourceUrls, isMp4Bytes } from "./decodeScenePackage";
import { decodeTex, encodePng } from "./vendor/we-scene-manifest";
import type {
  SceneDecodeRequest,
  SceneDecodeResponse,
  SceneDecodeSuccess,
  SceneRebuildRequest,
} from "./sceneWorker";
import type { SceneUserProperties, WeSceneManifest } from "./vendor/we-scene-manifest";
import { mountScenePlayer, type ScenePlayerHandle, type WeSceneFit } from "./scenePlayer";

/**
 * 解码结果换成 blob 之后的句柄：manifestJson 可直接喂播放器，revoke 负责收尾。
 *
 * 预设热更新复用同一份对象：`apply` 只给新出现的纹理建 blob，清单里的引用按
 * 「路径 → URL」全量重写。已建立的 URL 在整个挂载期保留 —— 日夜切换来回拖时
 * 不再回传/解码/重建 blob，代价只是这些 PNG 常驻内存（本来就已解过一次）。
 */
export interface SceneResources {
  manifestJson: string;
  textureCount: number;
  elapsedMs: number;
  pkgBytes: number;
  /** 当前清单引用到的纹理路径。 */
  paths: string[];
  /** 当前清单实际用到的 WE 用户属性名（预设面板据此过滤控件）。 */
  bindings: string[];
  /** Worker 解码会话号，热重建时用于拒绝过期会话。 */
  session: number;
  /** 已经建立 blob 的全部路径（含旧预设用过的），热重建时告知 Worker 不必回传。 */
  knownPaths(): string[];
  apply(decoded: SceneDecodeSuccess): void;
  revoke(): void;
}

export type SceneBackdropState =
  | { status: "loading" }
  | {
      status: "ready";
      textureCount: number;
      elapsedMs: number;
      pkgBytes: number;
      bindings: string[];
      /**
       * 当前这组 WE 预设值是否已经落到播放器清单上。
       * 首帧刻意先按工程烘焙值出图（不等 scene-properties 那次 HTTP），所以就绪后
       * 会有一小段「面板已是 WE 生效值、画布还是烘焙画面」的窗口；换预设热重建同理。
       */
      presetApplied: boolean;
    }
  | { status: "fallback"; reason: string };

export interface SceneBackdropMount {
  update(next: { fit: LiquidGlassFit; paused: boolean }): void;
  /** 应用一组新的 WE 用户属性值（整组覆盖）；内部按最新值合并重建。 */
  setProperties(properties: SceneUserProperties): void;
  /** 合成层被重建后请播放器补画一帧；暂停态下不补就会是一片空白（见 scenePlayer.recover）。 */
  recover(): void;
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
    else if (event.data.stale) settle.reject(new SceneSessionStaleError());
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

export class SceneSessionStaleError extends Error {
  constructor() {
    super("场景解码会话已切换");
    this.name = "SceneSessionStaleError";
  }
}

export function decodeSceneInWorker(
  pkgBytes: ArrayBuffer,
  properties?: SceneUserProperties,
): Promise<SceneDecodeSuccess> {
  return new Promise<SceneDecodeSuccess>((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    try {
      const request: SceneDecodeRequest = { id, kind: "decode", pkg: pkgBytes, properties };
      decodeWorker().postMessage(request, [pkgBytes]);
    } catch (error) {
      pending.delete(id);
      reject(error instanceof Error ? error : new Error(String(error)));
    }
  });
}

/** 预设热重建：Worker 复用已缓存的包体与 PNG，只回传主线程还没有的纹理。 */
export function rebuildSceneInWorker(
  session: number,
  properties: SceneUserProperties,
  knownPaths: string[],
): Promise<SceneDecodeSuccess> {
  return new Promise<SceneDecodeSuccess>((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    try {
      const request: SceneRebuildRequest = { id, kind: "rebuild", session, properties, knownPaths };
      decodeWorker().postMessage(request);
    } catch (error) {
      pending.delete(id);
      reject(error instanceof Error ? error : new Error(String(error)));
    }
  });
}

/** 清单里以该前缀引用的资源不在 scene.pkg 内，而是 Wallpaper Engine 安装目录的内置资产。 */
const BUILT_IN_PREFIX = "we-asset/";

/** blob 的 MIME 只影响少数消费方（FontFace 会看），按后缀给，别一律写 image/png。 */
function mimeOf(path: string, bytes?: Uint8Array): string {
  // 视频纹理在清单里仍是 `.tex`，后缀说明不了真相；MP4 头是唯一可信信号，
  // 标错 MIME 会让 <video> 拒绝播放（<img> 则直接加载失败）。
  if (isMp4Bytes(bytes)) return "video/mp4";
  const ext = path.slice(path.lastIndexOf(".") + 1).toLowerCase();
  if (ext === "ttf") return "font/ttf";
  if (ext === "otf") return "font/otf";
  if (ext === "woff") return "font/woff";
  if (ext === "woff2") return "font/woff2";
  return "image/png";
}

/** 内置资产的一手结果：能直接建 blob 的字节，外加（序列页才有的）帧表。 */
interface BuiltInAsset {
  bytes: Uint8Array<ArrayBuffer>;
  frames?: { w: number; h: number; count: number };
}

const builtInAssets = new Map<string, Promise<BuiltInAsset | null>>();

/**
 * WE 的 `.tex` 是它自己的容器（多为 BC1/BC3 压缩），浏览器不认；字体是原样字节。
 * 所以内置贴图在这里补一次解码，与包内纹理归成同一条「PNG 字节」通道。
 * 解不动就原样交回：WE 也把 PNG 直接当 `.tex` 存（官方鸟序列帧就是这样）。
 *
 * `frames` 是 `.tex` 自带的 TEXS 帧表换算出的「一格多大、一共几格」。包内纹理的序列尺寸
 * 有文件名约定（birds_128x120x16），清单建好时就带着；内置资产没有这个约定，只能等字节
 * 到手才知道——官方雾贴图是一张 8×8 的序列页，不知道格宽就会把六团雾画成一团。
 */
function builtInAssetBytes(path: string, buffer: ArrayBuffer): BuiltInAsset {
  if (!path.toLowerCase().endsWith(".tex")) return { bytes: new Uint8Array(buffer) };
  try {
    const decoded = decodeTex(new Uint8Array(buffer));
    const first = decoded.frames?.[0];
    return {
      bytes: new Uint8Array(encodePng(decoded.width, decoded.height, decoded.rgba)),
      frames: first
        ? { w: first.width, h: first.height, count: decoded.frames?.length ?? 1 }
        : undefined,
    };
  } catch {
    return { bytes: new Uint8Array(buffer) };
  }
}

/**
 * 取一只内置资产，按 assets 相对路径常驻缓存。
 *
 * 中文字体一只是十几 MB，且同一只字体在不同壁纸、日夜来回切换时反复被引用；失败不
 * 固化（下次挂载还能试），因为「没装 WE」与一次网络抖动的可恢复性完全不同。
 */
function loadBuiltInAsset(path: string): Promise<BuiltInAsset | null> {
  const running = builtInAssets.get(path);
  if (running) return running;
  const next: Promise<BuiltInAsset | null> = fetchWallpaperSceneAsset(path)
    .then((buffer) => builtInAssetBytes(path, buffer))
    .catch(() => {
      builtInAssets.delete(path);
      return null;
    });
  builtInAssets.set(path, next);
  return next;
}

/**
 * 把清单引用、但 scene.pkg 里没有的内置资产字节补进 `decoded.resources`。
 *
 * 补进来之后就与包内纹理同一条通道（建 blob → 重写 URL），播放器不需要知道来源差别。
 * 取不到的保持缺席：清单里那条 URL 不会被重写，播放器按加载失败处理（字体退系统栈），
 * 不带倒整幅画面。`we-asset/` 只是清单里的命名空间前缀，取字节的路由要的是 assets 目录
 * 内的相对路径，所以在这一层剥掉——资源键仍用清单路径，与 blob 表同源。
 */
async function attachBuiltInAssets(
  decoded: SceneDecodeSuccess,
  alreadyHeld: string[],
  load: (path: string) => Promise<BuiltInAsset | null>,
): Promise<void> {
  const held = new Set(alreadyHeld);
  const missing = decoded.paths.filter(
    (path) => path.startsWith(BUILT_IN_PREFIX) && !held.has(path) && !decoded.resources[path],
  );
  if (missing.length === 0) return;
  const assets = await Promise.all(
    missing.map((path) => load(path.slice(BUILT_IN_PREFIX.length)).catch(() => null)),
  );
  const manifest = decoded.manifest as WeSceneManifest;
  const systems = Array.isArray(manifest.particles3d) ? manifest.particles3d : [];
  for (const [index, path] of missing.entries()) {
    const asset = assets[index];
    if (!asset) continue;
    decoded.resources[path] = asset.bytes;
    if (!asset.frames) continue;
    // 文件名约定已经给出格宽的（包内纹理）不覆盖：那是作者写死的，比帧表更可信。
    for (const sys of systems) {
      if (!sys.frames && typeof sys.texUrl === "string" && sys.texUrl.endsWith(path)) {
        sys.frames = asset.frames;
      }
    }
  }
}

/**
 * 面板可用键 = 清单声明的键 − 依赖「没送到的内置资产」的键。
 *
 * 官方雾 / 光束 / 星光贴图不在 scene.pkg 里，而在 WE 安装目录；没装 WE 时字节取不到，
 * 画面上就没有任何东西消费这些开关，于是它们必须一起从面板消失，而不是留下拨了没反应
 * 的假开关（16 册红线 16）。清单用 `builtInAssetKeys` 交出「贴图 → 依赖它的键」这张表。
 */
function usableBindings(manifest: WeSceneManifest, urls: Map<string, string>): string[] {
  const declared = Array.isArray(manifest.userBindings) ? manifest.userBindings : [];
  const gates = manifest.builtInAssetKeys;
  if (!gates || typeof gates !== "object" || declared.length === 0) return declared;
  const blocked = new Set<string>();
  for (const [asset, keys] of Object.entries(gates)) {
    if (urls.has(asset)) continue;
    for (const key of keys) blocked.add(key);
  }
  return blocked.size === 0 ? declared : declared.filter((key) => !blocked.has(key));
}

/** 把解码出来的纹理字节换成 blob URL，并产出一份可直接渲染的清单 JSON。 */
export function createSceneResources(decoded: SceneDecodeSuccess, pkgBytes: number): SceneResources {
  const urls = new Map<string, string>();
  const resources: SceneResources = {
    manifestJson: "",
    textureCount: 0,
    elapsedMs: 0,
    pkgBytes,
    paths: [],
    bindings: [],
    session: decoded.session,
    knownPaths: () => Array.from(urls.keys()),
    apply(next) {
      for (const [path, bytes] of Object.entries(next.resources)) {
        if (urls.has(path)) continue;
        urls.set(path, URL.createObjectURL(new Blob([bytes], { type: mimeOf(path, bytes) })));
      }
      const manifest = next.manifest as unknown as WeSceneManifest;
      const rewritten = rewriteSceneResourceUrls(manifest, (path) => urls.get(path) ?? null);
      // 播放器读的是上游路由的响应体，形状必须是信封 `{ok, manifest}`（vendored 播放器判 `data.ok && data.manifest`），
      // 直接给裸清单会被当成「清单不可用」而回落。
      resources.manifestJson = JSON.stringify({ ok: true, manifest: rewritten });
      resources.textureCount = next.textureCount;
      resources.elapsedMs = next.elapsedMs;
      resources.paths = next.paths;
      resources.bindings = usableBindings(manifest, urls);
      resources.session = next.session;
    },
    revoke() {
      for (const url of urls.values()) URL.revokeObjectURL(url);
      urls.clear();
    },
  };
  resources.apply(decoded);
  return resources;
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
  /** 初始 WE 用户属性值（未提供的键回落到工程里烘焙的默认值）。 */
  properties?: SceneUserProperties;
  onState(state: SceneBackdropState): void;
  /** 测试注入口：真机走 fetch + Worker，单测传桩件即可跑完整挂载链。 */
  /** 是否把宿主鼠标位置转发给播放器做视差（默认开）。 */
  pointer?: boolean;
  load?(
    source: string,
    signal: AbortSignal,
    properties: SceneUserProperties,
  ): Promise<{ decoded: SceneDecodeSuccess; pkgBytes: number }>;
  rebuild?(
    session: number,
    properties: SceneUserProperties,
    knownPaths: string[],
  ): Promise<SceneDecodeSuccess>;
  mount?(host: HTMLElement, resources: SceneResources, fit: WeSceneFit, paused: boolean,
    callbacks: { onUnavailable(reason: string): void; onNeedsReload(): void }): ScenePlayerHandle;
  /** WE 内置资产取字节（默认走后端只读路由 + 按路径常驻缓存）；单测传桩件避免真发 HTTP。 */
  fetchBuiltIn?(path: string): Promise<BuiltInAsset | null>;
}): SceneBackdropMount {
  const controller = new AbortController();
  const fetchBuiltIn = options.fetchBuiltIn ?? loadBuiltInAsset;
  let disposed = false;
  let resources: SceneResources | null = null;
  let player: ScenePlayerHandle | null = null;
  let remounts = 0;
  let currentFit = weSceneFitFor(options.fit);
  let currentPaused = options.paused;
  let properties: SceneUserProperties = { ...(options.properties ?? {}) };
  let appliedProperties: SceneUserProperties = { ...properties };

  const sameProperties = (a: SceneUserProperties, b: SceneUserProperties): boolean => {
    const keys = Object.keys(a);
    if (keys.length !== Object.keys(b).length) return false;
    return keys.every((key) => Object.is(a[key], b[key]));
  };

  const readyState = (): SceneBackdropState => ({
    status: "ready",
    textureCount: resources?.textureCount ?? 0,
    elapsedMs: resources?.elapsedMs ?? 0,
    pkgBytes: resources?.pkgBytes ?? 0,
    bindings: resources?.bindings ?? [],
    presetApplied: sameProperties(appliedProperties, properties),
  });

  const mountPlayer = () => {
    if (!resources || disposed) return;
    const handle = options.mount
      ? options.mount(options.host, resources, currentFit, currentPaused, {
          onUnavailable: (reason) => finish({ status: "fallback", reason }),
          onNeedsReload: () => remount(),
        })
      : mountScenePlayer(options.host, {
          manifestJson: resources.manifestJson,
          fit: currentFit,
          paused: currentPaused,
          pointer: options.pointer,
          onUnavailable: (reason) => finish({ status: "fallback", reason }),
          onNeedsReload: () => remount(),
        });
    player = handle;
  };

  const remount = () => {
    if (disposed) return;
    if (remounts >= MAX_PLAYER_REBUILDS || !resources) {
      finish({ status: "fallback", reason: "渲染上下文反复丢失，已回落静态预览" });
      return;
    }
    remounts += 1;
    player?.dispose();
    player = null;
    mountPlayer();
  };

  const finish = (state: SceneBackdropState) => {
    if (disposed) return;
    options.onState(state);
  };

  const rebuildScene = options.rebuild
    ?? ((session, props, knownPaths) => rebuildSceneInWorker(session, props, knownPaths));

  // 滑杆会连续触发 setProperties：同一时刻只允许一次重建，期间只保留最新一组值，
  // 中间态的清单直接丢弃，避免播放器来回跳帧。
  let rebuildBusy = false;
  let rebuildQueued = false;

  const drainRebuilds = async () => {
    if (rebuildBusy || disposed) return;
    rebuildBusy = true;
    try {
      while (rebuildQueued && !disposed && resources) {
        rebuildQueued = false;
        const target = resources;
        const nextProperties = { ...properties };
        let decoded: SceneDecodeSuccess;
        try {
          decoded = await rebuildScene(target.session, nextProperties, target.knownPaths());
        } catch (error) {
          if (disposed || controller.signal.aborted) return;
          if (error instanceof SceneSessionStaleError) return;
          finish({
            status: "fallback",
            reason: error instanceof Error ? error.message : String(error),
          });
          return;
        }
        if (disposed || target !== resources) return;
        if (rebuildQueued) continue;
        // 切到「字体2/3/4」这类档位时才会第一次引用内置字体：补取字节，已在手的跳过。
        await attachBuiltInAssets(decoded, target.knownPaths(), fetchBuiltIn);
        if (disposed || target !== resources) return;
        target.apply(decoded);
        appliedProperties = nextProperties;
        player?.setManifest(target.manifestJson);
        finish(readyState());
      }
    } finally {
      rebuildBusy = false;
      if (rebuildQueued && !disposed && resources) void drainRebuilds();
    }
  };

  const scheduleRebuild = () => {
    if (disposed) return;
    rebuildQueued = true;
    void drainRebuilds();
  };

  const initialProperties = { ...properties };
  const load = options.load
    ?? (async (source: string, signal: AbortSignal, props: SceneUserProperties) => {
      const bytes = await fetchWallpaperScenePkg(source, signal);
      const decoded = await decodeSceneInWorker(bytes, props);
      return { decoded, pkgBytes: bytes.byteLength };
    });

  load(options.source, controller.signal, initialProperties)
    .then(async ({ decoded, pkgBytes }) => {
      await attachBuiltInAssets(decoded, [], fetchBuiltIn);
      if (disposed) return;
      resources = createSceneResources(decoded, pkgBytes);
      appliedProperties = initialProperties;
      mountPlayer();
      finish(readyState());
      // 首次解码期间用户就改了预设：按最新值再热重建一次。
      if (!sameProperties(appliedProperties, properties)) scheduleRebuild();
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
    setProperties(next) {
      if (disposed || sameProperties(properties, next)) return;
      properties = { ...next };
      if (!resources) return;
      // 先报一次「预设还没落上」：重建是异步的，宿主（和真机验证）需要看得见这个窗口期。
      finish(readyState());
      scheduleRebuild();
    },
    recover() {
      player?.recover();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      rebuildQueued = false;
      controller.abort();
      player?.dispose();
      player = null;
      resources?.revoke();
      resources = null;
    },
  };
}
