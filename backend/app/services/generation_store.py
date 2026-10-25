"""生成产出持久化：统一负责留存、入 RAG 和写对话快照。"""
import base64
import logging
import re
import time
import uuid
from typing import Any
from collections import OrderedDict
from pathlib import Path

import httpx

from app.services import (
    chat_memory, chat_snapshot, image_store, local_auth, rag_store, repo_meta, run_trace,
    view_urls,
)
from app.services.rag_backend import EmbedConfig
from app.services.pathnames import safe_seg

_LOG = logging.getLogger("uvicorn.error")
_WORKFLOW_NAMESPACE = uuid.UUID("98f86310-70e7-4bc2-9b7a-7655140560b0")


class _BoundedKeySet:
    """有界去重集合：记「已写记忆的工作流」防重复，超上限按 FIFO 淘汰最旧键。
    取代原模块级 set（只增不减，长期运行慢泄漏）。淘汰的旧键极不可能再 finalize，安全。"""

    def __init__(self, cap: int = 2048) -> None:
        self._cap = cap
        self._d: "OrderedDict[str, None]" = OrderedDict()

    def __contains__(self, key: str) -> bool:
        return key in self._d

    def add(self, key: str) -> None:
        self._d[key] = None
        self._d.move_to_end(key)
        while len(self._d) > self._cap:
            self._d.popitem(last=False)

    def clear(self) -> None:
        self._d.clear()


_MEMORY_DONE = _BoundedKeySet()

# 远程图片/视频下载上限，防超大/恶意直链把 response.content 一次性读爆内存。
_MAX_IMAGE_BYTES = 30 * 1024 * 1024   # 30 MB
_MAX_VIDEO_BYTES = 200 * 1024 * 1024  # 200 MB

# CDN 结果下载的**总时限**兜底（2026-10-13，B）。httpx 的 read timeout 语义是
# 「两次 recv 之间」的超时：GFW 对境外 CDN 的限速/滴漏会让分块间隔始终小于它
# ⇒ 计时器被反复重置、总耗时无上界（真机：同一直链直连 217s 只拿到 10% 后
# ReadTimeout；走本机代理 24.1s 下完 3.9MB。当日 5 轮取回 4.5～73 分钟）。
# 故在分块循环里另按单调时钟掐总时长；超时抛 TimeoutError，调用方回退远程 URL
# （浏览器走系统代理仍能开图），不再无限挂死。0＝不启用。
# ⚠ 界的诚实口径：截止检查只能在「收到一个分块后」跑，而单次 recv 最多阻塞
# `timeout`（per-read 超时）秒 ⇒ 最坏耗时 ≈ total_timeout + timeout。真机实测
# total_timeout=45 时 154.1s 才抛（45+109，滴漏间隔 109s < read timeout 120s），
# 与推导一致。要再紧只能上加 watchdog 线程 response.close()（暂不做，避免线程面）。
_DOWNLOAD_TOTAL_TIMEOUT_IMAGE = 600   # 10 min：30MB 上限下 ≥50KB/s 均不误杀
_DOWNLOAD_TOTAL_TIMEOUT_VIDEO = 1800  # 30 min：视频体积大、CDN 更慢


def _download_capped(url: str, timeout: int, max_bytes: int, proxy: str = "",
                     total_timeout: int = 0) -> bytes:
    """流式下载并在超过 max_bytes 时中止，避免全量读入超大响应。超限抛 ValueError。

    proxy：显式代理（生图/视频模型配了代理时透传，与上传侧
    `image_gen._load_image_bytes` 对齐——download 侧原先一个代理参数都没有，
    直连境外 CDN 被 GFW 滴漏限速，取回可拖到几十分钟）。trust_env=False 下不读
    环境变量，故代理必须显式给；local-view 本机回环地址跳过代理（Clash 等无法
    转发回环请求，会把取图打成 502）。
    total_timeout>0 时按单调时钟掐**总**时长，防「滴漏」绕开 per-read 超时。
    """
    from app.services.url_guard import is_local_view_url
    local = is_local_view_url(url)
    client_kwargs: dict[str, Any] = {"trust_env": False, "timeout": timeout}  # 后塞 proxy(str)，值域必须宽
    if proxy and not local:
        client_kwargs["proxy"] = proxy
    started = time.monotonic()
    with httpx.Client(**client_kwargs) as client:
        # local-view 的 wire 形态是根相对（属主 view_urls），httpx 只吃绝对地址
        # 自取图同属「本机 API」：移动端启用 LAF_LOCAL_TOKEN 后必须带令牌头，
        # 否则会被 local_auth 中间件判 401（桌面未启用时为空字典，行为不变）。
        headers = local_auth.outbound_headers() if local else None
        with client.stream("GET", view_urls.fetchable(url), headers=headers) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                if total_timeout and time.monotonic() - started > total_timeout:
                    raise TimeoutError(
                        f"下载超过总时限 {total_timeout}s（已收 {total} 字节），已中止：{url}"
                    )
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"远程文件超过 {max_bytes // (1024 * 1024)}MB 上限，已中止下载")
                chunks.append(chunk)
    return b"".join(chunks)


