"""资产库门面：列表/分页/结构化搜索/打标解析/回收站恢复/后台补任务的业务编排。

2026-10-24 审计中期项：自 routers/rag.py 下沉——路由只留 wire 模型与 HTTP 状态映射，
读路径策略（主表↔向量库回退）、分页口径、两段式反推解析规则（前后端唯一真源）、
后台任务循环统一在此。错误语义：AssetMissing→404、TaggerUnavailable→503、
ValueError→400、RuntimeError→500/503（按端点），由路由映射。
"""
from __future__ import annotations

import json
import re
import threading

from app.services import asset_store, rag_store
from app.services.rag_backend import EmbedConfig


class AssetMissing(LookupError):
    """资产条目不存在（路由映射 404）。"""


class TaggerUnavailable(RuntimeError):
    """打标模型未安装（路由映射 503，detail 附下载/放置指引）。"""


def asset_db_empty() -> bool:
    """主表尚未回填（0 行）⇒ 读路径回退 Chroma（旧行为）。

    升级瞬间不能让资产列表变空：未跑 scripts/backfill_asset_db.py 时以向量库为准；
    主表异常同样回退（列表绝不 500）。
    """
    try:
        return asset_store.count_assets() == 0
    except Exception:  # noqa: BLE001 [EXC-保留] 主表不可用一律回退（列表绝不 500）
        return True


# 画师标记 `@//画师名/`（ComfyUI「反推动漫」工作流里 DanbooruGallery 的画师串写法）
_ARTIST_MARK = re.compile(r"^@//(.+?)/+$")


