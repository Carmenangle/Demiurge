/**
 * vendored `we-scene-manifest.js` 的类型声明（手工维护，非生成）。
 *
 * 只声明 Demiurge 真正会用到的导出；其余导出（*.FromDir 目录分支、PNG/TEX 细粒度
 * 工具）在浏览器运行时不可用（node:fs 垫片会抛错），故不暴露给 TS 调用方。
 */
import type { Buffer } from "../shims/nodeBuffer";

/**
 * `buildSceneManifest` 的产物：一个纯 JSON 可序列化对象（网格顶点以 base64 字符串
 * 承载），纹理以 `resourceBase + 相对路径` 的 URL 引用。
 */
export type WeSceneManifest = {
  width: number;
  height: number;
  layers: unknown[];
  scripted?: boolean;
  is3D?: boolean;
  [key: string]: unknown;
};

export function parsePkg(data: Uint8Array): unknown[];
export function readPkgEntry(data: Uint8Array, entry: unknown): Uint8Array;
export function decodeTex(data: Uint8Array): { width: number; height: number; rgba: Uint8Array };
export function decodePngToRgba(pngBuf: Uint8Array): { width: number; height: number; rgba: Uint8Array };
export function encodePng(width: number, height: number, rgba: Uint8Array): Buffer;
export function buildSceneManifest(pkgData: Buffer, token: string): WeSceneManifest | null;
export function extractSceneResource(pkgData: Buffer, subpath: string): Buffer | null;
