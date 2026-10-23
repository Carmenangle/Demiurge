using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net.Http;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;

namespace Demiurge.Desktop;

internal enum RuntimeMode
{
    Packaged,
    Development,
}

internal sealed class RuntimeStartupException : Exception
{
    public RuntimeStartupException(string message) : base(message)
    {
    }
}

internal sealed class RuntimeHost : IDisposable
{
    public const int DefaultPort = 8010;
    private const int MaxLogTailLines = 25;

    private readonly int _port;
    private readonly HttpClient _http;
    private readonly object _runtimeLogGate = new();
    private Process? _process;
    private ChildProcessJob? _job;
    private string? _pidPath;
    private bool _disposed;

    public RuntimeHost(int port)
    {
        _port = port;
        _http = new HttpClient(new HttpClientHandler { UseProxy = false })
        {
            Timeout = TimeSpan.FromMilliseconds(1500),
        };
    }

    public int Port => _port;
    public string BaseUrl => $"http://127.0.0.1:{_port}";
    public RuntimeMode? Mode { get; private set; }
    public bool ReusedExternal { get; private set; }
    public bool OwnsProcess => _process is { HasExited: false };

    public static int ResolvePort(ShellSettings settings)
    {
        foreach (var name in new[] { "DEMIURGE_PORT", "LAF_RUNTIME_PORT" })
        {
            var raw = Environment.GetEnvironmentVariable(name);
            if (!string.IsNullOrWhiteSpace(raw))
            {
                return ParsePort(raw, name);
            }
        }
        return settings.Port is >= 1 and <= 65535 ? settings.Port : DefaultPort;
    }

    public static int ParsePort(string raw, string source)
    {
        if (!int.TryParse(raw.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var port) ||
            port is < 1 or > 65535)
        {
            throw new RuntimeStartupException($"{source} 不是合法端口：{raw}（应为 1-65535 的整数）");
        }
        return port;
    }

    public async Task EnsureReadyAsync(IProgress<string>? progress, CancellationToken cancellationToken)
    {
        progress?.Report($"检查 {BaseUrl} …");
        if (await IsHealthyAsync(cancellationToken))
        {
            if (!await ServesFrontendAsync(cancellationToken))
            {
                throw new RuntimeStartupException(
                    $"端口 {_port} 上已有 Demiurge 后端在运行，但它没有提供前端页面（通常是在开发模式下直接启动的后端）。\n\n" +
                    "请关闭该后端后重试，或为它设置 LAF_FRONTEND_DIST 指向已构建的 frontend/dist。\n" +
                    "桌面壳不会擅自改用其他端口。");
            }
            ReusedExternal = true;
            Mode = null;
            ShellLog.Info($"复用已运行的 Demiurge：{BaseUrl}");
            return;
        }

        if (await IsPortOpenAsync(cancellationToken))
        {
            throw new RuntimeStartupException(
                $"端口 {_port} 已被其他程序占用，无法启动 Demiurge。\n\n" +
                "请关闭占用该端口的程序后重试。");
        }

        var launch = BuildLaunch();
        Mode = launch.Mode;
        StartProcess(launch);
        progress?.Report(Mode == RuntimeMode.Packaged ? "正在启动内置运行时…" : "正在启动开发运行时（Python）…");
        await WaitUntilReadyAsync(progress, cancellationToken);
    }

