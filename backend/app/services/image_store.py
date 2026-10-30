"""把图片留存到本地 outputDir（全分辨率，不降质）。

两种来源的字节获取与文件名推断集中于此；ComfyUI /view 取图仍走 comfyui_client。
纯逻辑（文件名清洗、data URI 解码、扩展名推断）可脱离 HTTP 单测。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from app.config import COMFYUI_BASE_URL
from app.services import comfyui_client
from app.services.comfyui_client import ComfyError
from app.services.pathnames import safe_dir, safe_seg


def _next_seq_name(base: Path, ext: str) -> str:
    """时间戳 + 随机后缀命名，如 20260703_153012_874321_a1b2c3d4.png。

    不再靠「扫描磁盘取 max+1」——那套机制三处脆弱：删末尾图后 max 回退→新图撞旧编号；
    删中间图留空洞；用户手动改名会干扰 max 计算。时间戳前缀单调递增、永不复用、
    完全不依赖磁盘现状，删任何图/手动改名都不影响后续命名。随机后缀再防同一微秒内并发撞名。
    字典序 = 时间序，前端按文件名排序仍从新到旧（且已额外落 created_at 作权威排序，见 index_generation）。
    """
    ext = safe_seg(ext) or "png"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return f"{ts}_{uuid4().hex[:8]}.{ext}"


# 仓库内「按产出类型分文件夹」（2026-10-17 用户定案）：图片/视频/音频各一夹，
# 根目录只留应用数据（state.json/chat.json/世界书/数据库）。<repo>/pictures|videos|audios。
MEDIA_DIRS = {"image": "pictures", "video": "videos", "audio": "audios"}


def media_dir(base: Path, kind: str = "image") -> Path:
    """<repo>/pictures|videos|audios —— 按类型归置媒体产出（不存在则创建）。"""
    d = Path(base) / MEDIA_DIRS.get(kind, "pictures")
    d.mkdir(parents=True, exist_ok=True)
    return d



# 图站 CDN host（danbooru / pixiv）：下载有防盗链（需 Referer/UA），且本机 Python HTTP
# 栈抓大文件易挂（2026-10-16 实测 httpx 抓 cdn.donmai.us 挂死 >200s、curl 20s）
_IMAGE_SOURCE_HOSTS = ("donmai.us", "pximg.net", "pixiv.net")


def _download_external_url(url: str, max_bytes: int = 20 * 1024 * 1024) -> bytes:
    """下载公网 URL，重定向逐跳校验（防校验后重定向 SSRF 绕过）。

    旧实现用 urllib.urlopen：默认自动跟随重定向，重定向目标完全不校验，
    外部 URL 可 302 跳到私网/metadata 地址，把内网响应下载回作品目录。
    改为 httpx follow_redirects=False + 每跳「校验+钉扎→建连」（pinned_media_url：
    连接只用校验解析到的 IP，rebinding 在校验与建连之间换 IP 无效，见 url_guard）。
    trust_env 保持默认 True，保留「外部 URL 走系统代理」的既有通路。

    2026-10-16：图站 CDN 分支改走 `material_sources.fetch_image_bytes`——那里有
    host 白名单（防 SSRF，等价或更严）+ curl 优先通路 + 带 Referer/UA 解防盗链
    （用户实测「下载失败 403 Forbidden」即缺 Referer 所致）。
    """
    from app.services.url_guard import pinned_media_url

    current = url
    with httpx.Client(follow_redirects=False, timeout=30,
                      headers={"User-Agent": "Demiurge/1.0"}) as client:
        for hop in range(5):
            redirect = False
            try:
                with pinned_media_url(current) as pinned:
                    host = (httpx.URL(pinned).host or "").lower()
                    if any(host == h or host.endswith("." + h) for h in _IMAGE_SOURCE_HOSTS):
                        from app.services import material_sources
                        from app.services.repo_meta import setting_str_from_state

                        px = (setting_str_from_state("systemProxyUrl")
                              or setting_str_from_state("proxyUrl"))
                        try:
                            data, _ctype = material_sources.fetch_image_bytes(pinned, proxy=px)
                        except RuntimeError as exc:
                            raise ComfyError(f"下载图片失败：{exc}", 502) from exc
                        if len(data) > max_bytes:
                            raise ComfyError(f"下载超过 {max_bytes // (1024 * 1024)}MB 上限", 400)
                        return data

                    resp = client.get(pinned)
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = (resp.headers.get("location") or "").strip()
                        if not location:
                            raise ComfyError("重定向响应缺少 Location", 502)
                        # 下一跳由下一轮 pinned_media_url 校验+钉扎后才建连
                        current = str(httpx.URL(pinned).join(location))
                        redirect = True
            except ValueError as exc:
                # ComfyError 不是 ValueError 子类，不受此分支影响。
                # 首跳＝原始 URL 校验失败（保持 ValueError，由 _from_src 统一转 400）；
                # 后续跳＝重定向目标被拒。
                if hop == 0:
                    raise
                raise ComfyError(f"重定向目标被拒：{exc}", 400) from exc
            if redirect:
                continue
            resp.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ComfyError(f"下载超过 {max_bytes // (1024 * 1024)}MB 上限", 400)
                chunks.append(chunk)
            return b"".join(chunks)
    raise ComfyError("重定向跳数过多（>5）", 502)


def _from_src(src: str) -> tuple[bytes, str]:
    """通用模式：data URI 解码或外部 URL 下载，返回 (data, 扩展名)。"""
    if src.startswith("data:"):
        try:
            header, b64 = src.split(",", 1)
            data = base64.b64decode(b64)
        except Exception as e:
            raise ComfyError(f"解析 data URI 失败：{e}", 400)
        if len(data) > 20 * 1024 * 1024:
            raise ComfyError("data URI 图片超过 20MB 上限", 400)
        ext = "png"
        if "image/" in header:
            ext = header.split("image/")[1].split(";")[0] or "png"
        elif "video/" in header:
            ext = header.split("video/")[1].split(";")[0] or "mp4"
        return data, ext
    # 校验外部 URL 防 SSRF；本应用 local-view 代理地址豁免（已在后端可信路径落盘）
    from app.services import local_auth, view_urls
    from app.services.url_guard import is_local_view_url, validate_media_url
    if not is_local_view_url(src):
        try:
            validate_media_url(src)
        except ValueError as e:
            raise ComfyError(str(e), 400)
    try:
        # local-view 指向本机后端（config 的 BACKEND_BASE_URL），必须绕过系统代理——
        # Windows 系统代理（如 Clash 127.0.0.1:7897）会把 localhost 请求转发出去导致 502。
        # 外部 URL 走 _download_external_url（httpx 默认读系统环境代理，中转通路依赖它），
        # 且重定向逐跳校验，防校验后重定向绕过 SSRF 防护。
        if is_local_view_url(src):
            import urllib.request
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            # local-view 的 wire 形态是根相对（属主 view_urls），urllib 只吃绝对地址
            # ⇒ 先按本机实际监听端口拼回 origin；已是绝对地址的原样通过。
            # 自取图同属本机 API：移动端启用 LAF_LOCAL_TOKEN 后必须带令牌头（桌面为空）。
            request = urllib.request.Request(view_urls.fetchable(src),
                                             headers=local_auth.outbound_headers())
            with opener.open(request, timeout=30) as r:
                data = r.read(20 * 1024 * 1024)  # 最大 20 MB，防超大文件撑爆内存
        else:
            data = _download_external_url(src)
    except ComfyError:
        raise
    except Exception as e:
        raise ComfyError(f"下载图片失败：{e}", 502)
    tail = Path(src.split("?")[0]).name
    ext = tail.rsplit(".", 1)[1] if "." in tail else "png"
    return data, ext


def save_local(
    output_dir: str,
    repo_id: str = "home",
    *,
    src: str = "",
    filename: str = "",
    subfolder: str = "",
    type: str = "output",
    url: str = COMFYUI_BASE_URL,
    subdir: str = "",
    idempotency_key: str = "",
) -> str:
    """存原图到 outputDir/<repo_id>/，返回落盘路径。

    - src 非空：通用模式（data URI / 外部 URL）。
    - 否则用 filename 从 ComfyUI /view 取原图。
    - subdir 非空：落到 <repo_id>/<subdir>/ 子文件夹（如用户上传的参考图 → reference/）。
    校验失败/取图失败抛 ComfyError，路由层转 HTTPException。
    """
    if not output_dir:
        raise ComfyError("未配置输出图片路径", 400)
    from app.services import repo_meta
    root = repo_meta.repo_folder(output_dir, repo_id)  # 文件夹名=仓库名(保中文)，并写 _repo.json
    if subdir:
        # 参考图等显式子夹保持原位（如 reference/），不并入 pictures/
        base = root / safe_seg(subdir)
        base.mkdir(parents=True, exist_ok=True)
    else:
        # 生成图按类型归入 <repo>/pictures/（2026-10-17 用户定案：产出按类型分夹）
        base = media_dir(root, "image")
    if idempotency_key and filename and not src:
        fn = Path(filename).name
        ext = fn.rsplit(".", 1)[1] if "." in fn else "png"
        suffix = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
        existing = base / f"workflow_{suffix}.{safe_seg(ext) or 'png'}"
        if existing.exists():
            return str(existing)
    data = b""
    if src:
        data, ext = _from_src(src)
    else:
        if not filename:
            raise ComfyError("缺少 filename 或 src", 400)
        fn = Path(filename).name
        ext = fn.rsplit(".", 1)[1] if "." in fn else "png"
    # 上传的参考图落 reference/ 子夹，与生成图（根目录）分开。子夹名做安全化防路径穿越。
    # 统一按本仓库文件夹自己的顺序编号命名——不沿用 ComfyUI 的 uid_编号(会随重启从头计数、
    # 删图后新图撞旧编号导致覆盖)。每个仓库独立编号，跨来源都不撞名。
    if idempotency_key:
        suffix = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
        dest = base / f"workflow_{suffix}.{safe_seg(ext) or 'png'}"
        if dest.exists():
            return str(dest)
    else:
        dest = base / _next_seq_name(base, ext)
    tmp = base / f".{dest.name}.{uuid4().hex}.tmp"
    try:
        if src:
            tmp.write_bytes(data)
        else:
            # 流式落盘：云端视频动辄数百 MB，整块读进内存会顶爆 RSS
            comfyui_client.fetch_view_to_file(url, filename, str(tmp), type, subfolder)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return str(dest)


def save_image_named(output_dir: str, repo_id: str, *,
                     filename: str, subfolder: str = "", type: str = "output",
                     url: str = COMFYUI_BASE_URL, dest_stem: str = "") -> str:
    """角色 LoRA 生图落盘：用可读名（角色_轮次_序号）存到 <repo>/ 根目录，同名覆盖。

    与 save_local 的差异：文件名由 dest_stem 决定（保留中文角色名），同一槽位
    重新生成时覆盖同名文件 → URL 不变、内容更新。取图失败抛 ComfyError。
    """
    if not output_dir:
        raise ComfyError("未配置输出图片路径", 400)
    from app.services import repo_meta
    base = media_dir(repo_meta.repo_folder(output_dir, repo_id), "image")  # 生成图 → pictures/
    fn = Path(filename).name
    ext = fn.rsplit(".", 1)[1] if "." in fn else "png"
    stem = safe_dir(dest_stem) or "image"
    dest = base / f"{stem}.{safe_dir(ext) or 'png'}"
    tmp = base / f".{dest.name}.{uuid4().hex}.tmp"
    try:
        comfyui_client.fetch_view_to_file(url, filename, str(tmp), type, subfolder)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return str(dest)


def save_audio_local(output_dir: str, repo_id: str, *,
                     filename: str, subfolder: str = "", type: str = "output",
                     url: str = COMFYUI_BASE_URL, dest_stem: str = "") -> str:
    """把生成的配音分条存到 <repo>/voices/，文件名用 dest_stem（角色_轮次_句号）。

    配音是作品语音资产，物理落 voices 子夹，与用户上传的参考音轨同目录；文件名保留
    中文角色名（safe_dir 只挡 Windows 非法字符），便于按角色/轮次检索。
    幂等：同名已存在直接返回旧路径（同一槽位重跑不重复落盘）。
    """
    if not output_dir:
        raise ComfyError("未配置输出路径", 400)
    from app.services import repo_meta
    base = repo_meta.repo_folder(output_dir, repo_id) / "voices"
    base.mkdir(parents=True, exist_ok=True)
    fn = Path(filename).name
    ext = fn.rsplit(".", 1)[1] if "." in fn else "flac"
    ext = safe_dir(ext) or "flac"
    stem = safe_dir(dest_stem) or "voice"
    dest = base / f"{stem}.{ext}"
    if dest.exists():
        return str(dest)
    tmp = base / f".{dest.name}.{uuid4().hex}.tmp"
    try:
        comfyui_client.fetch_view_to_file(url, filename, str(tmp), type, subfolder)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return str(dest)


# ===== 上网素材：联网搜索下载的图片，存到 outputDir/_web_materials/ =====


def web_materials_dir(output_dir: str) -> Path:
    """上网素材目录：outputDir/_web_materials/。"""
    return Path(output_dir) / "_web_materials"


def _web_material_provenance_path(d: Path) -> Path:
    """provenance 记录文件：_web_materials/.provenance.json（filename → 元数据）。"""
    return d / ".provenance.json"


def _load_provenance(d: Path) -> dict:
    try:
        return json.loads(_web_material_provenance_path(d).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_provenance(d: Path, provenance: dict) -> None:
    """原子写 provenance（临时文件 + rename），失败静默（追溯缺失不阻断下载）。"""
    try:
        p = _web_material_provenance_path(d)
        tmp = p.with_name(f".{p.name}.{uuid4().hex}.tmp")
        tmp.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def load_web_material_provenance(output_dir: str) -> dict:
    """读 _web_materials/.provenance.json（离线资源包合并用；缺失/损坏返回空）。"""
    return _load_provenance(web_materials_dir(output_dir))


def save_web_material_provenance(output_dir: str, provenance: dict) -> None:
    """写回 _web_materials/.provenance.json（provenance 属主仍在本模块）。"""
    target = web_materials_dir(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    _save_provenance(target, provenance)


def _check_domain_allowlist(url: str) -> None:
    """域名策略（M1.3）：默认仅 https 可配白名单。

    - 默认只允许 https 公网图片（http 明文图床/不可信源拒绝）。
    - 环境变量 WEB_MATERIAL_ALLOWED_DOMAINS（逗号分隔）可放行额外域名，
      支持子域（allow 填 example.com 放行 img.example.com）。
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if scheme == "https":
        return
    if scheme != "http":
        raise ComfyError(f"图片 URL 只支持 https（当前 {scheme!r}）", 400)
    allowed = [h.strip().lower() for h in
               os.environ.get("WEB_MATERIAL_ALLOWED_DOMAINS", "").split(",") if h.strip()]
    if not allowed:
        raise ComfyError("图片 URL 使用 http 明文协议，已拒绝（可在 WEB_MATERIAL_ALLOWED_DOMAINS 放行指定域名）", 400)
    if host and any(host == a or host.endswith("." + a) for a in allowed):
        return
    raise ComfyError(f"图片 URL 域名 {host!r} 不在白名单内", 400)