def _save_remote_image(url: str, output_dir: str, repo_id: str = "home",
                       proxy: str = "") -> str:
    """Agent 云图容错留存；失败回退原地址，不阻断已完成的生成。

    proxy：生图模型代理，透传给 CDN 结果图下载（2026-10-13 实锤：直连境外 CDN
    被 GFW 限速滴漏，120s read timeout 被反复重置 ⇒ 取回 4.5～73 分钟且常挂死）。
    """
    if not output_dir or not url:
        return url
    try:
        if url.startswith("data:"):
            header, b64 = url.split(",", 1)
            data = base64.b64decode(re.sub(r"\s+", "", b64))
            ext = "png"
            if "image/" in header:
                ext = header.split("image/")[1].split(";")[0] or "png"
        else:
            data = _download_capped(url, timeout=120, max_bytes=_MAX_IMAGE_BYTES,
                                    proxy=proxy,
                                    total_timeout=_DOWNLOAD_TOTAL_TIMEOUT_IMAGE)
            tail = safe_seg(Path(url.split("?")[0]).name)
            ext = tail.rsplit(".", 1)[1] if "." in tail else "png"
        base = image_store.media_dir(repo_meta.repo_folder(output_dir, repo_id), "image")  # → pictures/
        name = image_store._next_seq_name(base, ext)
        dest = base / name
        dest.write_bytes(data)
        return view_urls.local_view(str(dest))
    except Exception as exc:  # noqa: BLE001
        # 留痕：否则「云端 Success 但对话没图」时只能看到无限挂死、无声失败
        _LOG.warning("远程图片留存失败，回退远程地址 url=%s: %s", url, exc)
        return url


def _index_with_retry(repo_id: str, cfg: EmbedConfig, prompt: str,
                      tags: str = "", image_url: str = "",
                      template_name: str = "", model_name: str = "",
                      lora_names: str = "", media_type: str = "image",
                      derived_from: list | None = None) -> bool:
    for attempt in range(3):
        try:
            rag_store.index_generation(repo_id, cfg, prompt, tags, image_url,
                                       template_name=template_name,
                                       model_name=model_name,
                                       lora_names=lora_names,
                                       media_type=media_type,
                                       derived_from=derived_from)
            return True
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("index_generation 失败(第%d次) repo=%s img=%s: %s",
                         attempt + 1, repo_id, image_url, exc)
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
    return False


def persist_image(thread_id: str, repo_id: str, prompt: str, image_url: str,
                  output_dir: str, embed_base: str, embed_key: str,
                  embed_model: str, regeneration: dict | None = None,
                  embed_proxy: str = "", gen_proxy: str = "",
                  write_snapshot: bool = True) -> dict:
    """Agent 图片：容错留存后入库并追加快照，返回前后端共用身份。

    gen_proxy：生图模型代理，透传给 CDN 结果图下载（与 embed_proxy 同一传法）。
    write_snapshot=False：只「落盘 + RAG 入库」，**不写 chat 快照**——供刻意
    「不回灌对话历史」的云端自动插画使用（2026-10-16：画布需要 generation 记录
    才能投影出内容节点，而插画产物不得进对话历史 ⇒ 两个开关分开）。
    默认 True ＝ 既有调用行为逐字不变。
    """
    shown = _save_remote_image(image_url, output_dir, repo_id, proxy=gen_proxy)
    mid = str(uuid.uuid4())
    cfg = EmbedConfig(embed_base, embed_key, embed_model, proxy=embed_proxy)
    _index_with_retry(repo_id, cfg, prompt, image_url=shown)
    if write_snapshot:
        try:
            chat_snapshot.upsert(
                thread_id,
                chat_snapshot.assistant_message(
                    mid, "", image=shown,
                    **({"regeneration": regeneration} if regeneration else {}),
                ),
            )
        except Exception as exc:  # noqa: BLE001  失败不阻断返回，但要留痕（否则丢快照无声）
            _LOG.warning("persist_image 写快照失败 thread=%s mid=%s: %s", thread_id, mid, exc)
    return {"id": mid, "url": shown,
            **({"regeneration": regeneration} if regeneration else {})}


