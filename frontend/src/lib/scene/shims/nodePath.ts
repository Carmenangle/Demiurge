/**
 * `node:path` 垫片：只实现 vendored 解码器用到的 join / resolve / sep。
 *
 * 这两个函数仅服务于「散装目录场景」分支（dirSceneAccess），而 Demiurge 只走
 * `scene.pkg` 内存分支，所以这里按 POSIX 语义最小实现即可，不模拟 Windows 盘符。
 */
export const sep = "/";

export function join(...parts: string[]): string {
  return parts
    .filter((part) => part.length > 0)
    .join(sep)
    .replace(/\/{2,}/g, sep);
}

/** Worker 里没有进程工作目录，只能把绝对路径原样返回、相对路径按 / 拼接。 */
export function resolve(...parts: string[]): string {
  const joined = join(...parts);
  return joined.startsWith(sep) ? joined : sep + joined;
}
