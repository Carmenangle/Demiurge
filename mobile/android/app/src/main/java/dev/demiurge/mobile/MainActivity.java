package dev.demiurge.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.util.Log;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

/**
 * 最小 WebView 壳：加载电脑端后端送达的同一份 SPA。
 * 业务逻辑零复制——页面、接口、存档都在电脑上，本 App 只是可配置地址的窗口。
 */
public final class MainActivity extends Activity {
    private static final String TAG = "DemiurgeShell";
    private static final String PREFS = "demiurge_shell";
    private static final String KEY_URL = "server_url";
    private static final String DEFAULT_URL = "http://127.0.0.1:8010/";
    private static final int REQ_FILE = 4101;
    private static final int REQ_AUDIO = 4102;
    private static final int MATCH = ViewGroup.LayoutParams.MATCH_PARENT;
    private static final int WRAP = ViewGroup.LayoutParams.WRAP_CONTENT;

    private FrameLayout root;
    private WebView web;
    private ValueCallback<Uri[]> fileCallback;
    private PermissionRequest audioRequest;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        WebView.setWebContentsDebuggingEnabled(true);
        root = new FrameLayout(this);
        setContentView(root);

        Intent intent = getIntent();
        boolean forceSettings = intent != null && intent.getBooleanExtra("open_settings", false);
        if (intent != null) intent.removeExtra("open_settings");

        String saved = prefs().getString(KEY_URL, "");
        if (saved.isEmpty() || forceSettings) {
            showConnect(saved.isEmpty() ? DEFAULT_URL : saved);
        } else {
            loadShell(saved);
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (intent != null && intent.getBooleanExtra("open_settings", false)) {
            intent.removeExtra("open_settings");
            showConnect(prefs().getString(KEY_URL, DEFAULT_URL));
        }
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }

    private int dp(int value) {
        return Math.round(getResources().getDisplayMetrics().density * value);
    }

    private void showConnect(String prefill) {
        destroyWeb();
        root.removeAllViews();

        ScrollView scroll = new ScrollView(this);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(24);
        box.setPadding(pad, dp(40), pad, pad);
        scroll.addView(box, new ViewGroup.LayoutParams(MATCH, WRAP));

        TextView title = new TextView(this);
        title.setText(R.string.connect_title);
        title.setTextSize(20);
        box.addView(title);

        TextView hint = new TextView(this);
        hint.setText(R.string.connect_hint);
        hint.setTextSize(13);
        hint.setPadding(0, dp(12), 0, dp(12));
        box.addView(hint);

        final EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(prefill);
        box.addView(input);

        Button go = new Button(this);
        go.setText(R.string.connect_button);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(WRAP, WRAP);
        lp.topMargin = dp(16);
        box.addView(go, lp);
        go.setOnClickListener(v -> {
            String url = normalize(input.getText().toString());
            if (url == null) {
                Toast.makeText(this, R.string.connect_invalid, Toast.LENGTH_LONG).show();
                return;
            }
            prefs().edit().putString(KEY_URL, url).apply();
            loadShell(url);
        });

        root.addView(scroll);
    }

    /** 补 scheme、补尾斜杠；主机为空即判非法。 */
    private String normalize(String raw) {
        String s = raw == null ? "" : raw.trim();
        if (s.isEmpty()) return null;
        if (!s.matches("(?i)^https?://.*")) s = "http://" + s;
        Uri uri = Uri.parse(s);
        String host = uri.getHost();
        if (host == null || host.isEmpty()) return null;
        if (!s.endsWith("/")) s += "/";
        return s;
    }

    private void loadShell(String url) {
        root.removeAllViews();
        if (web == null) {
            web = new WebView(this);
            configure(web);
            root.addView(web, new FrameLayout.LayoutParams(MATCH, MATCH));
        }
        Log.i(TAG, "loading " + url);
        web.loadUrl(url);
    }

    private void configure(WebView target) {
        WebSettings s = target.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setAllowFileAccess(false);
        // 兼容 <input type=file>：文件选择走 content:// ，不能关 ContentAccess。
        s.setUserAgentString(s.getUserAgentString() + " DemiurgeMobile/0.1.0");
        // target=_blank / window.open 需要多窗口支持，见 onCreateWindow 转发。
        s.setSupportMultipleWindows(true);
        s.setJavaScriptCanOpenWindowsAutomatically(true);

        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(target, false);

        target.setBackgroundColor(0xFF101014);
        target.setWebViewClient(new ShellClient());
        target.setWebChromeClient(new ShellChrome());
    }