def _is_thread_inspiration_image(thread_id: str, url: str) -> bool:
    """该会话快照里灵感卡是否含此 full_url（重启后候选列表丢失，仍可保存旧卡图片）。"""
    if not thread_id or not url:
        return False
    try:
        from app.services import chat_snapshot
        items = chat_snapshot.load(thread_id)
    except Exception:  # noqa: BLE001
        return False
    url = url.strip()
    for item in items:
        insp = item.get("inspiration") if isinstance(item, dict) else None
        if not isinstance(insp, dict):
            continue
        for image in insp.get("images") or []:
            if isinstance(image, dict) and str(image.get("full_url") or "").strip() == url:
                return True
    return False


def save_web_material(output_dir: str, src: str, source_url: str = "", title: str = "",
                      thread_id: str = "") -> dict:
    """把联网搜索到的图片下载到 _web_materials/，返回 {path, url, source_url, title, filename}。

    受控语义（M1.3）：
    - src 必须是**本会话搜索结果登记过的 URL**（灵感搜索返回的 full_url 已由
      inspiration 登记进候选列表），data URI / local-view（本地可信来源）豁免。
    - 域名策略：默认仅 https，可配白名单放行 http 域名。
    - SSRF 防护：_from_src → validate_media_url（拒绝私网/metadata/localhost）。
    - 大小限制 20MB、魔数校验、原子写、扩展名矫正。
    - provenance 落盘：source_url / 搜索词 / 搜索源 / 下载时间 / 大小 / 检测格式。
    """
    if not output_dir:
        raise ComfyError("未配置输出图片路径", 400)
    d = web_materials_dir(output_dir)
    d.mkdir(parents=True, exist_ok=True)

    # 候选校验：外部 http(s) URL 必须命中搜索结果候选列表（或本会话快照内灵感卡
    # 图片，重启后候选列表丢失仍可保存旧卡）；本地可信来源豁免
    from app.services.url_guard import is_local_view_url
    from app.services import web_material_candidates
    if not src.startswith("data:") and not is_local_view_url(src):
        if not web_material_candidates.is_candidate(src) and not _is_thread_inspiration_image(thread_id, src):
            raise ComfyError("图片不在搜索结果候选列表中，禁止直接下载（请通过灵感搜索获取图片后保存）", 400)
        _check_domain_allowlist(src)

    data, ext = _from_src(src)

    # 魔数校验：字节流必须是合法图片格式
    from app.services.image_magic import validate_image_bytes
    try:
        detected = validate_image_bytes(data)
        # 扩展名矫正：若声明扩展名与魔数不一致，以魔数为准
        ext = detected
    except ValueError as e:
        raise ComfyError(f"图片格式校验失败：{e}", 400)

    # 原子写：先写临时文件，再 rename（防并发/中断导致残缺文件）
    temp_path = d / (_next_seq_name(d, ext) + ".tmp")
    try:
        temp_path.write_bytes(data)
        temp_path.chmod(0o644)
        final_name = temp_path.stem  # 去掉 .tmp 后缀
        dest = d / final_name
        # 若同名文件已存在（极小概率），追加随机后缀
        if dest.exists():
            dest = d / _next_seq_name(d, ext)
        temp_path.rename(dest)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise

    # provenance 落盘：来源追溯链（source_url/搜索词/搜索源/时间/大小/格式）
    candidate = web_material_candidates.candidate_meta(src) if not src.startswith("data:") and not is_local_view_url(src) else {}
    provenance = _load_provenance(d)
    provenance[dest.name] = {
        "source_url": source_url or candidate.get("source_url") or "",
        "query": candidate.get("query") or "",
        "provider": candidate.get("provider") or "",
        "title": title or dest.name,   # 原始标题（素材库/多页文件夹显示用；旧记录无此键回退文件名）
        "downloaded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "size": len(data),
        "detected_ext": ext,
        # P2-1：图站素材的原站标签/作者（danbooru/pixiv 搜索时写入候选），供资产化与溯源
        "tags": candidate.get("tags") or [],
        "author": candidate.get("author") or "",
        "rating": candidate.get("rating") or "",
    }
    _save_provenance(d, provenance)

    from app.services import rag_store as _rag_store, view_urls

    url = view_urls.local_view(str(dest))
    # P2-1 完整刀：落盘即资产记录（素材进资产库/标签统计/语义索引/视觉索引）。
    # 入库失败不阻断保存——文件已落盘即报成功；资产库缺席该条只留 warning
    # （存量补录走 scripts/index_web_materials.py）。
    _rag_store.index_web_material_asset(url, provenance[dest.name])
    return {
        "path": str(dest),
        "url": url,
        "source_url": provenance[dest.name]["source_url"],
        "title": title or dest.name,
        "filename": dest.name,
        "query": candidate.get("query") or "",
        "downloaded_at": provenance[dest.name]["downloaded_at"],
    }