def _save_remote_video(url: str, output_dir: str, repo_id: str = "home",
                       proxy: str = "") -> str:
    """Agent 云视频容错留存；失败回退原地址，不阻断已完成的生成。

    proxy：视频模型代理，透传给 CDN 结果视频下载（与图片同链路）。
    """
    if not output_dir or not url:
        return url
    try:
        if url.startswith("data:"):
            header, b64 = url.split(",", 1)
            data = base64.b64decode(re.sub(r"\s+", "", b64))
            ext = "mp4"
            if "video/" in header:
                ext = header.split("video/")[1].split(";")[0] or "mp4"
            name = f"{uuid.uuid4().hex}.{safe_seg(ext)}"
        else:
            data = _download_capped(url, timeout=300, max_bytes=_MAX_VIDEO_BYTES,
                                    proxy=proxy,
                                    total_timeout=_DOWNLOAD_TOTAL_TIMEOUT_VIDEO)
            tail = safe_seg(Path(url.split("?")[0]).name)
            name = tail if "." in tail else f"{uuid.uuid4().hex}.mp4"
        dest = image_store.media_dir(repo_meta.repo_folder(output_dir, repo_id), "video") / name  # → videos/
        dest.write_bytes(data)
        return view_urls.local_view(str(dest))
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("远程视频留存失败，回退远程地址 url=%s: %s", url, exc)
        return url


def persist_video(thread_id: str, repo_id: str, prompt: str, video_url: str,
                  output_dir: str, vid_proxy: str = "") -> dict:
    """Agent 视频：容错留存后追加快照，返回前后端共用身份 {id,url}。

    vid_proxy：视频模型代理，透传给 CDN 结果视频下载。
    """
    shown = _save_remote_video(video_url, output_dir, repo_id, proxy=vid_proxy)
    mid = str(uuid.uuid4())
    try:
        chat_snapshot.upsert(thread_id, chat_snapshot.assistant_message(mid, "", video=shown))
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("persist_video 写快照失败 thread=%s mid=%s: %s", thread_id, mid, exc)
    return {"id": mid, "url": shown}


def _workflow_key(thread_id: str, prompt_id: str, image: dict | None = None) -> str:
    base = f"workflow:{thread_id}:{prompt_id}"
    if image is None:
        return f"{base}:text"
    return ":".join((base, str(image.get("type", "output")),
                     str(image.get("subfolder", "")), str(image.get("filename", ""))))


def _message_id(key: str) -> str:
    return str(uuid.uuid5(_WORKFLOW_NAMESPACE, key))


def _extract_tags(prompt: str, base_url: str, api_key: str, model: str) -> str:
    if not prompt.strip() or not base_url or not model:
        return ""
    try:
        from app.services import llm  # service 层直接调 llm，不经 routers（守分层）
        system = (
            "你是标签提取助手。把给定的绘画提示词切分成 4-8 个简短关键词标签，"
            "覆盖主体、风格、场景、光影等要点。中文提示词输出中文标签。"
            "只输出标签本身，用英文逗号分隔，不要解释、不要编号、不要换行。"
        )
        out = llm.chat(base_url, api_key, model, system, prompt, temperature=0.2)
        return ",".join(t.strip() for t in re.split(r"[,，;；\n]+", out) if t.strip())
    except Exception as exc:
        _LOG.debug("标签反推失败，返回空串: %s", exc)
        return ""


def _audio_slot_meta(thread_id: str, message_id: str, slot_id: str) -> dict:
    """从快照读音频槽的角色名/本轮序号（用于生成可读文件名）。"""
    try:
        items = chat_snapshot.load(thread_id)
    except Exception:  # noqa: BLE001
        return {"speaker": "", "seq": 0}
    for item in items:
        if not isinstance(item, dict) or item.get("id") != message_id:
            continue
        for part in item.get("parts") or []:
            if isinstance(part, dict) and part.get("slotId") == slot_id:
                return {
                    "speaker": str(part.get("speaker") or ""),
                    "seq": part.get("seq") or 0,
                }
    return {"speaker": "", "seq": 0}


def _audio_turn(thread_id: str, message_id: str) -> int:
    """第几次「带配音的对话」：快照中该消息之前（含）含音频槽的 assistant 消息数。

    audio 槽在 finalize 前仍是 media-slot（kind=audio），已完成的则是 type=audio part，
    两者都算「带配音」，故用 kind/type 双匹配。
    """
    try:
        items = chat_snapshot.load(thread_id)
    except Exception:  # noqa: BLE001
        return 0
    turn = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        has_audio = any(
            isinstance(p, dict)
            and (p.get("type") == "audio" or p.get("kind") == "audio")
            for p in (item.get("parts") or [])
        )
        if has_audio:
            turn += 1
        if item.get("id") == message_id:
            break
    return turn


