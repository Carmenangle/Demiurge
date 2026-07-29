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