    public void Stop()
    {
        if (_disposed)
        {
            return;
        }
        var process = _process;
        _process = null;
        ReusedExternal = false;
        Mode = null;
        if (process is null)
        {
            _job?.Dispose();
            _job = null;
            return;
        }

        try
        {
            if (!SafeHasExited(process))
            {
                ShellLog.Info($"停止运行时 pid={SafePid(process)}");
                TryTaskkill(SafePid(process), force: false);
                if (!process.WaitForExit(3000))
                {
                    TryTaskkill(SafePid(process), force: true);
                    if (!process.WaitForExit(5000))
                    {
                        process.Kill(entireProcessTree: true);
                        process.WaitForExit(3000);
                    }
                }
            }
        }
        catch (Exception ex)
        {
            ShellLog.Warn("停止运行时失败", ex);
        }
        finally
        {
            // 先杀进程再关 Job 句柄，避免正常退出路径被 Job 抢先终止。
            _job?.Dispose();
            _job = null;
            try
            {
                process.Dispose();
            }
            catch
            {
                // ignore
            }
            DeletePidFile();
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        Stop();
        _disposed = true;
        _http.Dispose();
    }

    private LaunchSpec BuildLaunch()
    {
        var package = ShellPaths.ResolvePackage(AppContext.BaseDirectory)
                      ?? ShellPaths.ResolvePackage(Environment.CurrentDirectory);
        if (package is not null)
        {
            if (!File.Exists(package.RuntimeExe))
            {
                throw new RuntimeStartupException($"运行时文件缺失：{package.RuntimeExe}");
            }
            var psi = new ProcessStartInfo(package.RuntimeExe)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                WorkingDirectory = package.RuntimeRoot,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
            };
            psi.Environment["LAF_RUNTIME_ROOT"] = package.RuntimeRoot;
            psi.Environment["LAF_RUNTIME_STATE"] = package.StatePath;
            psi.Environment["LAF_DATA_DIR"] = package.DataDir;
            psi.Environment["LAF_NO_BROWSER"] = "1";
            psi.Environment["LAF_RUNTIME_PORT"] = _port.ToString(CultureInfo.InvariantCulture);
            return new LaunchSpec(
                RuntimeMode.Packaged,
                psi,
                package.PackageRoot,
                Path.Combine(package.RuntimeRoot, "runtime.pid"));
        }

        var repo = ShellPaths.FindRepoLayout(AppContext.BaseDirectory)
                   ?? ShellPaths.FindRepoLayout(Environment.CurrentDirectory);
        if (repo is null)
        {
            throw new RuntimeStartupException(
                "未找到 Demiurge 运行时。\n\n" +
                "请把桌面壳放在便携包根目录（含 data/runtime/current.json），或在仓库内运行。");
        }
        if (!File.Exists(repo.PythonExe))
        {
            throw new RuntimeStartupException($"未找到 Python 环境：{repo.PythonExe}\n\n请先在 backend 下创建 .venv。");
        }
        var distOverride = Environment.GetEnvironmentVariable("DEMIURGE_FRONTEND_DIST");
        var frontendDist = string.IsNullOrWhiteSpace(distOverride)
            ? repo.FrontendDist
            : Path.GetFullPath(distOverride.Trim());
        if (!File.Exists(Path.Combine(frontendDist, "index.html")))
        {
            throw new RuntimeStartupException(
                $"前端尚未构建：{frontendDist}\n\n请先在 frontend 下运行 npm run build，或使用打包后的便携版。");
        }

        var dev = new ProcessStartInfo(repo.PythonExe)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = repo.BackendDir,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        dev.ArgumentList.Add("-m");
        dev.ArgumentList.Add("uvicorn");
        dev.ArgumentList.Add("app.main:app");
        dev.ArgumentList.Add("--host");
        dev.ArgumentList.Add("127.0.0.1");
        dev.ArgumentList.Add("--port");
        dev.ArgumentList.Add(_port.ToString(CultureInfo.InvariantCulture));
        dev.Environment["PYTHONUTF8"] = "1";
        dev.Environment["PYTHONIOENCODING"] = "utf-8";
        dev.Environment["LAF_NO_BROWSER"] = "1";
        dev.Environment["LAF_FRONTEND_DIST"] = frontendDist;
        // 壳的端口是唯一真源，清掉继承来的层目录，避免误用旧便携包。
        dev.Environment.Remove("LAF_RUNTIME_ROOT");
        dev.Environment.Remove("LAF_RUNTIME_STATE");
        dev.Environment.Remove("LAF_RUNTIME_EDITION");
        return new LaunchSpec(RuntimeMode.Development, dev, repo.RepoRoot, PidPath: null);
    }