def _image_turn(thread_id: str, message_id: str) -> int:
    """第几次「带插画的对话」：快照中该消息之前（含）含图片/插画槽的 assistant 消息数。

    与 _audio_turn 同语义，只是媒体类型换图片（type=image 或 kind=image）。
    """
    try:
        items = chat_snapshot.load(thread_id)
    except Exception:  # noqa: BLE001
        return 0
    turn = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        has_image = any(
            isinstance(p, dict)
            and (p.get("type") == "image" or p.get("kind") == "image")
            for p in (item.get("parts") or [])
        )
        if has_image:
            turn += 1
        if item.get("id") == message_id:
            break
    return turn


# 插画／配音槽 id 的真源格式＝`roleplay_turn.finalize_turn` 里 `illo-req-{repo_id}-{draft.turn}`
# 与 `audio-req-{repo_id}-{draft.turn}`（`draft.turn` 是**真实回合号**）；首尾帧/转场副槽在
# 前端以 `:first` / `:last` / `:transition` 后缀挂在同一 id 上（`useIllustrationChain`）。
_SLOT_TURN_RE = re.compile(
    r"^(?:illo|audio)-req-(?P<repo>.+)-(?P<turn>\d+)(?::(?P<sub>[A-Za-z_]+))?$",
)


def _slot_turn(slot_id: str) -> tuple[int, str]:
    """从槽 id 解析**真实回合号**与副槽后缀；解析不出来返回 `(0, "")`。

    2026-09-22 用户实锤（资产覆盖）：`{角色}_{轮次}_{序号}` 里的「轮次」原先取
    `_image_turn`（快照里含图片的 assistant 消息数）。而 finalize 落盘时本槽在快照里
    还是 `media-slot`（pending）⇒ 计数**不含自己**，与**上一轮**算出来的数相同
    （真机：第 46 轮算出 33＝第 45 轮的号）⇒ `save_image_named` 同名覆盖：
    两个槽指向同一 URL、刷新后第 45 轮的槽显示第 46 轮的图、第 45 轮的资产条目被顶掉。

    槽 id 里的 `draft.turn` 是**生成时就已经确定的真实回合号**，取它即可：
      · 相邻回合必然不同 ⇒ 不同槽永不撞名；
      · **同一槽重生成**前后同名 ⇒ 仍走既有「同名覆盖、URL 不变」语义（这是特性，不是 bug）；
      · 首尾帧副槽带 `_first` / `_last` ⇒ 同回合内的双帧图也互不撞名。
    ⚠ 解析失败（老调用方／测试里的 `img-bot-0` 之类）⇒ 返回 0，调用方回落旧计数 ⇒ 零回归。
    """
    match = _SLOT_TURN_RE.match(str(slot_id or "").strip())
    if not match:
        return 0, ""
    return int(match.group("turn")), str(match.group("sub") or "")


