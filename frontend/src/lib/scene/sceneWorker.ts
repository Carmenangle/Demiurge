/**
 * scene.pkg 解码 Worker：把 100MB 量级的解包/纹理解码挪出主线程。
 *
 * 只做「字节进、字节出」：不建 blob URL（blob 的生命周期归主线程），
 * 不碰 DOM，不访问文件系统。失败以错误字符串回报，由调用方回落静态预览。
 */
import { readScenePackage } from "./decodeScenePackage";

export interface SceneDecodeRequest {
  id: number;
  /** 整个 scene.pkg 的字节（transfer 过来，Worker 不复制）。 */
  pkg: ArrayBuffer;
}

export type SceneDecodeResponse =
  | {
      id: number;
      ok: true;
      manifest: Record<string, unknown>;
      /** 独立分配的 PNG 字节；随消息一起 transfer，主线程不再复制一遍。 */
      resources: Record<string, Uint8Array<ArrayBuffer>>;
      textureCount: number;
      /** 解包 + 全部纹理解码的毫秒数，便于真机判断大包是否值得实时渲染。 */
      elapsedMs: number;
    }
  | { id: number; ok: false; error: string };

/** 成功分支：主线程只会在 `ok === true` 之后继续用它。 */
export type SceneDecodeSuccess = Extract<SceneDecodeResponse, { ok: true }>;

const scope = self as unknown as {
  onmessage: ((event: MessageEvent<SceneDecodeRequest>) => void) | null;
  postMessage: (message: SceneDecodeResponse, transfer?: Transferable[]) => void;
};

scope.onmessage = (event) => {
  const { id, pkg } = event.data;
  const started = Date.now();
  try {
    const bundle = readScenePackage(new Uint8Array(pkg));
    const transfer = Object.values(bundle.resources).map((bytes) => bytes.buffer as ArrayBuffer);
    scope.postMessage(
      {
        id,
        ok: true,
        manifest: bundle.manifest as unknown as Record<string, unknown>,
        resources: bundle.resources,
        textureCount: bundle.textureCount,
        elapsedMs: Date.now() - started,
      },
      transfer,
    );
  } catch (error) {
    scope.postMessage({
      id,
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    });
  }
};