    private void StartProcess(LaunchSpec launch)
    {
        Directory.CreateDirectory(ShellPaths.LogsDir);
        var header = $"{Environment.NewLine}===== {DateTime.Now:yyyy-MM-dd HH:mm:ss} 启动 [{launch.Mode}] {launch.StartInfo.FileName} =====";
        File.AppendAllText(ShellPaths.RuntimeLogPath, header + Environment.NewLine, new UTF8Encoding(false));

        var process = new Process { StartInfo = launch.StartInfo, EnableRaisingEvents = true };
        process.OutputDataReceived += (_, args) => AppendRuntimeLog(args.Data);
        process.ErrorDataReceived += (_, args) =>
        {
            if (args.Data is not null)
            {
                AppendRuntimeLog("[stderr] " + args.Data);
            }
        };
        if (!process.Start())
        {
            throw new RuntimeStartupException("无法启动 Demiurge 运行时进程。");
        }
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        _process = process;
        _pidPath = launch.PidPath;
        WritePidFile(process.Id);

        _job = ChildProcessJob.TryCreate();
        if (_job is null || !_job.TryAssign(process))
        {
            ShellLog.Warn("无法把运行时绑定到 Job 对象，退出时改用 taskkill 回收。");
            _job?.Dispose();
            _job = null;
        }
        ShellLog.Info($"已启动运行时 pid={process.Id} mode={launch.Mode} port={_port}");
    }