def save_web_series(
    output_dir: str, illust_id: int, source_url: str = "", title: str = "",
    refresh_token: str = "", proxy: str = "", thread_id: str = "",
) -> dict:
    """pixiv 多页作品：**后端一次性拉全页 + 逐页落盘**。

    为什么放后端：前端是「for 循环逐页调 save」的客户端流程，**浏览器中途崩溃/断连就断了**
    （2026-10-17 用户实锤：5 页只落 1 页就崩了）。改成后端一次请求内完成——即便前端断连，
    FastAPI 线程池里的本函数也会继续写完，下次刷新即可看到全部页。
    返回 {ok, saved, failed, page_count}；每页内部重试 3 次抗瞬时网络抖动。
    """
    from app.services import material_sources

    if not output_dir:
        raise ComfyError("未配置输出图片路径", 400)
    r = material_sources.pixiv_pages(int(illust_id or 0), refresh_token=refresh_token, proxy=proxy)
    if not r.get("ok"):
        raise ComfyError(r.get("error") or "拉取作品分页失败", 502)
    urls: list[str] = r.get("originals") or r.get("pages") or []
    if not urls:
        raise ComfyError("该作品没有可保存的页", 404)

    saved = 0
    failed = 0
    for i, u in enumerate(urls):
        page_title = f"{title} · P{i + 1}/{len(urls)}" if len(urls) > 1 else (title or "")
        ok = False
        for _ in range(3):  # 服务端重试抗抖动
            try:
                save_web_material(output_dir, u, source_url, page_title, thread_id)
                ok = True
                break
            except Exception:  # noqa: BLE001 单页失败继续其余页
                pass
        if ok:
            saved += 1
        else:
            failed += 1
    return {"ok": failed == 0, "saved": saved, "failed": failed, "page_count": len(urls)}



