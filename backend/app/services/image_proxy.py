"""外网图片代理（M1.2）：浏览器缩略图直连外网图床会被墙/防盗链拦截，走后端中转。

安全合同（对照 local_media 的 Adapter+Response 分层，路由薄、服务深）：
- 只允许 http(s) URL（防 file://、data: 等协议注入）。
- SSRF 防护：调用 url_guard.validate_media_url 阻止私网/metadata 地址，并逐跳检查重定向目标。
- TOCTOU 关窗（2026-10-24）：每跳校验+建连包在 pinned_media_url 里——连接只用校验解析到
  的 IP，rebinding 在校验与建连之间换 IP 无效（见 url_guard 模块 docstring）。
- 流式读取并限制响应大小（默认 5MB），防被当开放代理滥用/内存撑爆。
- Content-Type 必须是 image/*（防拉回 HTML/JS）。
- 代理地址与灵感搜索同源：显式 proxy（trust_env=False），空则直连。
"""
from __future__ import annotations

import httpx

from app.services.url_guard import pinned_media_url, validate_media_url

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

    try:
        client_kw: dict = {"timeout": 20, "follow_redirects": False, "trust_env": False}
        if proxy and proxy.strip():
            client_kw["proxy"] = proxy.strip()
        with httpx.Client(**client_kw) as c:
            current = url
            ctype = ""
            # 重定向逐跳校验：校验通过才发下一跳请求（TOCTOU 修复——旧实现
            # follow_redirects=True 先请求后审计 history，对内网的请求已经发出）。
            # 每跳「校验+钉扎→建连」一次完成：连接只用校验解析到的 IP。
            for hop in range(5):
                try:
                    with pinned_media_url(current) as pinned:
                        with c.stream("GET", pinned) as r:
                            if r.status_code in (301, 302, 303, 307, 308):
                                location = (r.headers.get("location") or "").strip()
                                if not location:
                                    raise ImageProxyError(502, "重定向响应缺少 Location")
                                # 下一跳：退出钉扎后由下一轮 pinned_media_url 校验+钉扎
                                current = str(httpx.URL(pinned).join(location))
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
                except ImageProxyError:
                    raise  # 主动抛出的代理错误原样出（ImageProxyError 是 ValueError 子类，须先于 ValueError 分支）
                except ValueError as exc:
                    # 首跳＝原始 URL 校验失败；后续跳＝重定向目标被拒（保持既有 400 形状）
                    if hop == 0:
                        raise ImageProxyError(400, str(exc)) from exc
                    raise ImageProxyError(400, f"重定向目标被拒：{exc}") from exc
            raise ImageProxyError(502, "重定向跳数过多（>5）")
    except ImageProxyError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ImageProxyError(502, f"图片拉取失败：{exc}") from exc
