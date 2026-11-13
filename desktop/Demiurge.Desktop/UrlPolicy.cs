using System.IO;

namespace Demiurge.Desktop;

internal enum NavigationDecision
{
    Block,
    Allow,
    OpenExternal,
}

/// <summary>
/// 壳层 URL 策略（纯函数，唯一属主）。导航判定与外链判定刻意分开：
/// - <see cref="DecideNavigation"/>：主窗口能不能自己导航过去；
/// - <see cref="IsExternalHttp"/>：能不能交给系统浏览器（只允许 http/https）；
/// - <see cref="IsLocalPath"/>：日志目录等本地路径，不得套 URL 白名单。
/// mailto:/ms-*/intent: 等系统协议默认拒绝，产品确实需要时再加显式条目与自测。
/// </summary>
internal static class UrlPolicy
{
    public static NavigationDecision DecideNavigation(string? target)
    {
        if (!Uri.TryCreate(target, UriKind.Absolute, out var uri))
        {
            return NavigationDecision.Block;
        }
        return DecideNavigation(uri);
    }

    public static NavigationDecision DecideNavigation(Uri? uri)
    {
        if (uri is null || !uri.IsAbsoluteUri || !IsHttp(uri.Scheme) || string.IsNullOrEmpty(uri.Host))
        {
            return NavigationDecision.Block;
        }
        if (IsLoopbackOrLocalhost(uri))
        {
            return NavigationDecision.Allow;
        }
        return NavigationDecision.OpenExternal;
    }

    public static bool IsExternalHttp(Uri? uri) =>
        uri is not null && uri.IsAbsoluteUri && IsHttp(uri.Scheme) && !string.IsNullOrEmpty(uri.Host);

    public static bool IsLocalPath(string? path) =>
        !string.IsNullOrWhiteSpace(path) && Path.IsPathFullyQualified(path);

    /// <summary>日志口径：只留 scheme + host，绝不打印 path/query（可能含 token）。</summary>
    public static string Describe(Uri uri) =>
        string.IsNullOrEmpty(uri.Host) ? $"{uri.Scheme}:" : $"{uri.Scheme}://{uri.Host}";

    public static string Describe(string? target) =>
        Uri.TryCreate(target, UriKind.Absolute, out var uri) ? Describe(uri) : "<无法解析>";

    private static bool IsHttp(string scheme) =>
        scheme.Equals(Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase) ||
        scheme.Equals(Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase);

    private static bool IsLoopbackOrLocalhost(Uri uri) =>
        uri.IsLoopback || uri.Host.Equals("localhost", StringComparison.OrdinalIgnoreCase);
}