def finalize_workflow_batch(
    *, thread_id: str, repo_id: str, prompt_id: str, prompt: str,
    images: list[dict], output_dir: str, comfyui_url: str,
    embed_base: str, embed_key: str, embed_model: str,
    chat_base: str = "", chat_key: str = "", chat_model: str = "",
    videos: list[dict] | None = None,
    audios: list[dict] | None = None,
    regeneration: dict | None = None,
    template_name: str = "",
    model_name: str = "",
    lora_names: str = "",
    target_message_id: str = "",
    target_slot_id: str = "",
    base_slot_ref: dict | None = None,  # M2.1 视频首帧底图槽引用 {message_id, slot_id}
) -> dict:
    """持久化一批已完成的 ComfyUI 产出；单图/单视频/单音频阶段失败不会阻断其他产出。
    videos/audios 与 images 同结构({filename,subfolder,type})，消息以 video/audio 字段承载。
    base_slot_ref 供视频入库记录 derived_from（底图槽弱引用，来源删除不级联）。"""
    videos = videos or []
    audios = audios or []
    if not thread_id or not repo_id or not prompt_id:
        raise ValueError("thread_id、repo_id 和 prompt_id 不能为空")
    if not images and not videos and not audios and not prompt.strip():
        raise ValueError("生成结果没有图片、视频、音频或文字")

    durable = repo_id != "home"
    cfg = EmbedConfig(embed_base, embed_key, embed_model)
    # 标签提取仅服务图片/纯文字批次的入库检索；纯音频批次（自动配音）无图可索引，
    # 跳过以免同步 LLM 调用（超时可达 200s）阻塞 finalize，让音频槽位迟迟不填充。
    needs_tags = bool(images) or (not images and not videos and not audios and bool(prompt.strip()))
    tags = _extract_tags(prompt, chat_base, chat_key, chat_model) if durable and needs_tags else ""
    messages: list[dict] = []
    results: list[dict] = []
    inline_target = bool(target_message_id and target_slot_id)
    target: dict | None = None

    # 角色 LoRA 生图：用可读名（角色_轮次_序号）落盘；非角色 LoRA（兜底风格/无）沿用时间戳命名。
    # 前端在 regeneration 里带 characterLoraActor（首个命中角色 LoRA 的角色名），空串=非角色 LoRA。
    character_actor = ""
    if regeneration and isinstance(regeneration, dict):
        character_actor = str(regeneration.get("characterLoraActor") or "")

    for index, image in enumerate(images):
        key = _workflow_key(thread_id, prompt_id, image)
        mid = _message_id(key)
        persisted = indexed = snapshotted = False
        errors: list[str] = []
        shown = ""
        if durable:
            try:
                if character_actor:
                    # 2026-09-22：轮次优先取**槽 id 里的真实回合号**（见 `_slot_turn`），
                    # 取不到才回落「快照含图消息数」的旧口径（零回归）。
                    turn, sub = _slot_turn(target_slot_id) if inline_target else (0, "")
                    if not turn:
                        turn = _image_turn(thread_id, target_message_id) if inline_target else 0
                        sub = ""
                    path = image_store.save_image_named(
                        output_dir, repo_id,
                        filename=str(image.get("filename", "")),
                        subfolder=str(image.get("subfolder", "")),
                        type=str(image.get("type", "output")),
                        url=comfyui_url,
                        dest_stem=f"{character_actor}_{turn}{f'_{sub}' if sub else ''}_{index + 1}",
                    )
                else:
                    path = image_store.save_local(
                        output_dir, repo_id,
                        filename=str(image.get("filename", "")),
                        subfolder=str(image.get("subfolder", "")),
                        type=str(image.get("type", "output")),
                        url=comfyui_url,
                        idempotency_key=key,
                    )
                shown = view_urls.local_view(path)
                persisted = True
            except Exception as exc:  # 保留在线图，不影响本批其他产出
                _LOG.debug("留存失败(persist) key=%s，保留在线图: %s", key, exc)
                errors.append("persist")
        if not shown:
            shown = view_urls.remote_view(
                filename=str(image.get("filename", "")),
                type=str(image.get("type", "output")),
                subfolder=str(image.get("subfolder", "")),
                comfyui_url=comfyui_url,
            )

        message = chat_snapshot.assistant_message(
            mid, prompt if index == 0 else "", image=shown,
            **({"regeneration": regeneration} if regeneration else {}),
        )
        if not inline_target:
            messages.append(message)
        if durable:
            indexed = _index_with_retry(repo_id, cfg, prompt, tags, shown,
                                       template_name=template_name,
                                       model_name=model_name,
                                       lora_names=lora_names)
            if not indexed:
                errors.append("index")
            if inline_target and target is None:
                try:
                    snapshotted = chat_snapshot.resolve_media_slot(
                        thread_id, target_message_id, target_slot_id, shown,
                        media_type="image", regeneration=regeneration,
                    )
                    if not snapshotted:
                        errors.append("snapshot")
                except Exception as exc:
                    _LOG.debug("图片快照 upsert 失败 thread=%s: %s", thread_id, exc)
                    errors.append("snapshot")
            elif not inline_target:
                try:
                    chat_snapshot.upsert(thread_id, message)
                    snapshotted = True
                except Exception as exc:
                    _LOG.debug("消息快照 upsert 失败 thread=%s: %s", thread_id, exc)
                    errors.append("snapshot")
        if inline_target and target is None:
            target = {"message_id": target_message_id, "slot_id": target_slot_id,
                      "media_type": "image", "url": shown}
        results.append({
            "key": key, "message_id": mid, "display_url": shown,
            "persisted": persisted, "indexed": indexed,
            "snapshotted": snapshotted, "errors": errors,
        })

    for index, video in enumerate(videos):
        key = _workflow_key(thread_id, prompt_id, video)
        mid = _message_id(key)
        persisted = indexed = snapshotted = False
        errors = []  # 同函数复位不重声明
        shown = ""
        if durable:
            try:
                path = image_store.save_local(
                    output_dir, repo_id,
                    filename=str(video.get("filename", "")),
                    subfolder=str(video.get("subfolder", "")),
                    type=str(video.get("type", "output")),
                    url=comfyui_url,
                    idempotency_key=key,
                )
                shown = view_urls.local_view(path)
                persisted = True
            except Exception as exc:
                _LOG.debug("留存失败(persist) key=%s: %s", key, exc)
                errors.append("persist")
        if not shown:
            shown = view_urls.remote_view(
                filename=str(video.get("filename", "")),
                type=str(video.get("type", "output")),
                subfolder=str(video.get("subfolder", "")),
                comfyui_url=comfyui_url,
            )
        # 首个产物（无图时）承载提示词文本
        head_text = prompt if (index == 0 and not images) else ""
        message = chat_snapshot.assistant_message(mid, head_text, video=shown)
        if not inline_target:
            messages.append(message)
        if durable:
            # M2.1：视频入库资产库（media_type=video），derived_from 记首帧底图槽弱引用
            derived = [{
                "media_slot_ref": {"message_id": str(base_slot_ref.get("message_id", "")),
                                   "slot_id": str(base_slot_ref.get("slot_id", ""))},
                "kind": "video_base_image",
            }] if base_slot_ref else None
            indexed = _index_with_retry(repo_id, cfg, prompt, "", shown,
                                        template_name=template_name,
                                        model_name=model_name,
                                        lora_names=lora_names,
                                        media_type="video", derived_from=derived)
            if not indexed:
                errors.append("index")
            if inline_target and target is None:
                try:
                    snapshotted = chat_snapshot.resolve_media_slot(
                        thread_id, target_message_id, target_slot_id, shown,
                        media_type="video", regeneration=regeneration,
                        derived_from=derived,
                    )
                    if not snapshotted:
                        errors.append("snapshot")
                except Exception as exc:
                    _LOG.debug("视频快照 upsert 失败 thread=%s: %s", thread_id, exc)
                    errors.append("snapshot")
            elif not inline_target:
                try:
                    chat_snapshot.upsert(thread_id, message)
                    snapshotted = True
                except Exception as exc:
                    _LOG.debug("消息快照 upsert 失败 thread=%s: %s", thread_id, exc)
                    errors.append("snapshot")
        if inline_target and target is None:
            target = {"message_id": target_message_id, "slot_id": target_slot_id,
                      "media_type": "video", "url": shown}
        results.append({
            "key": key, "message_id": mid, "display_url": shown,
            "persisted": persisted, "indexed": indexed,
            "snapshotted": snapshotted, "errors": errors,
        })

    for index, audio in enumerate(audios):
        key = _workflow_key(thread_id, prompt_id, audio)
        mid = _message_id(key)
        persisted = snapshotted = False
        errors = []  # 类型已由 L402 声明（同函数多消息块各自复位）
        shown = ""
        if durable:
            # 配音分条：存 voices/ 并按「角色_轮次_句号」命名；无槽位元数据时回退旧逻辑
            meta = _audio_slot_meta(thread_id, target_message_id, target_slot_id) if inline_target else {}
            speaker = str(meta.get("speaker") or "")
            seq_num = meta.get("seq") or 0
            turn = _audio_turn(thread_id, target_message_id) if inline_target else 0
            try:
                if speaker:
                    path = image_store.save_audio_local(
                        output_dir, repo_id,
                        filename=str(audio.get("filename", "")),
                        subfolder=str(audio.get("subfolder", "")),
                        type=str(audio.get("type", "output")),
                        url=comfyui_url,
                        dest_stem=f"{speaker}_{turn}_{seq_num}",
                    )
                else:
                    path = image_store.save_local(
                        output_dir, repo_id,
                        filename=str(audio.get("filename", "")),
                        subfolder=str(audio.get("subfolder", "")),
                        type=str(audio.get("type", "output")),
                        url=comfyui_url,
                        idempotency_key=key,
                    )
                shown = view_urls.local_view(path)
                persisted = True
            except Exception as exc:
                _LOG.debug("留存失败(persist) key=%s: %s", key, exc)
                errors.append("persist")
        if not shown:
            shown = view_urls.remote_view(
                filename=str(audio.get("filename", "")),
                type=str(audio.get("type", "output")),
                subfolder=str(audio.get("subfolder", "")),
                comfyui_url=comfyui_url,
            )
        # 首个产物（无图/无视频时）承载提示词文本
        head_text = prompt if (index == 0 and not images and not videos) else ""
        message = chat_snapshot.assistant_message(mid, head_text, audio=shown)
        if not inline_target:
            messages.append(message)
        if durable:
            if inline_target and target is None:
                try:
                    snapshotted = chat_snapshot.resolve_media_slot(
                        thread_id, target_message_id, target_slot_id, shown,
                        media_type="audio", regeneration=regeneration,
                    )
                    if not snapshotted:
                        errors.append("snapshot")
                except Exception as exc:
                    _LOG.debug("音频快照 upsert 失败 thread=%s: %s", thread_id, exc)
                    errors.append("snapshot")
            elif not inline_target:
                try:
                    chat_snapshot.upsert(thread_id, message)
                    snapshotted = True
                except Exception as exc:
                    _LOG.debug("消息快照 upsert 失败 thread=%s: %s", thread_id, exc)
                    errors.append("snapshot")
        if inline_target and target is None:
            target = {"message_id": target_message_id, "slot_id": target_slot_id,
                      "media_type": "audio", "url": shown}
        results.append({
            "key": key, "message_id": mid, "display_url": shown,
            "persisted": persisted, "indexed": False,
            "snapshotted": snapshotted, "errors": errors,
        })

    if not images and not videos and not audios and prompt.strip():
        mid = _message_id(_workflow_key(thread_id, prompt_id))
        message = chat_snapshot.assistant_message(mid, prompt)
        messages.append(message)
        indexed = snapshotted = False
        errors = []  # 同上：复位不重声明
        if durable:
            indexed = _index_with_retry(repo_id, cfg, prompt, tags,
                                       template_name=template_name,
                                       model_name=model_name,
                                       lora_names=lora_names)
            if not indexed:
                errors.append("index")
            try:
                chat_snapshot.upsert(thread_id, message)
                snapshotted = True
            except Exception as exc:
                _LOG.debug("快照 upsert 失败 thread=%s: %s", thread_id, exc)
                errors.append("snapshot")
        results.append({
            "key": _workflow_key(thread_id, prompt_id), "message_id": mid,
            "display_url": "", "persisted": False, "indexed": indexed,
            "snapshotted": snapshotted, "errors": errors,
        })

    if durable and not inline_target:
        memory_key = _workflow_key(thread_id, prompt_id)
        if memory_key not in _MEMORY_DONE:
            try:
                chat_memory.append_message(
                    thread_id, "assistant", prompt,
                    [message["image"] for message in messages if message.get("image")] or None,
                )
                _MEMORY_DONE.add(memory_key)
            except Exception as exc:  # noqa: BLE001  记忆写入失败不阻断返回，但要留痕
                _LOG.warning("finalize_workflow_batch 写记忆失败 thread=%s prompt_id=%s: %s",
                             thread_id, prompt_id, exc)

    complete = durable and all(not item["errors"] for item in results)
    return {"prompt_id": prompt_id, "durable": durable, "messages": messages,
            "images": results, "complete": complete, "target": target}