    private async Task WaitUntilReadyAsync(IProgress<string>? progress, CancellationToken cancellationToken)
    {
        var timeout = ReadyTimeout();
        var deadline = DateTime.UtcNow + timeout;
        var nextNotice = DateTime.UtcNow;
        while (DateTime.UtcNow < deadline)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (_process is null || SafeHasExited(_process))
            {
                throw new RuntimeStartupException(
                    $"运行时启动失败，退出码 {SafeExitCode()}。{RuntimeLogHint()}");
            }
            if (await IsHealthyAsync(cancellationToken))
            {
                ShellLog.Info($"运行时已就绪：{BaseUrl} pid={SafePid(_process)}");
                return;
            }
            if (DateTime.UtcNow >= nextNotice)
            {
                nextNotice = DateTime.UtcNow.AddSeconds(5);
                progress?.Report($"等待服务就绪…（{BaseUrl}）");
            }
            await Task.Delay(300, cancellationToken);
        }
        throw new RuntimeStartupException(
            $"服务在 {timeout.TotalSeconds:0} 秒内未就绪。{RuntimeLogHint()}");
    }

    private async Task<bool> IsHealthyAsync(CancellationToken cancellationToken)
    {
        try
        {
            using var response = await _http.GetAsync($"{BaseUrl}/api/health", cancellationToken);
            if (!response.IsSuccessStatusCode)
            {
                return false;
            }
            var body = await response.Content.ReadAsStringAsync(cancellationToken);
            return IsHealthyPayload(body);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch
        {
            return false;
        }
    }

    public static bool IsHealthyPayload(string? body)
    {
        if (string.IsNullOrWhiteSpace(body))
        {
            return false;
        }
        try
        {
            using var document = JsonDocument.Parse(body);
            return document.RootElement.ValueKind == JsonValueKind.Object &&
                   document.RootElement.TryGetProperty("status", out var status) &&
                   status.ValueKind == JsonValueKind.String &&
                   string.Equals(status.GetString(), "ok", StringComparison.OrdinalIgnoreCase);
        }
        catch (JsonException)
        {
            return false;
        }
    }

    public static bool IsFrontendDocument(string? html) =>
        !string.IsNullOrWhiteSpace(html) &&
        html.Contains("id=\"root\"", StringComparison.OrdinalIgnoreCase);

    private async Task<bool> ServesFrontendAsync(CancellationToken cancellationToken)
    {
        try
        {
            using var response = await _http.GetAsync($"{BaseUrl}/", cancellationToken);
            if (!response.IsSuccessStatusCode)
            {
                return false;
            }
            var html = await response.Content.ReadAsStringAsync(cancellationToken);
            return IsFrontendDocument(html);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch
        {
            return false;
        }
    }

    private async Task<bool> IsPortOpenAsync(CancellationToken cancellationToken)
    {
        try
        {
            using var client = new TcpClient();
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(600);
            await client.ConnectAsync("127.0.0.1", _port, timeout.Token);
            return true;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch
        {
            return false;
        }
    }

    private static TimeSpan ReadyTimeout()
    {
        var raw = Environment.GetEnvironmentVariable("DEMIURGE_READY_TIMEOUT_SECONDS");
        if (int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out var seconds) &&
            seconds is >= 5 and <= 900)
        {
            return TimeSpan.FromSeconds(seconds);
        }
        return TimeSpan.FromSeconds(180);
    }

    private void AppendRuntimeLog(string? line)
    {
        if (string.IsNullOrEmpty(line))
        {
            return;
        }
        lock (_runtimeLogGate)
        {
            try
            {
                File.AppendAllText(ShellPaths.RuntimeLogPath, line + Environment.NewLine, new UTF8Encoding(false));
            }
            catch
            {
                // 日志写不进去不能拖死运行时。
            }
        }
    }

    private static string RuntimeLogHint()
    {
        var tail = ShellLog.Tail(ShellPaths.RuntimeLogPath, MaxLogTailLines);
        return string.IsNullOrWhiteSpace(tail)
            ? $"\n\n日志：{ShellPaths.RuntimeLogPath}"
            : $"\n\n最近日志（{ShellPaths.RuntimeLogPath}）：\n{tail}";
    }

    private void WritePidFile(int pid)
    {
        if (_pidPath is null)
        {
            return;
        }
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(_pidPath)!);
            File.WriteAllText(_pidPath, pid.ToString(CultureInfo.InvariantCulture), Encoding.ASCII);
        }
        catch (Exception ex)
        {
            ShellLog.Warn($"写入 runtime.pid 失败：{_pidPath}", ex);
        }
    }

    private void DeletePidFile()
    {
        var path = _pidPath;
        _pidPath = null;
        if (path is null)
        {
            return;
        }
        try
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }
        catch (Exception ex)
        {
            ShellLog.Warn($"删除 runtime.pid 失败：{path}", ex);
        }
    }

    private static bool TryTaskkill(int pid, bool force)
    {
        try
        {
            var arguments = force ? $"/PID {pid} /T /F" : $"/PID {pid} /T";
            var psi = new ProcessStartInfo("taskkill", arguments)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            using var killer = Process.Start(psi);
            if (killer is null)
            {
                return false;
            }
            killer.WaitForExit(8000);
            return killer.HasExited && killer.ExitCode == 0;
        }
        catch (Exception ex)
        {
            ShellLog.Warn($"taskkill 失败（pid={pid}, force={force}）", ex);
            return false;
        }
    }

    private static bool SafeHasExited(Process process)
    {
        try
        {
            return process.HasExited;
        }
        catch
        {
            return true;
        }
    }

    private static int SafePid(Process process)
    {
        try
        {
            return process.Id;
        }
        catch
        {
            return 0;
        }
    }

    private string SafeExitCode()
    {
        try
        {
            return _process?.ExitCode.ToString(CultureInfo.InvariantCulture) ?? "?";
        }
        catch
        {
            return "?";
        }
    }

    private sealed record LaunchSpec(RuntimeMode Mode, ProcessStartInfo StartInfo, string Root, string? PidPath);
}
