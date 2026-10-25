/**
 * 把 vendored WebGL 播放器作为「同源隔离 iframe」挂进壁纸层。
 *
 * 为什么用 blob: 文档而不是 srcdoc：
 * - srcdoc 文档里 `window.location.origin` 读到的是字符串 `'null'`，而播放器自己用
 *   `ev.origin !== window.location.origin` 校验宿主消息、又用 `postMessage(msg, location.origin)`
 *   回发（真机实测），两边都会静默丢掉 ⇒ fit / pause 改不动，`dsh-scene-unavailable` 也收不到。
 *   blob: 文档继承创建者源，origin 是真实站点源，播放器的协议原样可用。
 * - 上游文件已按 `vendor/NOTICE.md` 登记的补丁落盘（挂载时只注入垫片、不改文件），
 *   再次同步上游时 diff 最小。
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
  /** 是否转发宿主鼠标位置给播放器（视差）。默认开；关闭后场景不再随鼠标偏移。 */
  pointer?: boolean;
  /** 清单不可用（包里没有场景结构）⇒ 调用方回落静态预览。 */
  onUnavailable: (reason: string) => void;
  /** WebGL 上下文丢失且无法自行恢复 ⇒ 调用方重建播放器。 */
  onNeedsReload: () => void;
}

export interface ScenePlayerHandle {
  setFit(fit: WeSceneFit): void;
  setPaused(paused: boolean): void;
  /** 预设热更新：传入的清单与 mount 时的清单同形状（`{ok, manifest}` 信封也可）。 */
  setManifest(manifestJson: string): void;
  /**
   * 请播放器立刻补画一帧（上游的 `dsh-recover-renderer`）。
   * 暂停 = 停帧，但合成层被重建（窗口缩放 / 切回前台）时画布上没有新帧可贴，
   * 壁纸会整块变白；这条通道就是给宿主在那种时机叫播放器重画一次用的。
   */
  recover(): void;
  dispose(): void;
  /** 供测试与诊断：iframe 元素本身。 */
  readonly element: HTMLIFrameElement;
}

/** 从资源的清单 JSON 里取出播放器要的裸清单；信封 `{ok, manifest}` 与裸清单都接受。 */
export function parseSceneManifestPayload(manifestJson: string): Record<string, unknown> | null {
  let payload: unknown;
  try {
    payload = JSON.parse(manifestJson);
  } catch {
    return null;
  }
  if (!payload || typeof payload !== "object") return null;
  const envelope = payload as { manifest?: unknown };
  const manifest = envelope.manifest !== undefined ? envelope.manifest : payload;
  return manifest && typeof manifest === "object" ? manifest as Record<string, unknown> : null;
}

