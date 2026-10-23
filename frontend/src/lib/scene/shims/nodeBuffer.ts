/**
 * `node:buffer` 垫片：浏览器 / Worker 里用 buffer ponyfill 提供 Buffer 语义。
 *
 *  vendored 解码器依赖 Buffer 的 slice/subarray/concat/writeUInt32BE 等方法，
 *  故所有跨边界的字节都要包成 Buffer，不能只给 Uint8Array。
 */
import { Buffer as BufferCtor } from "buffer";

export const Buffer = BufferCtor;
export type Buffer = BufferCtor;

/** 零拷贝地把字节视图包成 Buffer（共享底层 ArrayBuffer）。 */
export function asBuffer(bytes: Uint8Array): Buffer {
  // ArrayBufferView 的 buffer 在类型上是 ArrayBufferLike；运行时来源只有 ArrayBuffer
  // （fetch / Worker 结构化克隆），所以这里收窄一次而不是再拷一份。
  return Buffer.from(bytes.buffer as ArrayBuffer, bytes.byteOffset, bytes.byteLength);
}
