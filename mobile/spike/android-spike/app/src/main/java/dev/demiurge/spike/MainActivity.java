package dev.demiurge.spike;

import android.app.Activity;
import android.content.SharedPreferences;
import android.content.res.AssetManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import android.util.Log;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.SecureRandom;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

public class MainActivity extends Activity {
    private static final String TAG = "DemiurgeSpike";
    private static final String FRONTEND_VERSION = "0.3.5";
    private static final int LOG_HEIGHT_DP = 260;

    // M4 安全：令牌只存 app 私有 SharedPreferences（allowBackup=false 已在 Manifest 关闭备份），
    // 每次请求走 X-Demiurge-Token；WebView 首跳经 /__auth 换 HttpOnly + SameSite=Strict cookie，
    // 供 <img>/<video>/SSE 这类无法自定义头的同源请求自动携带。引导落地即 clearHistory + flush：
    // 令牌只在首次启动进过一次 URL，之后靠持久化 cookie 直连根页。
    private static final String HEADER_NAME = "X-Demiurge-Token";
    private static final String BOOTSTRAP_PATH = "/__auth";
    // 与服务端 local_auth.COOKIE_NAME 同值；HttpOnly，JS 读不到，壳层只用于判断「是否已引导过」
    private static final String COOKIE_NAME = "demiurge_token";
    private static final String PREFS_NAME = "demiurge_auth";
    private static final String PREF_TOKEN = "local_api_token";

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final long t0 = System.currentTimeMillis();
    private TextView logView;
    private ScrollView logScroller;
    private WebView webView;
    private Button toggle;
    private boolean logOpen = true;
    private String token = "";
    private int port = 8010;
    private String baseUrl = "";
    private boolean bootstrapLoaded = false;
    private boolean authRetryDone = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);

        toggle = new Button(this);
        toggle.setText("收起日志");
        toggle.setOnClickListener(v -> setLogOpen(!logOpen));
        root.addView(toggle, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        webView = new WebView(this);
        configureWebView();
        root.addView(webView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        logView = new TextView(this);
        logView.setTextSize(11);
        logView.setTextColor(Color.parseColor("#DDDDDD"));
        logView.setBackgroundColor(Color.parseColor("#111111"));
        logView.setPadding(24, 24, 24, 24);
        logView.setTextIsSelectable(true);
        logScroller = new ScrollView(this);
        logScroller.addView(logView, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(logScroller, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(LOG_HEIGHT_DP)));

        setContentView(root);
        stamp("app started, python not yet");
        new Thread(this::boot, "boot").start();
    }

    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        // 页面全部来自 APK 内置前端（后端同源下发），不需要任何本地文件/内容访问能力
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setSupportMultipleWindows(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(webView, false);
        // 只允许停留在本机后端：任何外部 scheme（intent://、market://…）与外部主机一律拦掉
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                boolean allowed = "http".equals(url.getScheme()) && "127.0.0.1".equals(url.getHost());
                if (!allowed) {
                    Log.w(TAG, "blocked navigation: " + url.getScheme() + "://" + url.getHost());
                }
                return !allowed;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                // 引导 URL 的 query 里带令牌：落地到站内后立即抹掉会话历史，并把 cookie 落盘，
                // 下次启动就能直连（令牌只在首次引导进过一次 URL）。
                if (bootstrapLoaded && url != null && !url.contains(BOOTSTRAP_PATH)) {
                    bootstrapLoaded = false;
                    view.clearHistory();
                    CookieManager.getInstance().flush();
                    Log.i(TAG, "bootstrap landed: history cleared, cookie flushed");
                }
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request,
                                            WebResourceResponse errorResponse) {
                // 直连用 cookie；cookie 失效时只回落引导一次，避免 401 白屏或反复重试
                if (request.isForMainFrame() && errorResponse.getStatusCode() == 401
                        && !authRetryDone) {
                    authRetryDone = true;
                    Log.w(TAG, "main frame 401, falling back to bootstrap");
                    bootstrapLoaded = true;
                    view.loadUrl(bootstrapUrl());
                }
            }
        });
    }

    private String bootstrapUrl() {
        return baseUrl + BOOTSTRAP_PATH + "?token=" + Uri.encode(token) + "&next=/";
    }

    private boolean hasAuthCookie() {
        String cookies = CookieManager.getInstance().getCookie(baseUrl + "/");
        return cookies != null && cookies.contains(COOKIE_NAME + "=" + token);
    }

    private String loadOrCreateToken() {
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        String existing = prefs.getString(PREF_TOKEN, "");
        if (existing != null && !existing.isEmpty()) {
            return existing;
        }
        byte[] raw = new byte[32];
        new SecureRandom().nextBytes(raw);
        String created = Base64.encodeToString(raw,
                Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
        prefs.edit().putString(PREF_TOKEN, created).apply();
        return created;
    }

    private void boot() {
        token = loadOrCreateToken();
        stamp("local api token ready (" + token.length() + " chars)");

        File frontend = null;
        try {
            frontend = ensureFrontend();
        } catch (Throwable t) {
            append("FRONTEND EXTRACT ERROR:\n" + Log.getStackTraceString(t));
        }

        try {
            stamp("Python.start() begin");
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(getApplicationContext()));
            }
            stamp("python started: " + Python.getInstance()
                    .getModule("sys").get("version").toString().split(" ")[0]);
            stamp("mobile_server.start() begin, frontend=" + frontend);
            long tStart = System.currentTimeMillis();
            Python py = Python.getInstance();
            Object result = py.getModule("mobile_server")
                    .callAttr("start", getFilesDir().getAbsolutePath(),
                            frontend == null ? "" : frontend.getAbsolutePath(), token);
            port = py.getModule("mobile_server").callAttr("get_port").toInt();
            stamp("mobile_server.start() returned in "
                    + (System.currentTimeMillis() - tStart) + " ms: " + result);
        } catch (Throwable t) {
            append("PYTHON ERROR:\n" + Log.getStackTraceString(t));
            return;
        }

        String base = "http://127.0.0.1:" + port;
        for (int i = 0; i < 360; i++) {
            try {
                Thread.sleep(500);
                String body = httpGet(base + "/api/health");
                if (body.contains("\"status\":\"ok\"") || body.contains("\"status\": \"ok\"")) {
                    stamp("HEALTH OK (" + (i + 1) * 500 + " ms after python start): " + body);
                    try {
                        stamp("INFO: " + httpGet(base + "/api/mobile/info"));
                        stamp("SELFTEST: " + httpGet(base + "/api/mobile/selftest", 120000));
                    } catch (Throwable t) {
                        stamp("probe endpoints failed: " + t);
                    }
                    baseUrl = base;
                    ui.post(() -> {
                        // CookieManager 与 WebView 同处 UI 线程，别在 boot 线程里碰
                        boolean cookieOk = hasAuthCookie();
                        stamp("webview auth cookie "
                                + (cookieOk ? "present, direct load" : "absent, bootstrap"));
                        if (cookieOk) {
                            webView.loadUrl(baseUrl + "/");
                        } else {
                            bootstrapLoaded = true;
                            webView.loadUrl(bootstrapUrl());
                        }
                        setLogOpen(false);
                    });
                    return;
                }
                if (i % 10 == 0) {
                    stamp("waiting health (" + i + "): " + body);
                }
            } catch (Throwable t) {
                if (i % 10 == 0) {
                    stamp("waiting server (" + i + "): " + t);
                }
            }
        }
        append("server did not answer within 180s");
    }

    private File ensureFrontend() throws Exception {
        File dir = new File(getFilesDir(), "frontend");
        File stamp = new File(dir, ".version");
        if (stamp.isFile() && FRONTEND_VERSION.equals(readText(stamp))) {
            stamp("frontend present: " + dir);
            return dir;
        }
        stamp("extracting frontend.zip -> " + dir);
        long t = System.currentTimeMillis();
        if (dir.exists()) {
            deleteTree(dir);
        }
        dir.mkdirs();
        AssetManager am = getAssets();
        int files = 0;
        long bytes = 0;
        try (ZipInputStream zin = new ZipInputStream(am.open("frontend.zip"))) {
            byte[] buf = new byte[65536];
            ZipEntry entry;
            while ((entry = zin.getNextEntry()) != null) {
                File out = new File(dir, entry.getName());
                if (entry.isDirectory()) {
                    out.mkdirs();
                    continue;
                }
                File parent = out.getParentFile();
                if (parent != null) {
                    parent.mkdirs();
                }
                try (FileOutputStream fos = new FileOutputStream(out)) {
                    int n;
                    while ((n = zin.read(buf)) > 0) {
                        fos.write(buf, 0, n);
                        bytes += n;
                    }
                }
                files++;
                if (files % 100 == 0) {
                    stamp("  " + files + " files, " + (bytes >> 20) + " MB");
                }
            }
        }
        try (FileOutputStream fos = new FileOutputStream(stamp)) {
            fos.write(FRONTEND_VERSION.getBytes("UTF-8"));
        }
        stamp("frontend extracted: " + files + " files, " + (bytes >> 20) + " MB in "
                + (System.currentTimeMillis() - t) + " ms");
        return dir;
    }

    private static void deleteTree(File f) {
        File[] kids = f.listFiles();
        if (kids != null) {
            for (File kid : kids) {
                deleteTree(kid);
            }
        }
        f.delete();
    }

    private static String readText(File f) throws Exception {
        try (InputStream in = new java.io.FileInputStream(f)) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[1024];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
            }
            return out.toString("UTF-8").trim();
        }
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void setLogOpen(boolean open) {
        logOpen = open;
        ViewGroup.LayoutParams lp = logScroller.getLayoutParams();
        lp.height = open ? dp(LOG_HEIGHT_DP) : 0;
        logScroller.setLayoutParams(lp);
        toggle.setText(open ? "收起日志" : "展开日志");
    }

    private String httpGet(String spec) throws Exception {
        return httpGet(spec, 2000);
    }

    private String httpGet(String spec, int timeoutMs) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(spec).openConnection();
        conn.setConnectTimeout(2000);
        conn.setReadTimeout(timeoutMs);
        // 壳层健康检查/自检走本机 API，必须与 WebView cookie 同一身份闸
        conn.setRequestProperty(HEADER_NAME, token);
        try {
            int code = conn.getResponseCode();
            InputStream in = code < 400 ? conn.getInputStream() : conn.getErrorStream();
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while (in != null && (n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
            }
            return code + " " + out.toString("UTF-8");
        } finally {
            conn.disconnect();
        }
    }

    private void append(String line) {
        Log.i(TAG, line);
        ui.post(() -> logView.append(line + "\n"));
    }

    private void stamp(String line) {
        append("[+" + (System.currentTimeMillis() - t0) + " ms] " + line);
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.destroy();
        }
        super.onDestroy();
    }
}