def parse_tagged_text(text: str) -> tuple[list[str], str]:
    """两段式反推文本 →（标签列表, 描述段）——与 ComfyUI「反推动漫」工作流同构。

    工作流最终产物（PromptStashPassthrough）＝
        masterpiece, score_9, year 2025, highres, safe, 1girl, liduke, @//liduke/, ...
        <空行>
        A single girl with blonde hair ... against a stark white background.

    解析规则（单一真源，前后端不得各写一份）：
    - 按**空行**切段：第一段是标签行，其余全部拼回＝描述段；
    - 标签按英文逗号/中文逗号/顿号/分号/换行切分，去引号去空白；
    - `@//画师名/` 折成 `画师名`（同一画师裸名已存在则不重复，避免存两份）；
    - 去重保序（大小写不敏感）。
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return [], ""
    parts = re.split(r"\n\s*\n", raw, maxsplit=1)
    head = parts[0]
    description = parts[1].strip() if len(parts) > 1 else ""
    tags: list[str] = []
    seen: set[str] = set()

    def push(value: str) -> None:
        value = value.strip().strip("\"'“”‘’")
        if not value:
            return
        artist = _ARTIST_MARK.match(value)
        if artist:
            value = artist.group(1).strip()
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        tags.append(value)

    for token in re.split(r"[,，、;；\n]+", head):
        push(token)
    # 护栏：模型没按两段式输出（只给了一整句描述、没有逗号）⇒ 不把整句话塞成标签
    if len(tags) == 1 and len(tags[0].split()) >= 8:
        merged = head if not description else f"{head}\n\n{description}"
        return [], merged.strip()
    return tags, description


def _row_tags(row: dict) -> list[str]:
    """主表 tags_json → 标签列表（兼容裸逗号串/损坏 JSON）。"""
    raw = row.get("tags_json") or "[]"
    try:
        data = json.loads(raw) if isinstance(raw, str) else list(raw)
        if isinstance(data, list):
            return [str(t) for t in data if str(t).strip()]
    except (ValueError, TypeError):
        pass
    return [t.strip() for t in str(raw).split(",") if t.strip()]


def _fallback_search_chroma(repo_ids: list[str], *, cfg: EmbedConfig, q: str,
                            tags: list[str], media_type: str, before_ts: int) -> list[dict]:
    """主表未回填时的结构化搜索兜底（内存过滤；仅升级过渡期用，与 FTS5 同语义）。"""
    merged: list[dict] = []
    for rid in repo_ids:
        merged.extend(rag_store.list_generations(rid, cfg, before_ts=before_ts))
    wanted = [t.strip() for t in tags if t.strip()]
    text = (q or "").strip().lower()
    out: list[dict] = []
    for it in merged:
        if media_type and it.get("mediaType") != media_type:
            continue
        if wanted and not all(t in (it.get("tags") or []) for t in wanted):
            continue
        if text:
            hay = "\n".join([
                it.get("prompt") or "", it.get("description") or "",
                ",".join(it.get("tags") or []),
            ]).lower()
            if text not in hay:
                continue
        out.append(it)
    out.sort(key=lambda d: d.get("created_at", 0), reverse=True)
    return out


def list_generations_page(repo_id: str, repo_ids: list[str], *, cfg: EmbedConfig,
                          limit: int, before_ts: int, offset: int,
                          media_type: str) -> dict[str, object]:
    """列出生成记录（P1-1 读路径）：主表已回填走 SQLite（repo 范围+分页+媒体类型单查询），
    未回填回退向量库旧行为。limit>0 时分页（created_at 倒序）：响应带 has_more 与
    **total**（keyset=before_ts / offset 两种游标）；total 与 items 同口径（都按
    media_type 过滤后的计数）；向量库路径不计数 ⇒ 0＝未知，前端退回「虚拟下一页」。
    limit=0 时全量返回、has_more=False，行为与改造前一致。
    """
    fetch = limit + 1 if limit > 0 else 0
    media = media_type if media_type in ("image", "video", "audio") else ""
    items: list[dict] | None = None
    total = 0
    if not asset_db_empty():
        if before_ts > 0:
            items = asset_store.list_assets(
                repo_ids or [repo_id], limit=fetch, before_ts=before_ts, media_type=media)
        else:
            items = asset_store.list_assets(
                repo_ids or [repo_id], limit=fetch, offset=offset, media_type=media)
        try:
            total = asset_store.count_assets(repo_ids or [repo_id], deleted=False, media_type=media)
        except Exception:  # noqa: BLE001 [EXC-保留] 计数失败不该让列表 500（total=0 ⇒ 前端退回虚拟页）
            total = 0
    if items is None:
        # 回退（主表尚未回填）：沿用向量库路径，行为与改造前一致
        if repo_ids:
            items = rag_store.list_generations_multi(
                repo_ids, cfg, limit=fetch, before_ts=before_ts)
        else:
            items = rag_store.list_generations(
                repo_id, cfg, limit=fetch, before_ts=before_ts)
        total = 0   # 向量库路径不计数 ⇒ 0＝未知，前端退回「虚拟下一页」
    has_more = limit > 0 and len(items) > limit
    if has_more:
        items = items[:limit]
    return {"items": items, "has_more": has_more, "total": total}


def search_assets_page(repo_id: str, repo_ids: list[str], *, cfg: EmbedConfig, q: str,
                       tags: list[str], media_type: str, limit: int,
                       before_ts: int) -> dict[str, object]:
    """结构化搜索：FTS5 全文 + asset_tags 精确 AND + LIKE 兜底（中文子串）。

    与 Chroma hybrid 语义检索（/search-generations）**并存**，不是二选一：
    标签/chips/关键词走这里，语义开关走那里。主表未回填时走内存兜底。
    """
    scope = repo_ids or [repo_id]
    fetch = limit + 1 if limit > 0 else 0
    if asset_db_empty():
        items = _fallback_search_chroma(
            scope, cfg=cfg, q=q, tags=tags, media_type=media_type, before_ts=before_ts)
    else:
        items = asset_store.search_assets(
            scope, q=q, tags=tags, media_type=media_type,
            limit=fetch, before_ts=before_ts,
        )
    has_more = limit > 0 and len(items) > limit
    if has_more:
        items = items[:limit]
    return {"items": items, "has_more": has_more}


def tag_stats(repo_ids: list[str], cfg: EmbedConfig) -> dict[str, object]:
    """聚合仓库集合的标签→图片数量（按量降序），供加标签/搜索的输入补全。

    主表已回填 ⇒ SQLite 聚合（asset_tags 索引）；否则回退 Chroma 全量扫。
    """
    if not asset_db_empty():
        return {"items": asset_store.tag_stats(repo_ids)}
    return {"items": rag_store.tag_stats(repo_ids, cfg)}


def restore_asset(asset_id: str, repo_id: str, cfg: EmbedConfig) -> dict[str, object]:
    """从回收站恢复：**先重建向量、成功后才清 deleted_at**。

    顺序刻意如此：嵌入失败则保持回收站态（可重试），绝不出现「列表有、语义搜不到」
    的半状态。旧版随机 uuid 记录恢复后 id 变为 gen-<sha1>，顺手清掉旧空壳行。
    """
    row = asset_store.get_row(asset_id)
    if row is None:
        raise AssetMissing("回收站里没有这条记录")
    if row.get("deleted_at") is None:
        return {"ok": True, "already": True, "id": asset_id}
    repo = row.get("repo_id") or repo_id
    if not (cfg.base_url or cfg.model_dir):
        # wire 未带嵌入配置（脚本/后端自发调用）⇒ 读运行态真源，避免重建向量报缺协议
        cfg = rag_store.embed_config_from_state()
    derived_raw = row.get("derived_from") or "[]"
    try:
        derived = json.loads(derived_raw) if isinstance(derived_raw, str) else list(derived_raw)
        if not isinstance(derived, list):
            derived = []
    except (ValueError, TypeError):
        derived = []
    try:
        rag_store.index_generation(
            repo, cfg,
            prompt=row.get("prompt") or "",
            tags=",".join(_row_tags(row)),
            image_url=row.get("image_url") or "",
            created_at=int(row.get("created_at") or 0),
            description=row.get("description") or "",
            template_name=row.get("template_name") or "",
            model_name=row.get("model_name") or "",
            lora_names=row.get("lora_names") or "",
            media_type=row.get("media_type") or "image",
            derived_from=derived,
            source=row.get("source") or "",
            source_url=row.get("source_url") or "",
            source_meta=row.get("source_meta") or "",
        )
    except Exception as exc:  # noqa: BLE001 - 重建向量失败：保持回收站态，可重试
        raise RuntimeError(f"恢复失败（重建向量出错）：{exc}") from exc
    new_id = rag_store.generation_doc_id(repo, row.get("image_url") or "")
    if new_id != asset_id:
        asset_store.delete_rows([asset_id])
    asset_store.restore(new_id)
    return {"ok": True, "id": new_id}


def generate_thumbs(repo_id: str, repo_ids: list[str], cfg: EmbedConfig) -> dict[str, object]:
    """为仓库历史资产补生成缩略图（P0-2），登记为后台任务（jobs）显示 X/N 进度。
    只处理本机静态图；远程/裂图跳过。幂等可重跑；读路径有 thumb_url_if_ready 兜底
    发现，无需回写元数据（避免重嵌）。"""
    from app.services import jobs, thumbnail_store

    repo_ids = repo_ids or [repo_id]
    per_repo_items = {rid: rag_store.list_generations(rid, cfg) for rid in repo_ids}
    total = sum(
        1 for items in per_repo_items.values() for it in items
        if thumbnail_store.local_path_of_url(str(it.get("image_url") or "")) is not None
    )
    job = jobs.create("thumbnails", f"补缩略图（{len(repo_ids)} 仓库）", total)
    try:
        generated = skipped = 0
        for items in per_repo_items.values():
            for item in items:
                if jobs.should_cancel(job.id):
                    break
                path = thumbnail_store.local_path_of_url(str(item.get("image_url") or ""))
                if path is None:          # 远程/裂图：不计入 total，也不 tick
                    skipped += 1
                    continue
                if thumbnail_store.ensure_thumb(path) is None:
                    skipped += 1
                else:
                    generated += 1
                jobs.tick(job.id)
        jobs.finish(job.id, "cancelled" if jobs.should_cancel(job.id) else "done")
        return {"ok": True, "generated": generated, "skipped": skipped, "job_id": job.id}
    except Exception as exc:  # noqa: BLE001
        jobs.finish(job.id, "failed", str(exc))
        raise RuntimeError(f"补缩略图失败：{exc}") from exc


def wd14_tag_asset(asset_id: str, repo_id: str, cfg: EmbedConfig) -> dict[str, object]:
    """对单条资产跑 WD14 反推打标（P0-3），结果合并进 tags（不动原有标签）。

    仅支持本机留存图片（远程图取不到字节）；模型未安装抛 TaggerUnavailable（路由映射
    503 并附下载指引）。
    """
    from app.services import thumbnail_store, wd14_tagger

    if not wd14_tagger.model_available():
        raise TaggerUnavailable(
            "WD14 模型未安装：请将 model.onnx 与 selected_tags.csv 放到 "
            "data/models/wd14-tagger/（HF: SmilingWolf/wd-swinv2-tagger-v3）")
    items = rag_store.list_generations(repo_id, cfg)
    item = next((i for i in items if i.get("id") == asset_id), None)
    if item is None:
        raise AssetMissing("资产不存在")
    path = thumbnail_store.local_path_of_url(str(item.get("image_url") or ""))
    if path is None:
        raise ValueError("仅支持本机留存图片（远程图取不到字节）")
    tags = wd14_tagger.reverse_tags(path)
    existing = list(item.get("tags") or [])
    merged = list(dict.fromkeys([*existing, *tags]))[:32]
    rag_store.set_doc_tags(asset_id, repo_id, cfg, merged)
    return {"ok": True, "added": [t for t in tags if t not in existing], "total": len(merged)}


def tag_asset_with(asset_id: str, repo_id: str, model: str,
                   cfg: EmbedConfig) -> dict[str, object]:
    """按指定打标器对单条资产反推打标（合并进 tags，不动原有）。可插拔，见 tagger_registry。"""
    from app.services import tagger_registry, thumbnail_store, wd14_tagger

    spec = tagger_registry.resolve(model)
    if not wd14_tagger.model_available(spec.dir):
        raise TaggerUnavailable(f"打标模型未安装：{spec.name}（请放到 {spec.dir}）")
    items = rag_store.list_generations(repo_id, cfg)
    item = next((i for i in items if i.get("id") == asset_id), None)
    if item is None:
        raise AssetMissing("资产不存在")
    path = thumbnail_store.local_path_of_url(str(item.get("image_url") or ""))
    if path is None:
        raise ValueError("仅支持本机留存图片（远程图取不到字节）")
    tags = [name for name, _score in tagger_registry.predict(model, path)]
    snap = rag_store.add_tag_snapshot(asset_id, repo_id, cfg, tags,
                                      model=spec.id, label=spec.name)
    if snap is None:
        raise ValueError("无法写入标签快照（条目被锁定或不存在）")
    return {"ok": True, "model": spec.id, "snapshot_id": snap["id"],
            "tags": snap["tags"], "total": len(snap["tags"])}


def save_tag_text(asset_id: str, repo_id: str, *, text: str, model: str, label: str,
                  save_description: bool, cfg: EmbedConfig) -> dict[str, object]:
    """两段式反推落库：标签行 → 新标签快照；描述段 → 资产描述（重建语义索引）。

    视觉推理由前端发起（wire 透传对话模型配置），后端只做**解析 + 落库**，切分规则
    单一真源 = parse_tagged_text；快照/描述写入也只有这一份实现。
    """
    tags, description = parse_tagged_text(text)
    if not tags:
        raise ValueError("没有解析出标签：第一段应为逗号分隔的 tag 行（第二段描述用空行分隔）")
    snap = rag_store.add_tag_snapshot(asset_id, repo_id, cfg, tags, model=model, label=label)
    if snap is None:
        raise ValueError("无法写入标签快照（条目被锁定或不存在）")
    saved_description = False
    if save_description and description:
        saved_description = rag_store.set_generation_description(
            asset_id, repo_id, cfg, description,
        )
    return {
        "ok": True,
        "snapshot_id": snap["id"],
        "tags": snap["tags"],
        "total": len(snap["tags"]),
        "description": description if saved_description else "",
        "saved_description": saved_description,
    }


def index_visual_generations(repo_id: str, repo_ids: list[str],
                             cfg: EmbedConfig) -> dict[str, object]:
    """为仓库图片建立独立视觉向量索引。

    登记为**后台任务**（jobs），前端「后台活动」面板轮询 /jobs 显示 X/N 进度条；
    支持 repo_ids 多仓库一次跑（聚合出一个 1/157 的总进度）；分块编码、可协作式取消。
    """
    from app.services import jobs, visual_asset_index

    repo_ids = repo_ids or [repo_id]
    try:
        per_repo_items = {rid: rag_store.list_generations(rid, cfg) for rid in repo_ids}
        total = sum(visual_asset_index.count_indexable(items) for items in per_repo_items.values())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"视觉索引不可用：{exc}") from exc

    job = jobs.create("visual_index", f"视觉索引（{len(repo_ids)} 仓库）", total)
    try:
        indexed = skipped = 0
        for rid, items in per_repo_items.items():
            if jobs.should_cancel(job.id):
                break
            r = visual_asset_index.index_items(
                rid, items, on_progress=lambda n: jobs.tick(job.id, n),
            )
            indexed += int(r.get("indexed") or 0)
            skipped += int(r.get("skipped") or 0)
        jobs.finish(job.id, "cancelled" if jobs.should_cancel(job.id) else "done")
        return {"ok": True, "indexed": indexed, "skipped": skipped, "job_id": job.id}
    except Exception as exc:  # noqa: BLE001
        jobs.finish(job.id, "failed", str(exc))
        raise RuntimeError(f"视觉索引不可用：{exc}") from exc


def describe_missing_assets(repo_ids: list[str], vlm: dict, hint: str, concurrency: int,
                            cfg: EmbedConfig) -> dict[str, object]:
    """批量补视觉描述（后台任务，立即返回 job_id）。

    图片从本机磁盘直读（不绕后端代理 URL → dataURI 那一圈）；GET /jobs 轮询可得 X/N，
    「后台活动」面板显示进度条，可取消。线程内异常落 job failed，不静默吞掉。
    """
    from app.services import jobs, vlm_batch

    try:
        total = len(vlm_batch.missing_description_assets(repo_ids))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"资产库不可用：{exc}") from exc

    job = jobs.create("vlm_describe", f"视觉描述（{len(repo_ids)} 仓库）", total)

    def _run() -> None:
        try:
            vlm_batch.run_describe_missing(job.id, repo_ids, cfg, vlm, hint, concurrency)
        except Exception as exc:  # noqa: BLE001 [EXC-保留] 后台线程兜底：线程内异常不能静默吞掉
            jobs.finish(job.id, "failed", str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "job_id": job.id, "total": total}