/**
 * 生成播放器文档：在播放器脚本之前注入 fetch 垫片，把它的清单请求指向 blob URL。
 *
 * 垫片还负责把「暂停」升级成真正的停帧：上游播放器收到 `dsh-set-pause` 只是冻结
 * 动画时钟，仍会每帧重绘整屏；滚动/拖动期间那部分开销会直接变成掉帧。这里在
 * 垫片层把 rAF 队列一并挂起，上游文件保持逐字不改；恢复时补跑挂起的回调。
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
  // 暂停时挂起帧回调：播放器的 render 每帧末尾都会再 requestAnimationFrame，
  // 挂起后循环自然停下，画布保留最后一帧；恢复时先把挂起的那一帧补跑。
  var rafPaused = false;
  var rafPending = [];
  // 「暂停 = 定格」有个前提：画布上得真有一帧完整内容。上游 renderFrame 在清单到位前
  // 直接 return（连 clear 都不发），贴图没解码完的层又被逐层跳过（!texRec.loaded continue），
  // 所以开局即暂停时只补一帧会定格在空白画面上（真机实测：截出来整屏全白）。
  // 判据用真实 draw call：连续两帧绘制次数相同且不为 0 ⇒ 内容已成型，才允许停帧。
  var drawCalls = 0;
  // 只看 draw call 还不够：贴图是异步解码的（loadTexture 先塞 1×1 占位，img.onload 里
  // 才真正 texImage2D 上传），换清单后的头几帧可能「图层数已经稳定、但暗色天空还没传上来」，
  // 于是定格在半张画面上（真机实测：夜晚预设部署后先定在白天那一半）。故把纹理上传也当
  // 内容变化：预热期内有上传就不许停帧；已经定格后才到的上传则立刻补一帧。
  var uploads = 0;
  var prevFrameDraws = -1;
  var prevFrameUploads = -1;
  var warmFrames = 0;
  var settled = false;
  // 兜底：每帧图元数一直在变的场景（大量粒子）等不到稳定，预热够久就先定格，
  // 不致于把「暂停」过成满帧重绘。
  var WARM_FRAME_CAP = 120;
  // 暂停期间把交给播放器的时间戳钉死。上游用这个 now 反推 elapsed 喂给 u_time，
  // 而云层 / 水面 / 霓虹这类着色器动画不走 animTime，dsh-set-pause 管不住它们：
  // 不钉住的话，暂停中每补一帧（换预设、换填充方式、宿主为合成器补画）都会把动画
  // 相位往前跳一格——用户看到的就是「明明暂停了，改个预设背景却闪成另一个姿态」。
  // 钉住后补帧是幂等的，同一状态下重画得到同一帧。
  var frozenNow = 0;
  var nativeRaf = window.requestAnimationFrame.bind(window);
  var nativeCancel = window.cancelAnimationFrame.bind(window);
  var warmRaf = 0;
  var draining = false;
  var glProto = window.WebGLRenderingContext && window.WebGLRenderingContext.prototype;
  if (glProto) {
    ['drawArrays', 'drawElements'].forEach(function (name) {
      var orig = glProto[name];
      if (typeof orig !== 'function') return;
      glProto[name] = function () { drawCalls += 1; return orig.apply(this, arguments); };
    });
  }
  var arm;
  var drain = function () {
    warmRaf = 0;
    if (!rafPending.length) return;
    var pending = rafPending;
    rafPending = [];
    var now = frozenNow || performance.now();
    var before = drawCalls;
    var startUploads = uploads;
    // 播放器回调末尾又会 requestAnimationFrame，那一下不能算「还要继续预热」——
    // 此刻本帧的绘制数还没统计，放行就会自己给自己续命，暂停变成满帧重绘。
    draining = true;
    for (var i = 0; i < pending.length; i++) { try { pending[i](now); } catch (e) {} }
    draining = false;
    var draws = drawCalls - before;
    // 两次采样之间的上传增量 = 这一帧窗口里新解码完的贴图（img.onload 不走 rAF）。
    var arrivals = prevFrameUploads < 0 ? 0 : startUploads - prevFrameUploads;
    var steady = draws > 0 && draws === prevFrameDraws && arrivals === 0;
    prevFrameDraws = draws;
    prevFrameUploads = uploads;
    warmFrames += 1;
    settled = steady || warmFrames >= WARM_FRAME_CAP;
    if (!settled) arm();
  };
  arm = function () {
    // settled 之后一律不再起帧：定格就是定格，直到换清单 / 换填充方式 / 恢复播放。
    if (!rafPaused || settled || draining || warmRaf || !rafPending.length) return;
    warmRaf = nativeRaf(drain);
  };
  var restartWarm = function () {
    settled = false;
    prevFrameDraws = -1;
    prevFrameUploads = -1;
    warmFrames = 0;
    arm();
  };
  // 已经定格了才有新贴图解码完 ⇒ 画布上少一层，立刻再补一帧（不重置预热计数，
  // 由 WARM_FRAME_CAP 兜住「贴图一直在变」的病态场景）。
  var requestRepaint = function () {
    if (!rafPaused || !settled) return;
    settled = false;
    arm();
  };
  if (glProto && typeof glProto.texImage2D === 'function') {
    var nativeTexImage2D = glProto.texImage2D;
    glProto.texImage2D = function () {
      uploads += 1;
      if (!draining) requestRepaint();
      return nativeTexImage2D.apply(this, arguments);
    };
  }
  window.requestAnimationFrame = function (cb) {
    if (rafPaused) {
      rafPending.push(cb);
      // 画面还没成型 ⇒ 继续按 vsync 出帧；成型后 settled 置位 ⇒ 从这里开始真的停帧。
      arm();
      return 0;
    }
    return nativeRaf(cb);
  };
  window.addEventListener('message', function (ev) {
    if (ev.source !== window.parent) return;
    var msg = ev.data;
    if (!msg || msg.type !== 'dsh-set-pause') return;
    var next = !!msg.paused;
    rafPaused = next;
    if (next) {
      if (!frozenNow) frozenNow = performance.now();
      arm();
      return;
    }
    frozenNow = 0;
    // 恢复：先撤掉预热用的那一帧，再把挂起的回调交回原生 rAF 循环
    if (warmRaf) { nativeCancel(warmRaf); warmRaf = 0; }
    var pending = rafPending;
    rafPending = [];
    for (var i = 0; i < pending.length; i++) { try { pending[i](performance.now()); } catch (e) {} }
  });
  // 暂停中换清单 / 换填充方式：不补帧就会一直显示旧画面——用户改了壁纸预设却看不到反应。
  // 播放器自己的监听器排在本垫片之后，故交给下一帧（rAF 必然晚于本次消息派发），
  // 并重新走预热：画到内容稳定为止，只补一帧可能停在贴图还没解码的那一半画面上。
  window.addEventListener('message', function (ev) {
    if (ev.source !== window.parent) return;
    var msg = ev.data;
    if (!rafPaused || !msg) return;
    if (msg.type !== 'dsh-set-manifest' && msg.type !== 'dsh-set-fit') return;
    restartWarm();
  });
  // 宿主请播放器补一帧（合成层被重建时，暂停态画布上没有新帧可贴 ⇒ 整块变白）。
  // 暂停中这一帧改由垫片来补：上游那条分支直接 renderFrame(performance.now())，
  // 会把钉住的着色器相位又往前推一格。上下文真丢了才放行给上游走恢复流程。
  window.addEventListener('message', function (ev) {
    if (ev.source !== window.parent) return;
    var msg = ev.data;
    if (!rafPaused || !msg || msg.type !== 'dsh-recover-renderer') return;
    var canvas = document.querySelector('canvas');
    var ctx = canvas && canvas.getContext ? canvas.getContext('webgl') : null;
    if (ctx && ctx.isContextLost && ctx.isContextLost()) return;
    ev.stopImmediatePropagation();
    restartWarm();
  });
  // 视差输入转发：壁纸层是 pointer-events:none 的背景 iframe（放行会被它吞掉滚轮/拖拽），
  // 所以鼠标坐标由宿主按 rAF 节流 postMessage 进来，这里还原成播放器自己要的 mousemove。
  // 播放器按 e.clientX / window.innerWidth 归一化，故按同口径反算回去。
  window.addEventListener('message', function (ev) {
    var msg = ev.data;
    if (!msg || msg.type !== 'dsh-pointer') return;
    var nx = typeof msg.x === 'number' ? msg.x : 0.5;
    var ny = typeof msg.y === 'number' ? msg.y : 0.5;
    try {
      window.dispatchEvent(new MouseEvent('mousemove', {
        clientX: nx * window.innerWidth,
        clientY: ny * window.innerHeight,
        bubbles: true
      }));
    } catch (e) {}
  });
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
  let loaded = false;
  let fit = options.fit;
  let paused = options.paused;
  /** 播放器脚本注册监听之前的清单更新，等 load 后补发（否则消息被静默丢弃）。 */
  let pendingManifest: Record<string, unknown> | null = null;

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
    loaded = true;
    post({ type: "dsh-set-fit", fit });
    post({ type: "dsh-set-pause", paused });
    if (pendingManifest) {
      post({ type: "dsh-set-manifest", manifest: pendingManifest });
      pendingManifest = null;
    }
  };

  window.addEventListener("message", onMessage);
  iframe.addEventListener("load", onLoad);
  parent.appendChild(iframe);

  // 鼠标视差：iframe 保持 pointer-events:none（背景层不能抢宿主的滚轮/拖拽/点选），
  // 改由宿主监听 mousemove 并把归一化坐标按帧转发进去；rAF 节流 ⇒ 一帧最多一条消息。
  let pointerRaf = 0;
  let pointerX = 0.5;
  let pointerY = 0.5;
  const onPointerMove = (event: MouseEvent) => {
    pointerX = event.clientX / Math.max(1, window.innerWidth);
    pointerY = event.clientY / Math.max(1, window.innerHeight);
    if (pointerRaf) return;
    pointerRaf = requestAnimationFrame(() => {
      pointerRaf = 0;
      post({ type: "dsh-pointer", x: pointerX, y: pointerY });
    });
  };
  if (options.pointer !== false) {
    window.addEventListener("mousemove", onPointerMove, { passive: true });
  }

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
    setManifest(manifestJson) {
      const manifest = parseSceneManifestPayload(manifestJson);
      if (!manifest) return;
      if (!loaded) {
        pendingManifest = manifest;
        return;
      }
      post({ type: "dsh-set-manifest", manifest });
    },
    recover() {
      post({ type: "dsh-recover-renderer" });
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      if (pointerRaf) cancelAnimationFrame(pointerRaf);
      pointerRaf = 0;
      window.removeEventListener("mousemove", onPointerMove);
      iframe.removeEventListener("load", onLoad);
      window.removeEventListener("message", onMessage);
      iframe.remove();
      URL.revokeObjectURL(playerUrl);
      URL.revokeObjectURL(manifestUrl);
    },
  };
}