def list_web_materials(output_dir: str) -> list[dict]:
    """列出 _web_materials/ 下所有图片（带 provenance 来源信息）。"""
    if not output_dir:
        return []
    d = web_materials_dir(output_dir)
    if not d.exists():
        return []
    from app.services import view_urls
    # 2026-10-18：素材也进了资产主表（P1）——列表顺带投影 assetId（RAG 文档 id，
    # 反推/描述落库要用）与已生成的视觉描述，避免逐张再查一次。
    from app.services import asset_store, rag_store
    provenance = _load_provenance(d)
    desc_by_asset: dict[str, str] = {}
    try:
        for asset in asset_store.list_assets([rag_store.WEB_MATERIALS_REPO_ID]):
            desc_by_asset[asset["id"]] = asset.get("description") or ""
    except Exception:  # noqa: BLE001 [EXC-保留] 主表不可用不影响素材列表
        desc_by_asset = {}
    items = []
    for f in sorted(d.iterdir(), key=lambda x: x.name, reverse=True):
        if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            meta = provenance.get(f.name, {})
            url = view_urls.local_view(str(f))
            asset_id = rag_store.generation_doc_id(rag_store.WEB_MATERIALS_REPO_ID, url)
            items.append({
                "path": str(f),
                "url": url,
                "assetId": asset_id,
                "description": desc_by_asset.get(asset_id, ""),
                "source_url": meta.get("source_url") or "",
                "title": meta.get("title") or f.name,   # 优先用原始标题；旧记录无此键回退文件名
                "filename": f.name,
                "query": meta.get("query") or "",
                "provider": meta.get("provider") or "",
                "downloaded_at": meta.get("downloaded_at") or "",
                # 2026-10-16 扩充：原站标签/作者/评级/大小（「左图右信息」详情面板用；
                # 搜索落盘时已写入 provenance，数据本就就绪）
                "tags": meta.get("tags") or [],
                "author": meta.get("author") or "",
                "rating": meta.get("rating") or "",
                "size": meta.get("size") or 0,
            })
    return items


