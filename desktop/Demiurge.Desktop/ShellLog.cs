using System.IO;
using System.Text;

namespace Demiurge.Desktop;

internal static class ShellLog
{
    private static readonly object Gate = new();

    public static void Info(string message) => Write("INFO", message, null);
    public static void Warn(string message, Exception? error = null) => Write("WARN", message, error);
    public static void Error(string message, Exception? error = null) => Write("ERROR", message, error);

    public static string Tail(string path, int lines = 30)
    {
        try
        {
            if (!File.Exists(path))
            {
                return string.Empty;
            }
            var queue = new Queue<string>();
            foreach (var line in File.ReadLines(path, Encoding.UTF8))
            {
                queue.Enqueue(line);
                if (queue.Count > lines)
                {
                    queue.Dequeue();
                }
            }
            return string.Join(Environment.NewLine, queue);
        }
        catch
        {
            return string.Empty;
        }
    }

    private static void Write(string level, string message, Exception? error)
    {
        var line = $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff}] [{level}] {message}";
        if (error is not null)
        {
            line += Environment.NewLine + error;
        }
        lock (Gate)
        {
            try
            {
                Directory.CreateDirectory(ShellPaths.LogsDir);
                File.AppendAllText(ShellPaths.ShellLogPath, line + Environment.NewLine, new UTF8Encoding(false));
            }
            catch
            {
                // 日志写不进去也不能影响启动。
            }
        }
    }
}
