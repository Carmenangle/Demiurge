using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows;

namespace Demiurge.Desktop;

internal sealed class ShellSettings
{
    public const double DefaultWidth = 1280;
    public const double DefaultHeight = 860;
    public const double MinWidth = 720;
    public const double MinHeight = 520;

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
        PropertyNameCaseInsensitive = true,
    };

    public double? Width { get; set; }
    public double? Height { get; set; }
    public double? Left { get; set; }
    public double? Top { get; set; }
    public bool Maximized { get; set; }
    public int Port { get; set; } = RuntimeHost.DefaultPort;

    public static ShellSettings Load(string? path = null)
    {
        var actualPath = path ?? ShellPaths.SettingsPath;
        try
        {
            if (!File.Exists(actualPath))
            {
                return CreateDefault();
            }
            var settings = JsonSerializer.Deserialize<ShellSettings>(
                File.ReadAllText(actualPath, Encoding.UTF8), JsonOptions) ?? CreateDefault();
            settings.Normalize();
            return settings;
        }
        catch (Exception ex)
        {
            ShellLog.Warn($"读取桌面设置失败，改用默认值：{actualPath}", ex);
            return CreateDefault();
        }
    }

    public void Save(string? path = null)
    {
        var actualPath = path ?? ShellPaths.SettingsPath;
        Normalize();
        var directory = Path.GetDirectoryName(actualPath);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }
        var temporary = actualPath + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(this, JsonOptions), new UTF8Encoding(false));
        File.Move(temporary, actualPath, overwrite: true);
    }

    public void Normalize()
    {
        Width = Math.Clamp(Finite(Width, DefaultWidth), MinWidth, 12000);
        Height = Math.Clamp(Finite(Height, DefaultHeight), MinHeight, 12000);
        if (Port is < 1 or > 65535)
        {
            Port = RuntimeHost.DefaultPort;
        }
        if (Finite(Left, double.NaN) is var left && Finite(Top, double.NaN) is var top &&
            double.IsFinite(left) && double.IsFinite(top))
        {
            Left = left;
            Top = top;
            ClampToVirtualScreen();
        }
        else
        {
            Left = null;
            Top = null;
        }
    }

    private void ClampToVirtualScreen()
    {
        var screenLeft = SystemParameters.VirtualScreenLeft;
        var screenTop = SystemParameters.VirtualScreenTop;
        var screenWidth = SystemParameters.VirtualScreenWidth;
        var screenHeight = SystemParameters.VirtualScreenHeight;
        if (!double.IsFinite(screenWidth) || screenWidth <= 0 || !double.IsFinite(screenHeight) || screenHeight <= 0)
        {
            return;
        }
        var maxWidth = Math.Max(MinWidth, screenWidth - 40);
        var maxHeight = Math.Max(MinHeight, screenHeight - 80);
        Width = Math.Min(Width ?? DefaultWidth, maxWidth);
        Height = Math.Min(Height ?? DefaultHeight, maxHeight);

        var leftMin = screenLeft;
        var leftMax = Math.Max(leftMin, screenLeft + screenWidth - Width.Value);
        Left = Math.Clamp(Left ?? leftMin, leftMin, leftMax);
        var topMin = screenTop;
        var topMax = Math.Max(topMin, screenTop + screenHeight - Height.Value);
        Top = Math.Clamp(Top ?? topMin, topMin, topMax);
    }

    private static double Finite(double? value, double fallback) =>
        value is { } actual && double.IsFinite(actual) ? actual : fallback;

    private static ShellSettings CreateDefault()
    {
        var settings = new ShellSettings();
        settings.Normalize();
        return settings;
    }
}
