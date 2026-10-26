package dev.demiurge.spike;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.res.AssetManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import android.util.Log;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
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
    // M4 运行时：健康监控 + 前台服务 + 退后台暂停（PLAN §7）
    private static final long PAUSE_DELAY_MS = 30_000;
    private static final int HEALTH_INTERVAL_MS = 5_000;
    private static final int HEALTH_FAILS_TO_RESTART = 3;
    private Thread supervisor;
    private volatile boolean backendPaused = false;
    // 进程级前台可见性：BACK 退出会换一个 Activity 实例，旧实例 onPause 里 post 的
    // 30s 暂停定时器不会被新实例 onResume 的 removeCallbacks 清掉（handler 不是同一个）
    // ⇒ 回前台 30s 后仍会误暂停。暂停前必须再问一次「进程现在可见吗」。
    private static volatile boolean appVisible = false;
    private volatile boolean restartInFlight = false;
    private int fgsRunning = -1;   // -1=未起服；>0=通知里的任务数
    private final Runnable pauseRunnable = this::maybePauseBackend;
    // M5 文件桥：<input type=file> 走系统选件器；导出下载走 DownloadManager
    private static final int FILE_CHOOSER_REQUEST = 7001;
    private ValueCallback<Uri[]> filePathCallback;

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
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(
                "android.permission.POST_NOTIFICATIONS")
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 1001);
        }
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
        // M5：导出下载走 DownloadManager（fetch blob 它接不住，故后端导出故意做成
        // GET 附件 URL）。DownloadManager 带不上 HttpOnly cookie ⇒ 补令牌头；
        // 只放行本机后端源，外部 URL 不经此通道。
        webView.setDownloadListener((url, userAgent, contentDisposition,
                                     mimetype, contentLength) -> {
            Uri uri = Uri.parse(url);
            if (!"127.0.0.1".equals(uri.getHost())) {
                Log.w(TAG, "blocked download from non-local host: " + uri.getHost());
                return;
            }
            try {
                DownloadManager.Request req = new DownloadManager.Request(uri);
                req.setMimeType(mimetype);
                req.addRequestHeader(HEADER_NAME, token);
                String name = URLUtil.guessFileName(url, contentDisposition, mimetype);
                req.setDestinationInExternalPublicDir(
                        Environment.DIRECTORY_DOWNLOADS, name);
                req.setNotificationVisibility(
                        DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(req);
                stamp("download started: " + name);
            } catch (Throwable t) {
                append("download failed: " + Log.getStackTraceString(t));
            }
        });
        // M5：<input type=file> 的系统选件桥（导入作品包上传）
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(null);
                }
                filePathCallback = callback;
                try {
                    Intent intent = params.createIntent();
                    intent.addCategory(Intent.CATEGORY_OPENABLE);
                    startActivityForResult(intent, FILE_CHOOSER_REQUEST);
                    return true;
                } catch (Throwable t) {
                    filePathCallback = null;
                    Log.w(TAG, "file chooser unavailable: " + t);
                    return false;
                }
            }
        });
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
                    startSupervisor();
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
        // 缓存键＝构建期从 assets/frontend.zip 实算的 sha256（BuildConfig.FRONTEND_SHA）：
        // 换 zip 必然换键，不存在「忘了改版本常量 ⇒ 升级继续跑旧前端」的漏改面。
        if (stamp.isFile() && BuildConfig.FRONTEND_SHA.equals(readText(stamp))) {
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
            fos.write(BuildConfig.FRONTEND_SHA.getBytes("UTF-8"));
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

    @Override
    protected void onResume() {
        super.onResume();
        appVisible = true;
        ui.removeCallbacks(pauseRunnable);
        if (backendPaused) {
            backendPaused = false;
            new Thread(() -> {
                try {
                    Object r = Python.getInstance().getModule("mobile_server")
                            .callAttr("resume");
                    stamp("backend resumed: " + r);
                } catch (Throwable t) {
                    append("backend resume failed: " + Log.getStackTraceString(t));
                }
                startSupervisor();
            }, "resume").start();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        appVisible = false;
        // 退后台 30s 后若无在途生成则暂停 uvicorn 线程（解释器保活，回前台秒级恢复）
        ui.postDelayed(pauseRunnable, PAUSE_DELAY_MS);
    }

    private void maybePauseBackend() {
        new Thread(() -> {
            if (appVisible) {
                stamp("pause skipped (activity visible)");
                return;
            }
            try {
                Object r = Python.getInstance().getModule("mobile_server")
                        .callAttr("pause_if_idle");
                String text = String.valueOf(r);
                if (text.contains("True") || text.contains("'paused': True")) {
                    if (appVisible) {
                        // 决定暂停与回前台只差几毫秒：立刻拉回，不把用户留在死后端上
                        Python.getInstance().getModule("mobile_server").callAttr("resume");
                        stamp("pause reverted (foreground race)");
                        return;
                    }
                    backendPaused = true;
                    stamp("backend paused (idle in background)");
                } else {
                    stamp("backend kept alive: " + text);
                }
            } catch (Throwable t) {
                append("pause_if_idle failed: " + Log.getStackTraceString(t));
            }
        }, "pause-if-idle").start();
    }

    /** 健康监控：5s 一拍；3 连败只重启 uvicorn 线程；running_jobs 驱动前台服务起停。 */
    private void startSupervisor() {
        if (supervisor != null && supervisor.isAlive()) {
            return;
        }
        supervisor = new Thread(() -> {
            int fails = 0;
            while (!backendPaused) {
                try {
                    String body = httpGet(baseUrl + "/api/health", 3000);
                    if (body.contains("\"status\":\"ok\"") || body.contains("\"status\": \"ok\"")) {
                        fails = 0;
                        syncForegroundService(parseRunningJobs(body));
                    } else {
                        fails++;
                    }
                } catch (Throwable t) {
                    fails++;
                }
                if (fails >= HEALTH_FAILS_TO_RESTART && !restartInFlight) {
                    restartInFlight = true;
                    new Thread(() -> {
                        try {
                            Object r = Python.getInstance().getModule("mobile_server")
                                    .callAttr("restart_uvicorn");
                            append("[supervisor] uvicorn restarted: " + r);
                        } catch (Throwable t) {
                            append("[supervisor] restart failed: "
                                    + Log.getStackTraceString(t));
                        } finally {
                            restartInFlight = false;
                        }
                    }, "uvicorn-restart").start();
                    fails = 0;
                    sleepQuiet(8000);  // 等重拉的端口就绪，别把重启后的慢启动误判成连败
                }
                sleepQuiet(HEALTH_INTERVAL_MS);
            }
            // 暂停路径退出前收掉前台服务（能暂停必然 running=0，服务本就不该在）
            stopForegroundService();
        }, "health-supervisor");
        supervisor.start();
        stamp("supervisor started (5s health, 3-fail restart, fgs on running)");
    }

    private static void sleepQuiet(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static int parseRunningJobs(String body) {
        int idx = body.indexOf("\"running_jobs\":");
        if (idx < 0) {
            return 0;
        }
        int i = idx + "\"running_jobs\":".length();
        int end = i;
        while (end < body.length() && Character.isDigit(body.charAt(end))) {
            end++;
        }
        try {
            return Integer.parseInt(body.substring(i, end));
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    private void syncForegroundService(int running) {
        if (running > 0) {
            if (fgsRunning != running) {
                Intent intent = new Intent(this, GenerationForegroundService.class);
                intent.putExtra("running", running);
                if (Build.VERSION.SDK_INT >= 26) {
                    startForegroundService(intent);
                } else {
                    startService(intent);
                }
                if (fgsRunning <= 0) {
                    stamp("foreground service started (running=" + running + ")");
                }
                fgsRunning = running;
            }
        } else if (fgsRunning > 0) {
            stopForegroundService();
            stamp("foreground service stopped (running=0)");
        }
    }

    private void stopForegroundService() {
        if (fgsRunning > 0) {
            stopService(new Intent(this, GenerationForegroundService.class));
            fgsRunning = -1;
        }
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
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == FILE_CHOOSER_REQUEST) {
            if (filePathCallback != null) {
                filePathCallback.onReceiveValue(
                        WebChromeClient.FileChooserParams.parseResult(resultCode, data));
                filePathCallback = null;
            }
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.destroy();
        }
        super.onDestroy();
    }
}
