/**
 * `node:zlib` 垫片：用 fflate 实现 vendored 解码器需要的 inflateSync / deflateSync。
 *
 * 两处语义必须对齐 Node，否则 PNG 解码/编码会静默出错：
 * - 数据是 zlib 封装（PNG IDAT / encodePng 输出），所以用 unzlibSync / zlibSync，
 *   而不是裸 deflate 的 inflateSync / deflateSync。
 * - `maxOutputLength` 是防 zip-bomb 的上限。fflate 的 `{ out }` 在容量不足时
 *   会**静默截断**（实测长度直接变成 out 的长度），所以这里改用流式 Unzlib
 *   逐块累加、越界即抛，保住「坏包只失败不污染像素」的行为。
 */
import { Unzlib, unzlibSync, zlibSync } from "fflate";
import { asBuffer, type Buffer } from "./nodeBuffer";

export interface InflateOptions {
  maxOutputLength?: number;
}

export interface DeflateOptions {
  level?: number;
}

/** fflate 只接受 0..9 的字面量等级，Node 的 number 需要先收敛。 */
type DeflateLevel = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9;

function collectInflated(data: Uint8Array, maxOutputLength: number): Uint8Array {
  const chunks: Uint8Array[] = [];
  let total = 0;
  const sink = new Unzlib((chunk) => {
    total += chunk.length;
    if (total > maxOutputLength) {
      throw new Error(`zlib: inflated size exceeds maxOutputLength (${maxOutputLength})`);
    }
    chunks.push(chunk.slice());
  });
  sink.push(data, true);
  const out = new Uint8Array(total);
  let cursor = 0;
  for (const chunk of chunks) {
    out.set(chunk, cursor);
    cursor += chunk.length;
  }
  return out;
}

export function inflateSync(data: Uint8Array, options: InflateOptions = {}): Buffer {
  const max = options.maxOutputLength;
  const inflated = typeof max === "number" && Number.isFinite(max) && max > 0
    ? collectInflated(data, Math.floor(max))
    : unzlibSync(data);
  return asBuffer(inflated);
}

export function deflateSync(data: Uint8Array, options: DeflateOptions = {}): Buffer {
  const raw = typeof options.level === "number" ? Math.max(0, Math.min(9, Math.floor(options.level))) : 6;
  const level = raw as DeflateLevel;
  return asBuffer(zlibSync(data, { level }));
}
