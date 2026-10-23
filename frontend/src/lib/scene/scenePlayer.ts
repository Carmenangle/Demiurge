/**
 * 把 vendored WebGL 播放器作为「同源隔离 iframe」挂进壁纸层。
 *
 * 为什么用 blob: 文档而不是 srcdoc：
 * - srcdoc 文档里 `window.location.origin` 读到的是字符串 `'null'`，而播放器自己用
 *   `ev.origin !== window.location.origin` 校验宿主消息、又用 `postMessage(msg, location.origin)`
 *   回发（真机实测），两边都会静默丢掉 ⇒ fit / pause 改不动，`dsh-scene-unavailable` 也收不到。
 *   blob: 文档继承创建者源，origin 是真实站点源，播放器的协议原样可用。
 * - 上游文件仍保持逐字不改（只有一个注入垫片），再次同步上游时 diff 最小。
 *
 * 代价（已知、可接受）：同源 iframe 仍跑在渲染进程主线程上，真正的工作线程隔离
 * 需要 OffscreenCanvas 改造，属后续可选项；这里用「不可见即暂停」压住开销。
 */
import { WE_SCENE_PLAYER_HTML } from "./vendor/we-scene-player-html";

/** 播放器唯一会被拦截的请求：它原本从上游 HTTP 路由取清单。 */
const MANIFEST_FETCH_PATH = "/wallpaper-engine/scene-manifest/";

export type WeSceneFit = "cover" | "contain" | "fill";

export interface ScenePlayerOptions {
  /** 已完成 blob 替换的清单；播放器会 JSON.parse 它。 */
  manifestJson: string;
  fit: WeSceneFit;
  paused: boolean;
  /** 清单不可用（包里没有场景结构）⇒ 调用方回落静态预览。 */
  onUnavailable: (reason: string) => void;
  /** WebGL 上下文丢失且无法自行恢复 ⇒ 调用方重建播放器。 */
  onNeedsReload: () => void;
}

export interface ScenePlayerHandle {
  setFit(fit: WeSceneFit): void;
  setPaused(paused: boolean): void;
  dispose(): void;
  /** 供测试与诊断：iframe 元素本身。 */
  readonly element: HTMLIFrameElement;
}

/**
 * 生成播放器文档：在播放器脚本之前注入 fetch 垫片，把它的清单请求指向 blob URL。
 *
 * 导出为纯函数，便于单测断言「上游 HTML 本体未被改写、只多了一段垫片」。
 */
export function buildScenePlayerSrcdoc(manifestUrl: string): string {
  const shim = `<script>(function () {
  var MANIFEST_URL = ${JSON.stringify(manifestUrl)};
  var nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var href = typeof input === 'string' ? input : (input && input.url) || '';
    if (typeof href === 'string' && href.indexOf(${JSON.stringify(MANIFEST_FETCH_PATH)}) === 0) {
      return nativeFetch(MANIFEST_URL, init);
    }
    return nativeFetch(input, init);
  };
})();</script>`;
  // 用函数形式替换，避免 blob URL 里的 $ 序列被当成替换模式。
  return WE_SCENE_PLAYER_HTML.replace("<script>", () => shim + "<script>");
}

/** 挂载播放器；返回的 handle 负责移除 iframe 并撤销它用到的两个 blob URL。 */
export function mountScenePlayer(
  parent: HTMLElement,
  options: ScenePlayerOptions,
): ScenePlayerHandle {
  const blob = new Blob([options.manifestJson], { type: "application/json" });
  const manifestUrl = URL.createObjectURL(blob);
  const iframe = document.createElement("iframe");
  iframe.className = "app-backdrop-scene";
  iframe.setAttribute("aria-hidden", "true");
  iframe.setAttribute("tabindex", "-1");
  // 不设 sandbox：播放器与宿主必须同源，否则它自己的 origin 校验与回发消息都不成立。
  const playerUrl = URL.createObjectURL(
    new Blob([buildScenePlayerSrcdoc(manifestUrl)], { type: "text/html" }),
  );
  iframe.src = playerUrl;
  iframe.referrerPolicy = "no-referrer";

  let disposed = false;
  let fit = options.fit;
  let paused = options.paused;

  const post = (message: Record<string, unknown>) => {
    if (disposed) return;
    iframe.contentWindow?.postMessage(message, "*");
  };

  const onMessage = (event: MessageEvent) => {
    if (disposed || event.source !== iframe.contentWindow) return;
    const data = event.data as { type?: unknown } | null;
    if (!data || typeof data.type !== "string") return;
    if (data.type === "dsh-scene-unavailable") options.onUnavailable("场景清单不可用");
    else if (data.type === "dsh-scene-needs-reload") options.onNeedsReload();
  };

  const onLoad = () => {
    // 初始状态必须在播放器脚本注册监听之后再发；load 是最早的可靠时机。
    post({ type: "dsh-set-fit", fit });
    post({ type: "dsh-set-pause", paused });
  };

  window.addEventListener("message", onMessage);
  iframe.addEventListener("load", onLoad);
  parent.appendChild(iframe);

  return {
    element: iframe,
    setFit(next) {
      fit = next;
      post({ type: "dsh-set-fit", fit: next });
    },
    setPaused(next) {
      paused = next;
      post({ type: "dsh-set-pause", paused: next });
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      iframe.removeEventListener("load", onLoad);
      window.removeEventListener("message", onMessage);
      iframe.remove();
      URL.revokeObjectURL(playerUrl);
      URL.revokeObjectURL(manifestUrl);
    },
  };
}
