import pytest

from app.services.url_guard import (
    is_local_view_url,
    is_media_file,
    validate_comfyui_url,
    validate_media_url,
)


class TestComfyuiUrl:
    def test_localhost_allowed(self):
        assert validate_comfyui_url("http://127.0.0.1:8188") == "http://127.0.0.1:8188"

    def test_localhost_name_allowed(self):
        assert validate_comfyui_url("http://localhost:8188/") == "http://localhost:8188"

    def test_ipv6_loopback_allowed(self):
        assert validate_comfyui_url("http://[::1]:8188")

    def test_wrong_scheme_rejected(self):
        with pytest.raises(ValueError, match="协议"):
            validate_comfyui_url("https://127.0.0.1:8188")

    def test_file_scheme_rejected(self):
        with pytest.raises(ValueError):
            validate_comfyui_url("file:///etc/passwd")

    def test_credentials_rejected(self):
        with pytest.raises(ValueError, match="凭据"):
            validate_comfyui_url("http://user:pass@127.0.0.1:8188")

    def test_public_ip_rejected(self):
        with pytest.raises(ValueError, match="不在允许范围"):
            validate_comfyui_url("http://8.8.8.8:8188")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            validate_comfyui_url("")


class TestMediaUrl:
    def test_https_public_url_allowed(self):
        url = "https://example.com/image.png"
        result = validate_media_url(url)
        assert result == url

    def test_data_uri_passthrough(self):
        data = "data:image/png;base64,abc"
        assert validate_media_url(data) == data

    def test_localhost_rejected(self):
        with pytest.raises(ValueError, match="私网"):
            validate_media_url("http://127.0.0.1/secret")

    def test_private_ip_rejected(self):
        with pytest.raises(ValueError, match="私网"):
            validate_media_url("http://192.168.1.1/image.jpg")

    def test_10_net_rejected(self):
        with pytest.raises(ValueError, match="私网"):
            validate_media_url("http://10.0.0.1/image.jpg")

    def test_file_scheme_rejected(self):
        with pytest.raises(ValueError, match="协议"):
            validate_media_url("file:///etc/passwd")

    def test_credentials_rejected(self):
        with pytest.raises(ValueError, match="凭据"):
            validate_media_url("https://user:pass@example.com/img.png")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            validate_media_url("")


class TestMediaFile:
    def test_image_ext_allowed(self):
        assert is_media_file(r"D:\out\repo\a.png")
        assert is_media_file("x.JPEG")
        assert is_media_file("clip.mp4")

    def test_audio_ext_allowed(self):
        assert is_media_file("voice.wav")
        assert is_media_file("music.mp3")
        assert is_media_file("tts.flac")
        assert is_media_file("narr.ogg")

    def test_non_media_rejected(self):
        assert not is_media_file(r"C:\Users\me\.env")
        assert not is_media_file("app.db")
        assert not is_media_file("secret.py")
        assert not is_media_file("noext")


class TestLocalViewUrl:
    def test_loopback_local_view_true(self):
        assert is_local_view_url("http://127.0.0.1:8010/api/comfyui/local-view?path=x.png")
        assert is_local_view_url("http://localhost:8010/comfyui/local-view?path=x.png")

    def test_foreign_host_local_view_false(self):
        # 关键：外部主机带 local-view 路径不能豁免 SSRF
        assert not is_local_view_url("http://evil.com/comfyui/local-view?path=x")

    def test_non_local_view_path_false(self):
        assert not is_local_view_url("http://127.0.0.1:8010/comfyui/other")

    def test_root_relative_local_view_true(self):
        # wire 形态＝根相对（属主 view_urls）：本机取图豁免必须继续认得这个形态，
        # 否则相对地址会被当成外部 URL 送进 SSRF 校验而判死。
        assert is_local_view_url("/api/comfyui/local-view?path=x.png")
        assert is_local_view_url("  /api/comfyui/local-view?path=x.png  ")
        assert not is_local_view_url("/api/comfyui/other?path=x")

    def test_protocol_relative_not_exempted(self):
        # `//host/...` 的主机由调用方给出，不是自家根相对 ⇒ 不得豁免
        assert not is_local_view_url("//evil.com/api/comfyui/local-view?path=x")

    def test_foreign_host_with_our_endpoint_shape_still_false(self):
        # 相对化不放宽 SSRF：外部主机即使带一模一样的端点路径也判假
        # （与 view_urls._WIRE_TEXT 只收敛回环是同一条锚点）
        assert not is_local_view_url("http://evil.com/api/comfyui/local-view?path=x")


