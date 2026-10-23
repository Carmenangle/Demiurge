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
  type WeSceneManifest,
} from "./vendor/we-scene-manifest";

/** vendored 构建器写死的资源 URL 前缀（token 由我们传入，故可预测）。 */
export const SCENE_TOKEN = "demiurge";
export const SCENE_RESOURCE_PREFIX = `/wallpaper-engine/scene-resource/${SCENE_TOKEN}/`;

export interface ScenePackageBundle {
  /** 清单里的纹理引用仍是 `SCENE_RESOURCE_PREFIX + 相对路径`，尚未换成 blob。 */
  manifest: WeSceneManifest;
  /** 相对路径 → PNG 字节；只包含清单真正引用到的纹理。 */
  resources: SceneResources;
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
 * 解包并解码一个 `scene.pkg`。
 *
 * @throws Error 包体不合法（magic/越界）或 scene.json 缺失/无 objects
 */
export function readScenePackage(pkgBytes: Uint8Array): ScenePackageBundle {
  const pkg = asBuffer(pkgBytes);
  const manifest = buildSceneManifest(pkg, SCENE_TOKEN);
  if (!manifest || !Array.isArray(manifest.layers)) {
    throw new Error("scene.pkg 里没有可用的 scene.json 场景结构");
  }
  const paths = collectSceneResourcePaths(manifest);
  const resources: ScenePackageBundle["resources"] = {};
  for (const path of paths) {
    const bytes = extractSceneResource(pkg, path);
    if (bytes === null || bytes === undefined) continue;
    // Buffer 视图共享整个 pkg 的 ArrayBuffer，所以必须拷成独立分配才能 transfer。
    const copy = new Uint8Array(bytes.length);
    copy.set(bytes);
    resources[path] = copy;
  }
  return { manifest, resources, textureCount: Object.keys(resources).length };
}
