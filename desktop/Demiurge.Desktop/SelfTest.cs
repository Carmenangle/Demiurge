using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace Demiurge.Desktop;

internal static class SelfTest
{
    private const uint AttachParentProcess = 0xFFFFFFFF;
    private const string WebView2DownloadUrl = "https://go.microsoft.com/fwlink/p/?LinkId=2124703";

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AttachConsole(uint dwProcessId);

    public static int Run()
    {
        AttachConsoleIfPossible();
        var report = new StringBuilder();
        var passed = 0;
        var failed = 0;

        void Check(string name, Func<bool> test)
        {
            bool ok;
            string? error = null;
            try
            {
                ok = test();
            }
            catch (Exception ex)
            {
                ok = false;
                error = $"{ex.GetType().Name}: {ex.Message}";
            }
            if (ok)
            {
                passed++;
                report.AppendLine($"PASS {name}");
            }
            else
            {
                failed++;
                report.AppendLine($"FAIL {name}{(error is null ? string.Empty : " — " + error)}");
            }
        }

        Check("health: ok payload", () => RuntimeHost.IsHealthyPayload("{\"status\":\"ok\"}"));
        Check("health: reject other payloads", () =>
            !RuntimeHost.IsHealthyPayload("{\"status\":\"down\"}") &&
            !RuntimeHost.IsHealthyPayload("{}") &&
            !RuntimeHost.IsHealthyPayload("not json"));
        Check("frontend: root marker", () =>
            RuntimeHost.IsFrontendDocument("<html><body><div id=\"root\"></div></body></html>") &&
            !RuntimeHost.IsFrontendDocument("{\"detail\":\"Not Found\"}"));

        Check("port: 65536 rejected", () =>
        {
            try
            {
                RuntimeHost.ParsePort("65536", "DEMIURGE_PORT");
                return false;
            }
            catch (RuntimeStartupException)
            {
                return true;
            }
        });
        Check("port: 8010 accepted", () => RuntimeHost.ParsePort("8010", "DEMIURGE_PORT") == 8010);

        var originalDemiurgePort = Environment.GetEnvironmentVariable("DEMIURGE_PORT");
        var originalRuntimePort = Environment.GetEnvironmentVariable("LAF_RUNTIME_PORT");
        try
        {
            Environment.SetEnvironmentVariable("DEMIURGE_PORT", null);
            Environment.SetEnvironmentVariable("LAF_RUNTIME_PORT", null);
            Check("port: default 8010", () => RuntimeHost.ResolvePort(new ShellSettings { Port = 0 }) == 8010);
            Check("port: settings value", () => RuntimeHost.ResolvePort(new ShellSettings { Port = 8123 }) == 8123);
            Environment.SetEnvironmentVariable("LAF_RUNTIME_PORT", "8124");
            Check("port: LAF_RUNTIME_PORT", () => RuntimeHost.ResolvePort(new ShellSettings { Port = 8123 }) == 8124);
            Environment.SetEnvironmentVariable("DEMIURGE_PORT", "8125");
            Check("port: DEMIURGE_PORT wins", () => RuntimeHost.ResolvePort(new ShellSettings { Port = 8123 }) == 8125);
            Environment.SetEnvironmentVariable("DEMIURGE_PORT", "0");
            Check("port: invalid env rejected", () =>
            {
                try
                {
                    RuntimeHost.ResolvePort(new ShellSettings { Port = 8123 });
                    return false;
                }
                catch (RuntimeStartupException)
                {
                    return true;
                }
            });
        }
        finally
        {
            Environment.SetEnvironmentVariable("DEMIURGE_PORT", originalDemiurgePort);
            Environment.SetEnvironmentVariable("LAF_RUNTIME_PORT", originalRuntimePort);
        }

        var temp = Path.Combine(ShellPaths.LocalAppData, "self-test-" + Guid.NewGuid().ToString("N"));
        try
        {
            Directory.CreateDirectory(temp);
            var settingsPath = Path.Combine(temp, "desktop.json");
            var settings = new ShellSettings
            {
                Width = 100,
                Height = 100,
                Left = -999999,
                Top = -999999,
                Port = 90000,
            };
            settings.Save(settingsPath);
            var loaded = ShellSettings.Load(settingsPath);
            Check("settings: round-trip clamps bounds", () =>
                loaded.Width >= ShellSettings.MinWidth &&
                loaded.Height >= ShellSettings.MinHeight &&
                loaded.Port == RuntimeHost.DefaultPort &&
                loaded.Left is > -20000);
            File.WriteAllText(settingsPath, "{ broken json");
            var fallback = ShellSettings.Load(settingsPath);
            Check("settings: malformed file falls back", () =>
                fallback.Width == ShellSettings.DefaultWidth &&
                fallback.Height == ShellSettings.DefaultHeight &&
                fallback.Port == RuntimeHost.DefaultPort);

            var packageRoot = Path.Combine(temp, "package");
            var runtimeRoot = Path.Combine(packageRoot, "data", "runtime");
            Directory.CreateDirectory(Path.Combine(runtimeRoot, "base", "base-test"));
            File.WriteAllText(
                Path.Combine(runtimeRoot, "current.json"),
                "{\"schema_version\": 3, \"base_id\": \"base-test\", \"application_id\": \"app-test\"}");
            var layout = ShellPaths.ResolvePackage(Path.Combine(packageRoot, "data"));
            Check("layout: package detected from state file", () =>
                layout is not null &&
                layout.PackageRoot == packageRoot &&
                layout.RuntimeExe == Path.Combine(runtimeRoot, "base", "base-test", "Demiurge-Runtime.exe") &&
                layout.DataDir == Path.Combine(packageRoot, "data", "userdata"));
        }
        catch (Exception ex)
        {
            failed++;
            report.AppendLine($"FAIL self-test workspace — {ex.GetType().Name}: {ex.Message}");
        }
        finally
        {
            try
            {
                Directory.Delete(temp, recursive: true);
            }
            catch
            {
                // ignore
            }
        }

        Check("layout: repo or package present", () =>
            ShellPaths.FindRepoLayout(AppContext.BaseDirectory) is not null ||
            ShellPaths.ResolvePackage(AppContext.BaseDirectory) is not null);
        Check("job: kill tree when handle closes", JobObjectKillTest);

        var summary = $"self-test: {passed} passed, {failed} failed";
        report.AppendLine(summary);
        var text = report.ToString();
        try
        {
            Directory.CreateDirectory(ShellPaths.LogsDir);
            File.WriteAllText(Path.Combine(ShellPaths.LogsDir, "self-test.log"), text, new UTF8Encoding(false));
        }
        catch
        {
            // ignore
        }
        try
        {
            Console.WriteLine(text);
        }
        catch
        {
            // WinExe 可能在无控制台时拿不到 stdout。
        }
        return failed == 0 ? 0 : 1;
    }

    private static bool JobObjectKillTest()
    {
        using var job = ChildProcessJob.TryCreate();
        if (job is null || !job.IsValid)
        {
            return false;
        }
        var psi = new ProcessStartInfo("cmd.exe", "/c ping -n 60 127.0.0.1")
        {
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        using var process = Process.Start(psi);
        if (process is null || !job.TryAssign(process))
        {
            return false;
        }
        job.Dispose();
        return process.WaitForExit(5000);
    }

    private static void AttachConsoleIfPossible()
    {
        try
        {
            if (AttachConsole(AttachParentProcess))
            {
                Console.SetOut(new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true });
                Console.SetError(new StreamWriter(Console.OpenStandardError(), new UTF8Encoding(false)) { AutoFlush = true });
            }
        }
        catch
        {
            // 无父控制台时保持静默，报告仍写文件。
        }
    }

    public static string DownloadUrl => WebView2DownloadUrl;
}
