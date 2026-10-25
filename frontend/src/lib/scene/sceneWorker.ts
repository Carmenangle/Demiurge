/**
 * scene.pkg 解码 Worker：把 100MB 量级的解包/纹理解码挪出主线程。
 *
 * 只做「字节进、字节出」：不建 blob URL（blob 的生命周期归主线程），
 * 不碰 DOM，不访问文件系统。失败以错误字符串回报，由调用方回落静态预览。
 *
 * 预设热更新走同一个 Worker：首次 decode 后缓存包体与已解码 PNG，后续 rebuild
 * 只重建清单并回传主线程尚未持有的纹理，避免每拖一下滑杆就重新解一遍包。
 */
import { readScenePackage } from "./decodeScenePackage";
import type { SceneUserProperties } from "./vendor/we-scene-manifest";

export interface SceneDecodeRequest {
  id: number;
  kind: "decode";
  /** 整个 scene.pkg 的字节（transfer 过来，Worker 不复制）。 */
  pkg: ArrayBuffer;
  /** 初始预设（WE 用户覆盖 + 工程默认值）。 */
  properties?: SceneUserProperties;
}

export interface SceneRebuildRequest {
  id: number;
  kind: "rebuild";
  /** 首次 decode 响应里的会话号；与当前缓存不匹配即返回 stale。 */
  session: number;
  properties: SceneUserProperties;
  /** 主线程已持有 blob 的纹理路径（不再回传字节）。 */
  knownPaths: string[];
}

export type SceneWorkerRequest = SceneDecodeRequest | SceneRebuildRequest;

export type SceneDecodeResponse =
  | {
      id: number;
      ok: true;
      manifest: Record<string, unknown>;
      /** 独立分配的 PNG 字节；随消息一起 transfer，主线程不再复制一遍。 */
      resources: Record<string, Uint8Array<ArrayBuffer>>;
      /** 清单引用到的全部纹理路径（含本次未回传的）。 */
      paths: string[];
      textureCount: number;
      /** 解包 + 全部纹理解码的毫秒数，便于真机判断大包是否值得实时渲染。 */
      elapsedMs: number;
      session: number;
      /** 与缓存里的解码会话不匹配（用户已切到别的壁纸）。 */
      stale?: false;
    }
  | { id: number; ok: false; error: string; stale?: boolean };

/** 成功分支：主线程只会在 `ok === true` 之后继续用它。 */
export type SceneDecodeSuccess = Extract<SceneDecodeResponse, { ok: true }>;

const scope = self as unknown as {
  onmessage: ((event: MessageEvent<SceneWorkerRequest>) => void) | null;
  postMessage: (message: SceneDecodeResponse, transfer?: Transferable[]) => void;
};

let sessionSeq = 0;
let cache: {
  session: number;
  pkg: Uint8Array;
  textures: Map<string, Uint8Array<ArrayBuffer>>;
} | null = null;

scope.onmessage = (event) => {
  const request = event.data;
  const started = Date.now();
  try {
    if (request.kind === "rebuild") {
      if (!cache || cache.session !== request.session) {
        scope.postMessage({
          id: request.id,
          ok: false,
          error: "场景解码会话已切换",
          stale: true,
        });
        return;
      }
      const bundle = readScenePackage(cache.pkg, request.properties, {
        textures: cache.textures,
        knownPaths: new Set(request.knownPaths),
      });
      respond(request.id, bundle, Date.now() - started, cache.session);
      return;
    }
    sessionSeq += 1;
    const pkg = new Uint8Array(request.pkg);
    cache = { session: sessionSeq, pkg, textures: new Map() };
    const bundle = readScenePackage(pkg, request.properties, {
      textures: cache.textures,
    });
    respond(request.id, bundle, Date.now() - started, cache.session);
  } catch (error) {
    scope.postMessage({
      id: request.id,
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    });
  }
};

function respond(
  id: number,
  bundle: ReturnType<typeof readScenePackage>,
  elapsedMs: number,
  session: number,
): void {
  const transfer = Object.values(bundle.resources).map((bytes) => bytes.buffer as ArrayBuffer);
  scope.postMessage(
    {
      id,
      ok: true,
      manifest: bundle.manifest as unknown as Record<string, unknown>,
      resources: bundle.resources,
      paths: bundle.paths,
      textureCount: bundle.textureCount,
      elapsedMs,
      session,
    },
    transfer,
  );
}