def persist_inspiration(thread_id: str, title: str, content: str,
                        sources: list[dict], images: list[dict] | None = None) -> dict:
    """落一张灵感卡。返回含全量 images 的 card（供 wire 传前端预览）。

    会话快照存 {title, content, sources, selected, images}——**images 于 2026-09-11
    晚入快照**（合同变更）：只存远程 URL 元信息（thumb/full/source/title，≤8 条 ≈2KB，
    无膨胀风险），防「队列 headless 完成后从快照重载 / 刷新页面」时卡片图全灭
    （当轮 SSE 的 images 只活在事件里，一重载就没了——真机实锤：SSE 事件带 8 图，
    快照重载后 0 图）。用户选中项仍由 /inspiration/select 端点写回 selected。
    """
    slim_images = [
        {"thumb_url": (im.get("thumb_url") or "")[:500],
         "full_url": (im.get("full_url") or "")[:800],
         "source_url": (im.get("source_url") or "")[:500],
         "title": (im.get("title") or "")[:200]}
        for im in (images or [])[:8] if isinstance(im, dict) and im.get("full_url")
    ]
    card: dict[str, Any] = {"id": str(uuid.uuid4()), "title": title, "content": content,
                            "sources": sources, "images": images or []}
    try:
        chat_snapshot.upsert(thread_id, chat_snapshot.assistant_message(
            card["id"], "",
            inspiration={"title": title, "content": content,
                         "sources": sources, "selected": [],
                         "images": slim_images},
        ))
    except Exception as exc:  # noqa: BLE001  灵感卡写快照失败不阻断返回，但要留痕
        _LOG.warning("persist_inspiration 写快照失败 thread=%s: %s", thread_id, exc)
    return card


