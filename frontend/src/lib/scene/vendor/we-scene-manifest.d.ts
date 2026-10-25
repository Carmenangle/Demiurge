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
  /** WE 场景级相机视差开关（`general.cameraparallax`，缺省开启）。 */
  cameraParallax?: boolean;
  /**
   * 真正影响已渲染图层的用户属性键（按名字排序）。
   * 右侧壁纸预设面板只展示这些选项：文字/音频/鼠标拖尾等本渲染器画不出的
   * 属性不会伪装成可用开关。
   */
  userBindings?: string[];
  /** puppetId → 蒙皮骨架/动画数据（仅含真实被引用的 puppet 模型）。 */
  puppets?: Record<string, MdlPuppetPayload>;
  scripted?: boolean;
  is3D?: boolean;
  /**
   * WE 粒子系统（`particles/*.json`）。发射器/初始化/算子都已在清单侧折叠成
   * 场景像素与秒，播放器只按这张表模拟；3D 通道用其中标量字段，2D 通道用其余。
   */
  particles3d?: WeSceneParticleSystem[];
  /**
   * 内置资产路径（`we-asset/...`）→ 仅被该系统用到的用户属性键。
   * 主线程取不到字节时据此撤掉控件：没装 WE 就不该留下拨了没反应的开关。
   */
  builtInAssetKeys?: Record<string, string[]>;
  [key: string]: unknown;
};
/** WE 粒子系统一条系统的清单形状（发射参数已在清单侧折叠成场景像素与秒）。 */
export type WeSceneParticleSystem = {
  name: string;
  texUrl: string;
  /** 发射中心（场景像素，含 emitter 局部偏移按对象 scale 映射）。 */
  origin: number[];
  /** 生成速率（个/秒，已乘 instanceoverride.rate）。 */
  rate: number;
  maxCount: number;
  lifeMin: number;
  lifeMax: number;
  sizeMin: number;
  sizeMax: number;
  distMin: number;
  distMax: number;
  /** 预设原值（场景像素/秒）：不随对象 scale 放大，也不做镜像。 */
  velMin: number[];
  velMax: number[];
  /** 0..1，已乘 instanceoverride.colorn 染色。 */
  colorMin: number[];
  colorMax: number[];
  /** `additive` 加色 / `translucent` 常规 alpha。 */
  blend?: string;
  /** 对象自身 z 轴转角（弧度，与 2D 图层同一口径）。 */
  angle?: number;
  /** 精灵尺寸与扩散的逐轴倍率（取自对象 scale 绝对值）。 */
  sizeScale?: number[];
  /** 对象 scale 为负 ⇒ 作者做的镜像，播放器交换 uv 边界翻采样（只翻贴图，不翻位置）。 */
  flipX?: boolean;
  flipY?: boolean;
  /** boxrandom 的各轴半宽（已乘 sizeScale）；存在时播放器走矩形分支而非球壳。 */
  box?: number[];
  /** sphererandom 的各轴压扁比例（官方雾预设用 1 0.2 0 把球压成扁带）。 */
  directions?: number[];
  /** 预设原值（场景像素/秒²），不随对象 scale 放大。 */
  gravity?: number[];
  drag?: number;
  alphaMin?: number;
  alphaMax?: number;
  rotMin?: number;
  rotMax?: number;
  fadeIn?: number;
  fadeOut?: number;
  oscFreq?: number;
  oscMin?: number;
  /** 序列帧（一格多大、一共几格）；包内纹理的尺寸来自文件名，内置资产来自 TEXS 帧表。 */
  frames?: { w: number; h: number; count: number };
  /** controlpointattract 吸引点（绝对场景坐标）。 */
  attractions?: Array<{ x: number; y: number; scale: number; threshold: number }>;
};
/** WE 用户在 project.json 里可调的属性值（用户覆盖优先于工程默认值）。 */
export type SceneUserProperties = Record<string, string | number | boolean>;
export type MdlPuppetPayload = {
  id: string;
  boneCount: number;
  parents: number[];
  inverseBindB64: string;
  bindTrsB64: string;
  clips: Record<string, {
    id: number;
    fps: number;
    frameCount: number;
    loop: boolean;
    tracks: Array<{ bone: number; keysB64: string }>;
  }>;
};

export function parsePkg(data: Uint8Array): unknown[];
export function readPkgEntry(data: Uint8Array, entry: unknown): Uint8Array;
export type MdlRig = {
  boneCount: number;
  bones: Array<{ parent: number; matrix: Float32Array; world: Float32Array }>;
  attachments: Array<{
    name: string;
    boneIndex: number;
    local: Float32Array;
    socket: Float32Array;
  }>;
  clips: Array<{
    id: number;
    name: string;
    type: string;
    fps: number;
    frameCount: number;
    tracks: Array<Float32Array | undefined>;
  }>;
};
export function parseMdlRig(data: Uint8Array): MdlRig;
/** `frames` 是 .tex 自带的 TEXS 帧表（序列页每格的像素矩形），没有就是非序列贴图。 */
export function decodeTex(data: Uint8Array): {
  width: number;
  height: number;
  rgba: Uint8Array;
  frames?: Array<{ width: number; height: number; frametime: number }>;
};
export function decodePngToRgba(pngBuf: Uint8Array): { width: number; height: number; rgba: Uint8Array };
export function encodePng(width: number, height: number, rgba: Uint8Array): Buffer;
export function buildSceneManifest(
  pkgData: Buffer,
  token: string,
  userProperties?: SceneUserProperties,
): WeSceneManifest | null;
export function extractSceneResource(pkgData: Buffer, subpath: string): Buffer | null;
