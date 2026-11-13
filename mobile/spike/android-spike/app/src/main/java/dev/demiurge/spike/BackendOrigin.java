package dev.demiurge.spike;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/**
 * 后端 origin 判据：scheme + host + 有效端口全等。
 *
 * Cookie 按主机而不按端口隔离，并且下载通道会把 `X-Demiurge-Token` 附在 URL 上
 * ⇒ 只校验 host 时，本机任意监听端口都能收到凭证。这里用 {@link URI}（不依赖
 * android.net.Uri）以便在 JVM 侧直接回归；任何解析失败一律拒绝（fail-closed）。
 */
public final class BackendOrigin {

    private BackendOrigin() {
    }

    public static boolean matches(String baseUrl, String candidate) {
        URI base = parseAbsolute(baseUrl);
        URI target = parseAbsolute(candidate);
        if (base == null || target == null) {
            return false;
        }
        if (base.getRawUserInfo() != null || target.getRawUserInfo() != null) {
            return false;
        }
        String baseScheme = scheme(base);
        if (!isHttp(baseScheme) || !baseScheme.equals(scheme(target))) {
            return false;
        }
        String baseHost = normalizeHost(base);
        if (baseHost.isEmpty() || !baseHost.equals(normalizeHost(target))) {
            return false;
        }
        int basePort = effectivePort(base);
        return basePort > 0 && basePort == effectivePort(target);
    }

    private static URI parseAbsolute(String raw) {
        if (raw == null) {
            return null;
        }
        try {
            URI uri = new URI(raw);
            if (!uri.isAbsolute() || uri.getRawAuthority() == null) {
                return null;
            }
            return uri;
        } catch (URISyntaxException exc) {
            return null;
        }
    }

    private static boolean isHttp(String scheme) {
        return "http".equals(scheme) || "https".equals(scheme);
    }

    private static String scheme(URI uri) {
        String value = uri.getScheme();
        return value == null ? "" : value.toLowerCase(Locale.ROOT);
    }

    private static String normalizeHost(URI uri) {
        String host = uri.getHost();
        if (host == null) {
            return "";
        }
        host = host.toLowerCase(Locale.ROOT);
        // java.net.URI 对 IPv6 保留方括号；带不带括号必须是同一个 origin
        if (host.startsWith("[") && host.endsWith("]")) {
            host = host.substring(1, host.length() - 1);
        }
        return host;
    }

    private static int effectivePort(URI uri) {
        if (uri.getPort() != -1) {
            return uri.getPort();
        }
        String scheme = scheme(uri);
        if ("http".equals(scheme)) {
            return 80;
        }
        if ("https".equals(scheme)) {
            return 443;
        }
        return -1;
    }
}
