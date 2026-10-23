/**
 * `node:fs` 垫片：Demiurge 的运行时里没有文件系统，也不该有。
 *
 * vendored 解码器只有「散装目录场景」分支（dirSceneAccess：project.json 与
 * scene.json、.tex 都是目录里的独立文件）才碰 fs；我们只把 `scene.pkg` 的字节
 * 交给 pkg 分支（pkgSceneAccess），所以这里一律抛错——真走到这里说明调用方
 * 传错了入口，而不是需要降级。
 */
const UNAVAILABLE = "scene 解码运行在 Worker 里，没有 node:fs；请改用 scene.pkg 字节入口";

export function lstatSync(): never {
  throw new Error(UNAVAILABLE);
}

export function statSync(): never {
  throw new Error(UNAVAILABLE);
}

export function readdirSync(): never {
  throw new Error(UNAVAILABLE);
}

export function readFileSync(): never {
  throw new Error(UNAVAILABLE);
}

export function realpathSync(): never {
  throw new Error(UNAVAILABLE);
}
