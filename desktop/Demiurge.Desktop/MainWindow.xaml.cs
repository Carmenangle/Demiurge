using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Media.Imaging;
using Microsoft.Web.WebView2.Core;
using Application = System.Windows.Application;
using Forms = System.Windows.Forms;

namespace Demiurge.Desktop;

public partial class MainWindow : Window
{
    private readonly RuntimeHost _runtime;
    private readonly ShellSettings _settings;
    private readonly CancellationTokenSource _lifetime = new();
    private Forms.NotifyIcon? _tray;
    private System.Drawing.Icon? _trayImage;
    private bool _webViewReady;
    private bool _starting;
    private bool _closing;
    private string? _fallbackUrl;
    private bool _openLogsOnClick = true;

    internal MainWindow(RuntimeHost runtime, ShellSettings settings)
    {
        InitializeComponent();
        _runtime = runtime;
        _settings = settings;
        ApplySettings();
        TrySetWindowIcon();
        CreateTrayIcon();
        Loaded += OnLoaded;
        Closing += OnClosing;
    }

    public void ActivateFromShell()
    {
        if (_closing)
        {
            return;
        }
        if (!IsVisible)
        {
            Show();
        }
        if (WindowState == WindowState.Minimized)
        {
            WindowState = WindowState.Normal;
        }
        Activate();
        Topmost = true;
        Topmost = false;
        Focus();
    }

    private void ApplySettings()
    {
        Width = _settings.Width ?? ShellSettings.DefaultWidth;
        Height = _settings.Height ?? ShellSettings.DefaultHeight;
        if (_settings.Left is double left && _settings.Top is double top)
        {
            WindowStartupLocation = WindowStartupLocation.Manual;
            Left = left;
            Top = top;
        }
        if (_settings.Maximized)
        {
            WindowState = WindowState.Maximized;
        }
    }

    private void TrySetWindowIcon()
    {
        try
        {
            Icon = new BitmapImage(new Uri("pack://application:,,,/Assets/app.ico"));
        }
        catch (Exception ex)
        {
            ShellLog.Warn("加载窗口图标失败", ex);
        }
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        await StartAsync();
    }

    private async Task StartAsync()
    {
        if (_starting)
        {
            return;
        }
        _starting = true;
        try
        {
            await InitializeWebViewAsync();
            await EnsureRuntimeAndNavigateAsync();
        }
        catch (WebView2RuntimeNotFoundException ex)
        {
            ShellLog.Error("缺少 WebView2 Runtime", ex);
            ShowWebViewMissing(
                "未检测到 Microsoft Edge WebView2 Runtime，桌面壳无法显示界面。\n\n" +
                $"点击下方按钮下载安装后重试。\n{SelfTest.DownloadUrl}");
        }
        catch (RuntimeStartupException ex)
        {
            ShellLog.Error("运行时启动失败", ex);
            ShowFailure(ex.Message);
        }
        catch (OperationCanceledException) when (_lifetime.IsCancellationRequested)
        {
            // 关窗中止启动。
        }
        catch (Exception ex)
        {
            ShellLog.Error("启动失败", ex);
            ShowFailure(ex.Message);
        }
        finally
        {
            _starting = false;
        }
    }

    private async Task InitializeWebViewAsync()
    {
        if (_webViewReady)
        {
            return;
        }
        ShowOverlay("正在准备 WebView2…", null, showActions: false);
        var options = new CoreWebView2EnvironmentOptions
        {
            AdditionalBrowserArguments = Environment.GetEnvironmentVariable("DEMIURGE_WEBVIEW2_ARGS") ?? string.Empty,
        };
        var environment = await CoreWebView2Environment.CreateAsync(null, ShellPaths.WebViewDataDir, options);
        await Browser.EnsureCoreWebView2Async(environment);
        ConfigureWebView();
        _webViewReady = true;
    }