    private String originOf(String url) {
        try {
            Uri uri = Uri.parse(url);
            String scheme = uri.getScheme() == null ? "http" : uri.getScheme();
            String host = uri.getHost() == null ? "" : uri.getHost();
            int port = uri.getPort();
            if (port == -1) port = "https".equalsIgnoreCase(scheme) ? 443 : 80;
            return scheme.toLowerCase() + "://" + host.toLowerCase() + ":" + port;
        } catch (Exception e) {
            return "";
        }
    }

    /** 同源留在 WebView（含 blob:/data:），其余 http(s) 与自定义 scheme 交给系统。 */
    private boolean handleUrl(Uri uri) {
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase();
        if ("blob".equals(scheme) || "data".equals(scheme) || "about".equals(scheme)) return false;
        if (!"http".equals(scheme) && !"https".equals(scheme)) {
            openExternally(uri);
            return true;
        }
        if (!originOf(uri.toString()).isEmpty()
                && originOf(uri.toString()).equals(originOf(web == null ? "" : web.getUrl()))) {
            return false;
        }
        openExternally(uri);
        return true;
    }

    private void openExternally(Uri uri) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (Exception e) {
            Log.w(TAG, "no handler for " + uri + ": " + e);
        }
    }

    private final class ShellClient extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            return handleUrl(request.getUrl());
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (!request.isForMainFrame()) return;
            Log.w(TAG, "main frame error " + error.getErrorCode() + " " + error.getDescription());
            new AlertDialog.Builder(MainActivity.this)
                    .setMessage(R.string.connect_failed)
                    .setPositiveButton(R.string.retry, (d, w) -> view.reload())
                    .setNeutralButton(R.string.change_server,
                            (d, w) -> showConnect(prefs().getString(KEY_URL, DEFAULT_URL)))
                    .setNegativeButton(android.R.string.cancel, null)
                    .show();
        }
    }

    private final class ShellChrome extends WebChromeClient {
        @Override
        public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                         FileChooserParams params) {
            if (fileCallback != null) fileCallback.onReceiveValue(null);
            fileCallback = callback;
            try {
                startActivityForResult(params.createIntent(), REQ_FILE);
                return true;
            } catch (Exception e) {
                fileCallback = null;
                callback.onReceiveValue(null);
                Log.w(TAG, "file chooser failed: " + e);
                return false;
            }
        }

        @Override
        public void onPermissionRequest(final PermissionRequest request) {
            boolean wantsAudio = false;
            for (String resource : request.getResources()) {
                if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource)) wantsAudio = true;
            }
            if (!wantsAudio) {
                request.deny();
                return;
            }
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                request.grant(new String[] {PermissionRequest.RESOURCE_AUDIO_CAPTURE});
            } else {
                audioRequest = request;
                requestPermissions(new String[] {Manifest.permission.RECORD_AUDIO}, REQ_AUDIO);
            }
        }

        /** window.open/target=_blank：把新窗口的首个 URL 接回主 WebView。 */
        @Override
        public boolean onCreateWindow(WebView view, boolean isDialog, boolean isUserGesture,
                                      android.os.Message resultMsg) {
            final WebView temp = new WebView(MainActivity.this);
            temp.setWebViewClient(new WebViewClient() {
                @Override
                public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest request) {
                    Uri uri = request.getUrl();
                    temp.destroy();
                    if (web != null && handleUrl(uri)) return true;
                    if (web != null) web.loadUrl(uri.toString());
                    return true;
                }
            });
            WebView.WebViewTransport transport = (WebView.WebViewTransport) resultMsg.obj;
            transport.setWebView(temp);
            resultMsg.sendToTarget();
            return true;
        }

        @Override
        public boolean onConsoleMessage(android.webkit.ConsoleMessage message) {
            Log.d(TAG, "console: " + message.message() + " @ " + message.sourceId() + ":" + message.lineNumber());
            return true;
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQ_FILE) {
            if (fileCallback != null) {
                fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data));
                fileCallback = null;
            }
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        if (requestCode == REQ_AUDIO && audioRequest != null) {
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
                audioRequest.grant(new String[] {PermissionRequest.RESOURCE_AUDIO_CAPTURE});
            } else {
                audioRequest.deny();
            }
            audioRequest = null;
            return;
        }
        super.onRequestPermissionsResult(requestCode, permissions, results);
    }

    @Override
    protected void onPause() {
        if (web != null) web.onPause();
        super.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) web.onResume();
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        destroyWeb();
        super.onDestroy();
    }

    private void destroyWeb() {
        if (web == null) return;
        try {
            web.stopLoading();
            root.removeView(web);
            web.destroy();
        } catch (Exception e) {
            Log.w(TAG, "web destroy: " + e);
        }
        web = null;
    }
}