def persist_text(thread_id: str, message_id: str, text: str,
                 interrupted: bool = False) -> None:
    if not (text or "").strip() or not message_id:
        return
    try:
        if interrupted:
            # 中断半成品：文本拼「（已打断）」后缀再落快照（与 chat_memory.append_turn /
            # agent_graph._resume_interrupted_messages 同标记，见 chat_snapshot.INTERRUPT_MARKER）。
            # 后缀是跨轮接续识别的唯一信号（to_prompt_history 只带 role/content），
            # 不带则刷新后下一轮永远匹配不到 → 观感=从头再来（2026-09-04 收口）。
            base = (text or "").strip()
            if not base.endswith(chat_snapshot.INTERRUPT_MARKER):
                base += chat_snapshot.INTERRUPT_MARKER
            chat_snapshot.upsert(
                thread_id,
                chat_snapshot.assistant_message(message_id, base, interrupted=True),
            )
        else:
            chat_snapshot.append_text(thread_id, message_id, text)
    except Exception as exc:  # noqa: BLE001  文本落盘失败不阻断收尾，但要留痕
        _LOG.warning("persist_text 落盘失败 thread=%s mid=%s: %s", thread_id, message_id, exc)


def persist_user_message(
    thread_id: str, message_id: str, text: str, images: list[str] | None = None,
    attachments: list[dict] | None = None,
) -> None:
    """在 Agent 开始前持久化用户气泡，断线或前端快照竞态也不能只剩助手回复。"""
    try:
        chat_snapshot.ensure_user_message(thread_id, message_id, text, images, attachments)
    except Exception as exc:  # noqa: BLE001 用户输入持久化失败由 Trace 记录，调用仍可返回错误
        _LOG.warning(
            "persist_user_message 落盘失败 thread=%s mid=%s: %s",
            thread_id, message_id, exc,
        )


