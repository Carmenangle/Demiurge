/**
 * scene.pkg 解码：把打包字节变成「可渲染清单 + 纹理 PNG 字节」。
 *
 * 分层的理由：解码是纯函数（可在 Worker 里跑、可在单测里跑、不碰 DOM），
 * blob URL 的生命周期只能由主线程管（Worker terminate 会带走它创建的 blob），
 * 所以这里只产出字节，URL 交换交给 sceneBackdrop。
 */
import { asBuffer } from "./shims/nodeBuffer";
import {
  buildSceneManifest,
  extractSceneResource,
  type SceneUserProperties,
  type WeSceneManifest,
} from "./vendor/we-scene-manifest";

/** 预设热重建时复用的已解码纹理与主线程已持有 blob 的路径。 */
export interface SceneDecodeCache {
  textures: Map<string, Uint8Array<ArrayBuffer>>;
  /** 这些路径的 PNG 字节主线程已有 blob，重建响应里不再回传。 */
  knownPaths?: ReadonlySet<string>;
}

/** vendored 构建器写死的资源 URL 前缀（token 由我们传入，故可预测）。 */
export const SCENE_TOKEN = "demiurge";
export const SCENE_RESOURCE_PREFIX = `/wallpaper-engine/scene-resource/${SCENE_TOKEN}/`;

export interface ScenePackageBundle {
  /** 清单里的纹理引用仍是 `SCENE_RESOURCE_PREFIX + 相对路径`，尚未换成 blob。 */
  manifest: WeSceneManifest;
  /** 相对路径 → PNG 字节；只包含清单真正引用到、且本次需要回传的纹理。 */
  resources: SceneResources;
  /** 清单引用到的全部纹理路径（含主线程已持有、本次不回传的）。 */
  paths: string[];
  /** 实际解出的纹理条数。清单里的纹理路径都由包内条目表推导，必然可读，所以没有「缺纹理」通道。 */
  textureCount: number;
}

type SceneResources = Record<string, Uint8Array<ArrayBuffer>>;

/** 递归替换结构中所有命中的字符串；其余节点原样返回。 */
export function mapSceneStrings<T>(node: T, replace: (value: string) => string): T {
  if (typeof node === "string") return replace(node) as T;
  if (Array.isArray(node)) return node.map((item) => mapSceneStrings(item, replace)) as unknown as T;
  if (node && typeof node === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      out[key] = mapSceneStrings(value, replace);
    }
    return out as T;
  }
  return node;
}

/** 把 `SCENE_RESOURCE_PREFIX + p` 形式的字符串换成 urlFor(p) 的结果；urlFor 返回 null 则保留原串。 */
export function rewriteSceneResourceUrls(
  manifest: WeSceneManifest,
  urlFor: (path: string) => string | null,
): WeSceneManifest {
  return mapSceneStrings(manifest, (value) => {
    if (!value.startsWith(SCENE_RESOURCE_PREFIX)) return value;
    const path = value.slice(SCENE_RESOURCE_PREFIX.length);
    const url = urlFor(path);
    return url === null ? value : url;
  });
}

/** 清单引用到的全部纹理相对路径（去重、保序）。 */
export function collectSceneResourcePaths(manifest: WeSceneManifest): string[] {
  const found: string[] = [];
  const seen = new Set<string>();
  mapSceneStrings(manifest, (value) => {
    if (!value.startsWith(SCENE_RESOURCE_PREFIX)) return value;
    const path = value.slice(SCENE_RESOURCE_PREFIX.length);
    if (!seen.has(path)) {
      seen.add(path);
      found.push(path);
    }
    return value;
  });
  return found;
}

/**
 * 视频纹理的字节以 MP4 的 `ftyp` box 开头（box 长度在头 4 字节）。
 * WE 把这种 `.tex` 当普通贴图引用，但浏览器只认裸 MP4，所以要把这个事实
 * 一路带到 blob MIME 和播放器选路，不能靠文件后缀猜。
 */
export function isMp4Bytes(bytes: Uint8Array | undefined | null): boolean {
  return !!bytes && bytes.length >= 8 &&
    bytes[4] === 0x66 && bytes[5] === 0x74 && bytes[6] === 0x79 && bytes[7] === 0x70;
}

/** 给引用视频纹理的图层打标；播放器据此走 <video> 纹理而不是 <img>。 */
export function markSceneVideoLayers(
  manifest: WeSceneManifest,
  videoPaths: ReadonlySet<string>,
): void {
  if (videoPaths.size === 0) return;
  const isVideoRef = (value: unknown): boolean =>
    typeof value === "string" && value.startsWith(SCENE_RESOURCE_PREFIX) &&
    videoPaths.has(value.slice(SCENE_RESOURCE_PREFIX.length));
  for (const layer of manifest.layers as Array<Record<string, unknown>>) {
    if (!layer || typeof layer !== "object") continue;
    const refs: unknown[] = [layer.texUrl];
    if (Array.isArray(layer.texUrls)) refs.push(...layer.texUrls);
    if (refs.some(isVideoRef)) layer.videoTex = true;
  }
}

/**
 * 解包并解码一个 `scene.pkg`。
 *
 * @throws Error 包体不合法（magic/越界）或 scene.json 缺失/无 objects
 */
export function readScenePackage(
  pkgBytes: Uint8Array,
  userProperties?: SceneUserProperties,
  cache?: SceneDecodeCache,
): ScenePackageBundle {
  const pkg = asBuffer(pkgBytes);
  const manifest = buildSceneManifest(pkg, SCENE_TOKEN, userProperties);
  if (!manifest || !Array.isArray(manifest.layers)) {
    throw new Error("scene.pkg 里没有可用的 scene.json 场景结构");
  }
  const paths = collectSceneResourcePaths(manifest);
  const resources: ScenePackageBundle["resources"] = {};
  for (const path of paths) {
    if (!cache) {
      const extracted = extractSceneResource(pkg, path);
      if (extracted === null || extracted === undefined) continue;
      const copy = new Uint8Array(extracted.length);
      copy.set(extracted);
      resources[path] = copy;
      continue;
    }
    let bytes = cache.textures.get(path);
    if (!bytes) {
      const extracted = extractSceneResource(pkg, path);
      if (extracted === null || extracted === undefined) continue;
      bytes = new Uint8Array(extracted.length);
      bytes.set(extracted);
      cache.textures.set(path, bytes);
    }
    if (cache.knownPaths?.has(path)) continue;
    // Buffer/缓存视图都可能是共享分配；主线程要 transfer，必须独立拷贝。
    const copy = new Uint8Array(bytes.length);
    copy.set(bytes);
    resources[path] = copy;
  }
  // 热重建不回传主线程已持有 blob 的纹理字节，视频判定必须能落到缓存上，
  // 否则换一次预设图层就退回 <img> 通道（视频层被静默跳过）。
  const videoPaths = new Set(
    paths.filter((path) => isMp4Bytes(resources[path] ?? cache?.textures.get(path))),
  );
  markSceneVideoLayers(manifest, videoPaths);
  return { manifest, resources, paths, textureCount: paths.length };
}