def delete_web_material(output_dir: str, filename: str) -> bool:
    """删除 _web_materials/ 下的指定文件（同步清理 provenance 记录）。"""
    if not output_dir:
        return False
    d = web_materials_dir(output_dir)
    safe_name = Path(filename).name
    target = d / safe_name
    if target.exists() and target.is_file():
        target.unlink()
        # 清理 provenance 条目，保留其余
        provenance = _load_provenance(d)
        if provenance.pop(safe_name, None) is not None:
            _save_provenance(d, provenance)
        # P2-1 完整刀：同步删资产记录（文件已删，只删库条目；失败不阻断删文件）
        from app.services import rag_store as _rag_store, view_urls

        _rag_store.delete_web_material_asset(view_urls.local_view(str(target)))
        return True
    return False


# ===== 参考图：聊天上传到 <repo>/reference/ 的图片，供画布自动导入 =====


def list_reference_images(output_dir: str, repo_id: str) -> list[dict]:
    """列出 <repo_id>/reference/ 下所有图片文件。"""
    if not output_dir or not repo_id:
        return []
    from app.services import repo_meta
    base = repo_meta.repo_folder(output_dir, repo_id)
    d = base / "reference"
    if not d.exists():
        return []
    from app.services import view_urls
    items = []
    for f in sorted(d.iterdir(), key=lambda x: x.name, reverse=True):
        if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"):
            items.append({
                "path": str(f),
                "url": view_urls.local_view(str(f)),
                "title": f.name,
                "filename": f.name,
            })
    return items