    private void ConfigureWebView()
    {
        var core = Browser.CoreWebView2;
#if DEBUG
        core.Settings.AreDevToolsEnabled = true;
#else
        core.Settings.AreDevToolsEnabled = false;
#endif
        core.Settings.IsStatusBarEnabled = false;
        core.NavigationStarting += OnNavigationStarting;
        core.NewWindowRequested += OnNewWindowRequested;
        core.NavigationCompleted += OnNavigationCompleted;
        core.DocumentTitleChanged += (_, _) =>
        {
            var title = core.DocumentTitle;
            Title = string.IsNullOrWhiteSpace(title) ? "Demiurge" : title;
        };
        core.ProcessFailed += OnProcessFailed;
    }

    private async Task EnsureRuntimeAndNavigateAsync()
    {
        await _runtime.EnsureReadyAsync(
            new Progress<string>(message =>
            {
                if (!_closing)
                {
                    ShowOverlay(message, null, showActions: false);
                }
            }),
            _lifetime.Token);
        if (_closing)
        {
            return;
        }
        ShowOverlay("正在加载界面…", null, showActions: false);
        Browser.Source = new Uri(_runtime.BaseUrl);
    }

    private void OnNavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs e)
    {
        if (!Uri.TryCreate(e.Uri, UriKind.Absolute, out var uri))
        {
            return;
        }
        if (uri.Scheme is not ("http" or "https"))
        {
            return;
        }
        if (uri.IsLoopback || uri.Host.Equals("localhost", StringComparison.OrdinalIgnoreCase))
        {
            return;
        }
        e.Cancel = true;
        OpenExternal(e.Uri);
    }

    private void OnNewWindowRequested(object? sender, CoreWebView2NewWindowRequestedEventArgs e)
    {
        e.Handled = true;
        if (Uri.TryCreate(e.Uri, UriKind.Absolute, out var uri) && uri.Scheme is ("http" or "https"))
        {
            OpenExternal(e.Uri);
        }
    }

    private void OnNavigationCompleted(object? sender, CoreWebView2NavigationCompletedEventArgs e)
    {
        if (_closing)
        {
            return;
        }
        if (e.IsSuccess)
        {
            HideOverlay();
            return;
        }
        ShowFailure($"页面加载失败：{e.WebErrorStatus}\n{_runtime.BaseUrl}");
    }

    private void OnProcessFailed(object? sender, CoreWebView2ProcessFailedEventArgs e)
    {
        if (_closing)
        {
            return;
        }
        Dispatcher.BeginInvoke(new Action(() =>
        {
            ShowFailure($"WebView2 进程异常退出（{e.ProcessFailedKind}）。请重启 Demiurge。");
            RetryButton.Visibility = Visibility.Collapsed;
        }));
    }

    private void ShowOverlay(string status, string? detail, bool showActions, bool showRetry = true)
    {
        if (_closing)
        {
            return;
        }
        StatusText.Text = status;
        DetailText.Text = detail ?? string.Empty;
        DetailText.Visibility = string.IsNullOrWhiteSpace(detail) ? Visibility.Collapsed : Visibility.Visible;
        Actions.Visibility = showActions ? Visibility.Visible : Visibility.Collapsed;
        RetryButton.Visibility = showRetry ? Visibility.Visible : Visibility.Collapsed;
        OpenBrowserButton.Content = _openLogsOnClick ? "打开日志目录" : "下载 WebView2";
        Overlay.Visibility = Visibility.Visible;
        Browser.Visibility = Visibility.Collapsed;
    }

    private void HideOverlay()
    {
        Overlay.Visibility = Visibility.Collapsed;
        Browser.Visibility = Visibility.Visible;
        Browser.Focus();
    }

    private void ShowFailure(string message)
    {
        _fallbackUrl = null;
        _openLogsOnClick = true;
        ShowOverlay("启动失败", message, showActions: true);
    }

    private void ShowWebViewMissing(string message)
    {
        _fallbackUrl = SelfTest.DownloadUrl;
        _openLogsOnClick = false;
        ShowOverlay("缺少 WebView2 运行时", message, showActions: true, showRetry: false);
    }

    private async void RetryButton_Click(object sender, RoutedEventArgs e)
    {
        if (_closing)
        {
            return;
        }
        if (!_webViewReady)
        {
            await StartAsync();
            return;
        }
        ShowOverlay("正在重新启动服务…", null, showActions: false);
        _runtime.Stop();
        try
        {
            await EnsureRuntimeAndNavigateAsync();
        }
        catch (RuntimeStartupException ex)
        {
            ShellLog.Error("重试启动失败", ex);
            ShowFailure(ex.Message);
        }
        catch (OperationCanceledException) when (_lifetime.IsCancellationRequested)
        {
            // 关窗中止。
        }
        catch (Exception ex)
        {
            ShellLog.Error("重试失败", ex);
            ShowFailure(ex.Message);
        }
    }

    private void OpenBrowserButton_Click(object sender, RoutedEventArgs e)
    {
        if (_fallbackUrl is not null)
        {
            OpenExternal(_fallbackUrl);
        }
        else if (_openLogsOnClick)
        {
            OpenLogsFolder();
        }
        else
        {
            OpenExternal(_runtime.BaseUrl);
        }
    }

    private void CreateTrayIcon()
    {
        try
        {
            var stream = Application.GetResourceStream(new Uri("pack://application:,,,/Assets/app.ico"))?.Stream;
            if (stream is null)
            {
                return;
            }
            using (stream)
            {
                _trayImage = new System.Drawing.Icon(stream);
            }
            _tray = new Forms.NotifyIcon
            {
                Icon = _trayImage,
                Text = "Demiurge",
                Visible = true,
            };
            var menu = new Forms.ContextMenuStrip();
            menu.Items.Add("打开主界面", null, (_, _) => ActivateFromShell());
            menu.Items.Add("在浏览器中打开", null, (_, _) => OpenExternal(_runtime.BaseUrl));
            menu.Items.Add(new Forms.ToolStripSeparator());
            menu.Items.Add("退出", null, (_, _) => Dispatcher.BeginInvoke(new Action(Close)));
            _tray.ContextMenuStrip = menu;
            _tray.DoubleClick += (_, _) => ActivateFromShell();
        }
        catch (Exception ex)
        {
            ShellLog.Warn("创建托盘图标失败", ex);
        }
    }

    private void OnClosing(object? sender, CancelEventArgs e)
    {
        _closing = true;
        SaveSettings();
        _lifetime.Cancel();
        if (_tray is not null)
        {
            _tray.Visible = false;
            _tray.Dispose();
            _tray = null;
        }
        _trayImage?.Dispose();
        _trayImage = null;
    }

    private void SaveSettings()
    {
        try
        {
            var bounds = WindowState == WindowState.Normal
                ? new Rect(Left, Top, Width, Height)
                : RestoreBounds;
            if (bounds.Width > 0 && bounds.Height > 0)
            {
                _settings.Left = bounds.Left;
                _settings.Top = bounds.Top;
                _settings.Width = bounds.Width;
                _settings.Height = bounds.Height;
            }
            _settings.Maximized = WindowState == WindowState.Maximized;
            _settings.Save();
        }
        catch (Exception ex)
        {
            ShellLog.Warn("保存窗口设置失败", ex);
        }
    }

    private static void OpenLogsFolder()
    {
        try
        {
            Directory.CreateDirectory(ShellPaths.LogsDir);
            OpenExternal(ShellPaths.LogsDir);
        }
        catch (Exception ex)
        {
            ShellLog.Warn("打开日志目录失败", ex);
        }
    }

    private static void OpenExternal(string target)
    {
        try
        {
            Process.Start(new ProcessStartInfo(target) { UseShellExecute = true });
        }
        catch (Exception ex)
        {
            ShellLog.Warn($"打开外部目标失败：{target}", ex);
        }
    }
}
