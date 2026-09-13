"""外网图片代理（M1.2）：浏览器缩略图直连外网图床会被墙/防盗链拦截，走后端中转。

安全合同（对照 local_media 的 Adapter+Response 分层，路由薄、服务深）：
- 只允许 http(s) URL（防 file://、data: 等协议注入）。
- SSRF 防护：调用 url_guard.validate_media_url 阻止私网/metadata 地址，并逐跳检查重定向目标。
- 流式读取并限制响应大小（默认 5MB），防被当开放代理滥用/内存撑爆。
- Content-Type 必须是 image/*（防拉回 HTML/JS）。
- 代理地址与灵感搜索同源：显式 proxy（trust_env=False），空则直连。
"""
from __future__ import annotations

import httpx

from app.services.url_guard import validate_media_url

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB，缩略图/原图预览足够


class ImageProxyError(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def fetch_remote_image(url: str, proxy: str = "",
                       max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes, str]:
    """拉取远程图片，返回 (bytes, content_type)。失败抛 ImageProxyError。

    proxy 为访问外网的代理地址（与灵感搜索一致）；空则直连。
    """
    url = (url or "").strip()
    # SSRF 防护：首跳校验协议/私网/metadata/DNS rebinding
    try:
        url = validate_media_url(url)
    except ValueError as e:
        raise ImageProxyError(400, str(e))

    try:
        client_kw: dict = {"timeout": 20, "follow_redirects": False, "trust_env": False}
        if proxy and proxy.strip():
            client_kw["proxy"] = proxy.strip()
        with httpx.Client(**client_kw) as c:
            current = url
            ctype = ""
            # 重定向逐跳校验：校验通过才发下一跳请求（TOCTOU 修复——旧实现
            # follow_redirects=True 先请求后审计 history，对内网的请求已经发出）。
            for _ in range(5):
                with c.stream("GET", current) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        location = (r.headers.get("location") or "").strip()
                        if not location:
                            raise ImageProxyError(502, "重定向响应缺少 Location")
                        try:
                            current = validate_media_url(
                                str(httpx.URL(current).join(location)),
                            )
                        except ValueError as exc:
                            raise ImageProxyError(400, f"重定向目标被拒：{exc}") from exc
                        continue
                    r.raise_for_status()
                    try:
                        validate_media_url(str(r.url))
                    except ValueError as exc:
                        raise ImageProxyError(400, f"最终响应地址指向内网：{r.url}") from exc
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    if not ctype.startswith("image/"):
                        raise ImageProxyError(400, f"非图片内容类型：{ctype or 'unknown'}")
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in r.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise ImageProxyError(
                                413, f"图片过大（超过 {max_bytes // (1024 * 1024)}MB）",
                            )
                        chunks.append(chunk)
                    return b"".join(chunks), ctype
            raise ImageProxyError(502, "重定向跳数过多（>5）")
    except ImageProxyError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ImageProxyError(502, f"图片拉取失败：{exc}") from exc
