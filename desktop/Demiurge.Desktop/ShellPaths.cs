using System.IO;
using System.Text.Json;

namespace Demiurge.Desktop;

internal sealed record PackageLayout(
    string PackageRoot,
    string RuntimeRoot,
    string StatePath,
    string RuntimeExe,
    string DataDir);

internal sealed record RepoLayout(string RepoRoot)
{
    public string BackendDir => Path.Combine(RepoRoot, "backend");
    public string PythonExe => Path.Combine(BackendDir, ".venv", "Scripts", "python.exe");
    public string FrontendDist => Path.Combine(RepoRoot, "frontend", "dist");
}

internal static class ShellPaths
{
    public static string LocalAppData { get; } = ResolveHome();

    public static string LogsDir => Path.Combine(LocalAppData, "logs");
    public static string ShellLogPath => Path.Combine(LogsDir, "desktop.log");
    public static string RuntimeLogPath => Path.Combine(LogsDir, "runtime.log");
    public static string SettingsPath => Path.Combine(LocalAppData, "desktop.json");
    public static string WebViewDataDir => Path.Combine(LocalAppData, "webview2");

    public const string SingleInstanceMutexName = @"Local\Demiurge.Desktop.SingleInstance";
    public const string ActivateEventName = @"Local\Demiurge.Desktop.Activate";

    /** 便携包根目录：向上找到含 data/runtime/current.json 的目录。 */
    public static PackageLayout? ResolvePackage(string startDirectory)
    {
        var root = FindAncestor(startDirectory, dir =>
            File.Exists(Path.Combine(dir, "data", "runtime", "current.json")));
        if (root is null)
        {
            return null;
        }
        var runtimeRoot = Path.Combine(root, "data", "runtime");
        var statePath = Path.Combine(runtimeRoot, "current.json");
        var baseId = ReadBaseId(statePath);
        if (string.IsNullOrWhiteSpace(baseId))
        {
            return null;
        }
        return new PackageLayout(
            root,
            runtimeRoot,
            statePath,
            Path.Combine(runtimeRoot, "base", baseId, "Demiurge-Runtime.exe"),
            Path.Combine(root, "data", "userdata"));
    }

    public static RepoLayout? FindRepoLayout(string startDirectory)
    {
        var root = FindAncestor(startDirectory, dir =>
            File.Exists(Path.Combine(dir, "backend", "app", "main.py")) &&
            File.Exists(Path.Combine(dir, "frontend", "package.json")));
        return root is null ? null : new RepoLayout(root);
    }

    private static string? FindAncestor(string startDirectory, Func<string, bool> predicate)
    {
        var current = new DirectoryInfo(Path.GetFullPath(startDirectory));
        while (current is not null)
        {
            if (predicate(current.FullName))
            {
                return current.FullName;
            }
            current = current.Parent;
        }
        return null;
    }

    private static string ResolveHome()
    {
        var configured = Environment.GetEnvironmentVariable("DEMIURGE_SHELL_HOME");
        if (!string.IsNullOrWhiteSpace(configured))
        {
            return Path.GetFullPath(configured.Trim());
        }
        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Demiurge");
    }

    private static string? ReadBaseId(string statePath)
    {
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(statePath));
            if (document.RootElement.ValueKind == JsonValueKind.Object &&
                document.RootElement.TryGetProperty("base_id", out var baseId) &&
                baseId.ValueKind == JsonValueKind.String)
            {
                return baseId.GetString();
            }
        }
        catch (Exception ex)
        {
            ShellLog.Warn($"读取运行时状态失败：{statePath}", ex);
        }
        return null;
    }
}