# ---------------------------------------------------------------------------
# 2026-10-24 出站建连 IP 钉扎（TOCTOU 关窗）机制测试。
# 真实失败路径：validate 校验时解析一次 DNS，httpx 建连时再解析一次，两次解析之间
# rebinding 可把公网域名换到私网 IP。修复＝校验与建连共用同一批 IP（pinned_*）。
# ---------------------------------------------------------------------------


class TestPinnedMediaUrl:
    def test_钉扎期间只回已验证IP_退出即恢复(self, monkeypatch):
        import socket

        from app.services import url_guard

        calls = {"n": 0}

        def fake(host, port, *args, **kwargs):
            calls["n"] += 1
            # 第 1 次＝校验解析（公网 93.184.216.34），之后毒化为另一公网 IP 6.6.6.6
            ip = "93.184.216.34" if calls["n"] == 1 else "6.6.6.6"
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port or 0))]

        monkeypatch.setattr(url_guard, "_orig_getaddrinfo", fake)

        with url_guard.pinned_media_url("http://public.test/x.png") as pinned:
            assert pinned == "http://public.test/x.png"
            resolved = socket.getaddrinfo("public.test", 80)
            assert [r[4][0] for r in resolved] == ["93.184.216.34"]
        # 退出钉扎后恢复系统解析（毒化生效——这正是要关的窗口，钉扎期内它无效）
        resolved = socket.getaddrinfo("public.test", 80)
        assert [r[4][0] for r in resolved] == ["6.6.6.6"]

    def test_毒化IP不得经钉扎通道漏出(self, monkeypatch):
        import socket

        from app.services import url_guard

        calls = {"n": 0}

        def fake(host, port, *args, **kwargs):
            calls["n"] += 1
            ip = "93.184.216.34" if calls["n"] == 1 else "192.0.2.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port or 0))]

        monkeypatch.setattr(url_guard, "_orig_getaddrinfo", fake)

        with url_guard.pinned_media_url("http://public.test/x.png"):
            resolved = socket.getaddrinfo("public.test", 8080)
            assert [r[4][0] for r in resolved] == ["93.184.216.34"]
            assert all(r[4][1] == 8080 for r in resolved)  # 端口如实带给建连方

    def test_嵌套钉扎内层退出恢复外层(self, monkeypatch):
        import socket

        from app.services import url_guard

        def fake(host, port, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                     ("93.184.216.34", port or 0))]

        monkeypatch.setattr(url_guard, "_orig_getaddrinfo", fake)

        with url_guard.pinned_media_url("http://a.test/x"):
            with url_guard.pinned_host("b.test"):
                assert socket.getaddrinfo("a.test", 80)[0][4][0] == "93.184.216.34"
                assert socket.getaddrinfo("b.test", 80)[0][4][0] == "93.184.216.34"
            # 内层退出后外层钉扎必须仍在（栈式恢复，不能被内层 unpin 带走）
            assert socket.getaddrinfo("a.test", 80)[0][4][0] == "93.184.216.34"

    def test_地址族过滤_钉扎IP不满足请求族时空结果拒连(self, monkeypatch):
        import socket

        from app.services import url_guard

        def fake(host, port, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                     ("93.184.216.34", port or 0))]

        monkeypatch.setattr(url_guard, "_orig_getaddrinfo", fake)

        with url_guard.pinned_media_url("http://v4.test/x"):
            # 请求 IPv6 族时只能回钉扎集合里匹配族的结果；绝不允许回落系统解析重开窗口
            assert socket.getaddrinfo("v4.test", 80, family=socket.AF_INET6) == []

    def test_私网解析拒绝且不留钉(self, monkeypatch):
        import socket

        from app.services import url_guard

        def fake(host, port, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                     ("10.0.0.5", port or 0))]

        monkeypatch.setattr(url_guard, "_orig_getaddrinfo", fake)

        with pytest.raises(ValueError, match="私网"):
            with url_guard.pinned_media_url("http://private.test/x.png"):
                pass
        # 拒绝后不得残留钉扎（残留会让同线程后续请求连去被拒地址）
        assert getattr(url_guard._pin_local, "hosts", None) in (None, {})

    def test_解析失败不钉扎也不拒绝(self, monkeypatch):
        from app.services import url_guard

        def fake(host, port, *args, **kwargs):
            raise OSError("NXDOMAIN")

        monkeypatch.setattr(url_guard, "_orig_getaddrinfo", fake)

        # 保守语义：解析失败视为可通过（不因网络故障拒绝合法请求），且不钉扎
        with url_guard.pinned_host("unresolvable.test"):
            assert getattr(url_guard._pin_local, "hosts", None) in (None, {})


