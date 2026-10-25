package dev.demiurge.spike;

import android.app.Activity;
import android.content.res.AssetManager;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.ViewGroup;
import android.webkit.WebView;
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
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

public class MainActivity extends Activity {
    private static final String TAG = "DemiurgeSpike";
    private static final String FRONTEND_VERSION = "0.3.2";
    private static final int LOG_HEIGHT_DP = 260;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final long t0 = System.currentTimeMillis();
    private TextView logView;
    private ScrollView logScroller;
    private WebView webView;
    private Button toggle;
    private boolean logOpen = true;

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
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
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

    private void boot() {
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
            if (frontend != null) {
                Python.getInstance().getModule("spike_server")
                        .callAttr("set_frontend_dir", frontend.getAbsolutePath());
                stamp("frontend dir passed to python");
            }
            Python.getInstance().getModule("spike_server").callAttr("start");
            stamp("spike_server.start() returned");
        } catch (Throwable t) {
            append("PYTHON ERROR:\n" + Log.getStackTraceString(t));
            return;
        }

        for (int i = 0; i < 40; i++) {
            try {
                Thread.sleep(500);
                String body = httpGet("http://127.0.0.1:8010/api/health");
                int cut = body.indexOf("validate_20k_ms");
                int end = cut > 0 ? Math.min(body.length(), cut + 40) : body.length();
                stamp("HEALTH OK: " + body.substring(0, end));
                ui.post(() -> {
                    webView.loadUrl("http://127.0.0.1:8010/");
                    setLogOpen(false);
                });
                return;
            } catch (Throwable t) {
                if (i % 5 == 0) {
                    stamp("waiting server (" + i + "): " + t);
                }
            }
        }
        append("server did not answer within 20s");
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
        HttpURLConnection conn = (HttpURLConnection) new URL(spec).openConnection();
        conn.setConnectTimeout(2000);
        conn.setReadTimeout(2000);
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