def persist_media_slot(thread_id: str, message_id: str, slot_id: str,
                       offset: int | None = None) -> None:
    """即时持久化自动插画槽，保证前端离页后异步产物仍有原位回填目标。"""
    try:
        chat_snapshot.ensure_media_slot(
            thread_id, message_id, slot_id, offset=offset,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("persist_media_slot 落盘失败 thread=%s mid=%s slot=%s: %s",
                     thread_id, message_id, slot_id, exc)


def persist_audio_slot(thread_id: str, message_id: str, slot_id: str, *,
                       speaker: str | None = None,
                       seq: int | None = None, total: int | None = None) -> None:
    """持久化音频对白槽（追加式，保留已有图片/视频槽）；失败不阻断配音任务。"""
    try:
        chat_snapshot.append_media_slot(
            thread_id, message_id, slot_id,
            kind="audio", speaker=speaker, seq=seq, total=total,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("persist_audio_slot 落盘失败 thread=%s mid=%s slot=%s: %s",
                     thread_id, message_id, slot_id, exc)


def persist_illustration_submission(*, thread_id: str, message_id: str,
                                    slot_id: str, prompt_id: str) -> bool:
    """持久化自动插画的 ComfyUI 任务身份；失败不影响已提交的任务。"""
    try:
        return chat_snapshot.bind_media_slot_prompt(
            thread_id, message_id, slot_id, prompt_id,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("插画 prompt_id 落盘失败 thread=%s mid=%s slot=%s: %s",
                     thread_id, message_id, slot_id, exc)
        return False


def claim_illustration_submission(*, thread_id: str, message_id: str, slot_id: str) -> bool:
    """在调用 ComfyUI 前认领权威媒体槽，阻止重复/过期事件产生额外任务。

    2026-08-31 晚实锤「新的任务请求没有生图」：前端收到 illustrate_request、槽已建，
    却无 illustration.submitted / illustration.failed——失败静默。认领结果从此落
    trace（illustration.claim），复现时一眼能分：槽未就绪 / 已认领 / 消息缺失 / 异常。
    """
    ctx = {"thread_id": thread_id, "repo_id": thread_id}
    try:
        claimed = chat_snapshot.claim_media_slot_submission(
            thread_id, message_id, slot_id,
        )
        run_trace.emit(
            ctx, "illustration.claim",
            message_id=message_id, slot_id=slot_id, claimed=claimed,
        )
        return claimed
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("插画提交认领失败 thread=%s mid=%s slot=%s: %s",
                     thread_id, message_id, slot_id, exc)
        run_trace.emit(
            ctx, "illustration.claim",
            message_id=message_id, slot_id=slot_id, claimed=False, error=str(exc),
        )
        return False


def persist_illustration_failure(
    *, thread_id: str, repo_id: str, message_id: str, slot_id: str,
    stage: str, error: str, prompt_id: str = "",
) -> bool:
    """记录自动插画失败并清除快照槽；失败信息不得进入对话正文。"""
    removed = False
    try:
        removed = chat_snapshot.remove_media_slot(thread_id, message_id, slot_id)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("自动插画失败槽清理失败 thread=%s mid=%s slot=%s: %s",
                     thread_id, message_id, slot_id, exc)
    run_trace.emit(
        {"thread_id": thread_id, "repo_id": repo_id},
        "illustration.failed",
        message_id=message_id, slot_id=slot_id, stage=stage, error=error,
        prompt_id=prompt_id, slot_removed=removed,
    )
    return removed


def persist_prompt_approval(thread_id: str, approval: dict) -> None:
    message_id = str(approval.get("messageId") or "")
    if not message_id:
        return
    try:
        chat_snapshot.merge_fields(thread_id, message_id, promptApproval=approval)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("persist_prompt_approval 落盘失败 thread=%s mid=%s: %s",
                     thread_id, message_id, exc)


def persist_route_choice(thread_id: str, route_choice: dict) -> None:
    message_id = str(route_choice.get("messageId") or "")
    if not message_id:
        return
    try:
        chat_snapshot.merge_fields(thread_id, message_id, routeChoice=route_choice)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("persist_route_choice 落盘失败 thread=%s mid=%s: %s",
                     thread_id, message_id, exc)