class TestPinnedChainE2E:
    """四条出站链的 TOCTOU 端到端：毒化 DNS 后连接必须落在已验证 IP。"""

    def test_image_proxy_毒化DNS仍连已验证IP(self, fake_dns, loopback_servers, allow_loopback_as_public):
        import httpx

        from app.services.image_proxy import fetch_remote_image

        fake_dns["evil.test"] = ["127.0.0.1", "127.0.0.2"]
        port = loopback_servers["127.0.0.1"]
        url = f"http://evil.test:{port}/a.png"

        data, ctype = fetch_remote_image(url)
        assert (data, ctype) == (b"legit-bytes", "image/png")

        # 夹具自证（放在主断言后，避免消费 DNS 序列）：不加钉的同源请求确实被
        # 毒化 DNS 带到 127.0.0.2 的 poison 服务——本测试测的窗口真实存在
        with httpx.Client(timeout=5, trust_env=False) as probe:
            assert probe.get(url).content == b"poison-bytes"

    def test_image_store保存链_毒化DNS仍连已验证IP(self, fake_dns, loopback_servers, allow_loopback_as_public):
        from app.services.image_store import _download_external_url

        fake_dns["evil.test"] = ["127.0.0.1", "127.0.0.2"]
        port = loopback_servers["127.0.0.1"]
        # evil.test 不在图站 CDN 白名单 ⇒ 走 httpx 直下分支（灵感卡保存的公网链）
        assert _download_external_url(f"http://evil.test:{port}/a.png") == b"legit-bytes"

    def test_visual_ci取图_毒化DNS仍连已验证IP(self, fake_dns, loopback_servers, allow_loopback_as_public):
        import base64

        from app.services.visual_ci import _to_data_uri

        fake_dns["evil.test"] = ["127.0.0.1", "127.0.0.2"]
        port = loopback_servers["127.0.0.1"]
        uri = _to_data_uri(f"http://evil.test:{port}/a.png")
        assert uri.startswith("data:image/png;base64,")
        assert base64.b64decode(uri.split(",", 1)[1]) == b"legit-bytes"

    def test_material_sources_httpx兜底_毒化DNS仍连已验证IP(self, fake_dns, loopback_servers, allow_loopback_as_public):
        from app.services import material_sources

        fake_dns["cdn.donmai.us"] = ["127.0.0.1", "127.0.0.2"]
        port = loopback_servers["127.0.0.1"]
        data, ctype = material_sources._fetch_via_httpx(
            f"http://cdn.donmai.us:{port}/a.png", proxy="")
        assert data == b"legit-bytes"

    def test_model_downloader_毒化DNS仍落已验证IP(self, fake_dns, loopback_servers, allow_loopback_as_public, tmp_path):
        from app.services import model_downloader

        fake_dns["huggingface.co"] = ["127.0.0.1", "127.0.0.2"]
        port = loopback_servers["127.0.0.1"]
        model_downloader._download(
            "t1", f"http://huggingface.co:{port}/repo/file.safetensors?token=x",
            str(tmp_path), "checkpoint", "", "",
        )
        dest = tmp_path / "checkpoints" / "file.safetensors"
        assert dest.read_bytes() == b"legit-bytes"
