package dev.demiurge.spike;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

import org.junit.Test;

/**
 * 导航/下载 origin 判据的 JVM 回归（纯 java.net，不依赖 Android，不需要真机）。
 *
 * Cookie 按主机隔离而不按端口 ⇒ 只校验 host 时，同机任意监听端口都能收到
 * `X-Demiurge-Token`。这里钉住「scheme + host + 有效端口」三件套，并锁住
 * MainActivity 的两个调用点不得退回 host-only 写法。
 */
public class BackendOriginTest {

    private static final String BASE = "http://127.0.0.1:18024";

    @Test
    public void 同源完整地址与任意子路径放行() {
        assertTrue(BackendOrigin.matches(BASE, BASE + "/"));
        assertTrue(BackendOrigin.matches(BASE, BASE + "/api/transfer/export?repo_id=x#frag"));
        assertTrue(BackendOrigin.matches(BASE + "/", BASE + "/assets/index.js"));
    }

    @Test
    public void 不同端口拒绝() {
        assertFalse(BackendOrigin.matches(BASE, "http://127.0.0.1:18025/export"));
        assertFalse(BackendOrigin.matches(BASE, "http://127.0.0.1:8010/"));
    }

    @Test
    public void 不同主机拒绝() {
        assertFalse(BackendOrigin.matches(BASE, "http://localhost:18024/"));
        assertFalse(BackendOrigin.matches(BASE, "http://127.0.0.1.evil.com:18024/"));
        assertFalse(BackendOrigin.matches(BASE, "http://127.0.0.2:18024/"));
    }

    @Test
    public void 不同协议拒绝() {
        assertFalse(BackendOrigin.matches(BASE, "https://127.0.0.1:18024/"));
        assertFalse(BackendOrigin.matches(BASE, "ftp://127.0.0.1:18024/"));
    }

    @Test
    public void 用户信息与畸形地址拒绝() {
        assertFalse(BackendOrigin.matches(BASE, "http://127.0.0.1:18024@evil.com/"));
        assertFalse(BackendOrigin.matches("http://user@" + "127.0.0.1:18024", BASE + "/"));
        assertFalse(BackendOrigin.matches(BASE, ""));
        assertFalse(BackendOrigin.matches(BASE, null));
        assertFalse(BackendOrigin.matches(null, BASE));
        assertFalse(BackendOrigin.matches(BASE, "http://[::1"));
        assertFalse(BackendOrigin.matches(BASE, "blob:http://127.0.0.1:18024/x"));
        assertFalse(BackendOrigin.matches(BASE, "127.0.0.1:18024/export"));
    }

    @Test
    public void IPv6方括号归一() {
        String v6 = "http://[::1]:18024";
        assertTrue(BackendOrigin.matches(v6, v6 + "/export"));
        assertFalse(BackendOrigin.matches(v6, "http://[::1]:18025/export"));
        assertFalse(BackendOrigin.matches(v6, BASE + "/"));
    }

    @Test
    public void 默认端口按协议归一() {
        assertTrue(BackendOrigin.matches("http://127.0.0.1", "http://127.0.0.1:80/x"));
        assertTrue(BackendOrigin.matches("http://127.0.0.1:80", "http://127.0.0.1/x"));
        assertTrue(BackendOrigin.matches("https://127.0.0.1", "https://127.0.0.1:443/x"));
        assertFalse(BackendOrigin.matches("http://127.0.0.1", "http://127.0.0.1:443/x"));
    }

    @Test
    public void 协议与主机大小写归一() {
        assertTrue(BackendOrigin.matches(BASE, "HTTP://127.0.0.1:18024/export"));
        assertTrue(BackendOrigin.matches("HTTP://127.0.0.1:18024", BASE + "/"));
    }

    @Test
    public void MainActivity两个入口都必须走完整origin判据() throws Exception {
        String source = readMainActivity();

        assertTrue("下载入口必须调 BackendOrigin.matches(baseUrl, url)",
                source.contains("BackendOrigin.matches(baseUrl, url)"));
        assertTrue("导航入口必须调 BackendOrigin.matches(baseUrl, request.getUrl().toString())",
                source.contains("BackendOrigin.matches(baseUrl, request.getUrl().toString())"));
        assertFalse("下载入口不得退回 host-only 判定",
                source.contains("\"127.0.0.1\".equals(uri.getHost())"));
        assertFalse("导航入口不得退回 host-only 判定",
                source.contains("\"127.0.0.1\".equals(url.getHost())"));

        int guard = source.indexOf("BackendOrigin.matches(baseUrl, url)");
        int header = source.indexOf("addRequestHeader(HEADER_NAME, token)");
        assertTrue("令牌头只能在 origin 判据之后添加", guard >= 0 && header > guard);
    }

    @Test
    public void MainActivity必须拦截所有非后端origin的子请求() throws Exception {
        String source = readMainActivity();

        String intercept = "shouldInterceptRequest(WebView view, WebResourceRequest request)";
        assertTrue("必须实现 shouldInterceptRequest 兜住子框架/子资源请求（导航回调不管 iframe）",
                source.contains(intercept));
        assertEquals("导航与子请求拦截必须走同一个完整 origin 判据",
                2, countOccurrences(
                        source, "BackendOrigin.matches(baseUrl, request.getUrl().toString())"));

        int method = source.indexOf(intercept);
        int guard = source.indexOf(
                "BackendOrigin.matches(baseUrl, request.getUrl().toString())", method);
        assertTrue("子请求拦截器内部必须调用完整 origin 判据", guard > method);
    }

    private static int countOccurrences(String haystack, String needle) {
        int count = 0;
        int from = 0;
        while (true) {
            int at = haystack.indexOf(needle, from);
            if (at < 0) {
                return count;
            }
            count++;
            from = at + needle.length();
        }
    }

    private static String readMainActivity() throws Exception {
        Path[] candidates = {
                Paths.get("src/main/java/dev/demiurge/spike/MainActivity.java"),
                Paths.get("mobile/spike/android-spike/app/src/main/java/dev/demiurge/spike/MainActivity.java"),
        };
        for (Path candidate : candidates) {
            if (Files.isRegularFile(candidate)) {
                return new String(Files.readAllBytes(candidate), StandardCharsets.UTF_8);
            }
        }
        fail("找不到 MainActivity.java（cwd=" + Paths.get("").toAbsolutePath() + "）");
        return "";
    }
}
