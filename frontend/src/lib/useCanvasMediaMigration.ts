// useCanvasMediaMigration — 媒体类型判定的兼容迁移（2026-11-12）
//
// 背景：`generationNodeType` 曾只认 snake_case `media_type`/`video_url`，而后端
// `list_generations` 返回的是 camelCase `mediaType`（地址在 `image_url`）⇒ 视频/音频
// 记录全被判成图组，画布把它们的布局与删除黑名单记在 `img-gen-<id>` 下。
//
// 类型判定修好后节点 id 变成 `video-gen-` / `audio-gen-`，旧键必须跟着改名：
// 否则位置丢失（重新 findFreeSpot）、被删过的节点**复活**（黑名单对不上新 id）。
//
// 触发条件：布局已恢复（ready）且生成记录已拉到；迁移是幂等的，跑一次后 changed=false。

import { useEffect } from "react";
import { migrateMediaLayoutKeys, type GenLike, type PlacedRect } from "./canvasRuntime";

export function useCanvasMediaMigration(
  gens: readonly GenLike[],
  nodes: Record<string, PlacedRect>,
  deletedIds: readonly string[],
  ready: boolean,
  apply: (nodes: Record<string, PlacedRect>, deletedIds: string[]) => void,
): void {
  useEffect(() => {
    if (!ready || gens.length === 0) return;
    const m = migrateMediaLayoutKeys(gens, nodes, deletedIds);
    if (m.changed) apply(m.nodes, m.deletedIds);
  }, [ready, gens, nodes, deletedIds, apply]);
}
