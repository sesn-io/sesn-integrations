using Microsoft.Extensions.Logging;

namespace Sesn.Jellyfin.Services;

/// <summary>
/// Validates the configured Sesn address before any key is sent to it. Only
/// https is accepted, except plain http to this machine for local testing.
/// </summary>
public static class SesnUrl
{
    /// <summary>The default Sesn origin.</summary>
    public const string Default = "https://sesn.io";

    private static string? _warnedFor;

    /// <summary>Resolves the configured origin, or returns null (and logs) when it is not safe to use.</summary>
    public static Uri? Resolve(string? configured, ILogger logger)
    {
        var raw = string.IsNullOrWhiteSpace(configured) ? Default : configured.Trim();
        if (!Uri.TryCreate(raw.TrimEnd('/') + "/", UriKind.Absolute, out var origin))
        {
            logger.LogError("Sesn address is not a valid URL; nothing will be sent until it is fixed");
            return null;
        }

        if (!IsAllowed(origin))
        {
            logger.LogError("Sesn address must start with https:// (http is only allowed for localhost); nothing will be sent to {Host}", origin.Host);
            return null;
        }

        if (!IsSesnHost(origin.Host) && !string.Equals(_warnedFor, origin.Host, StringComparison.OrdinalIgnoreCase))
        {
            _warnedFor = origin.Host;
            logger.LogWarning("Sesn address points at {Host}, not sesn.io; this server's Sesn key is sent there", origin.Host);
        }

        return origin;
    }

    /// <summary>Returns true for https, or http to a loopback host.</summary>
    public static bool IsAllowed(Uri origin)
    {
        if (string.Equals(origin.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }

        return string.Equals(origin.Scheme, Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase) && IsLoopback(origin.Host);
    }

    private static bool IsLoopback(string host) =>
        string.Equals(host, "localhost", StringComparison.OrdinalIgnoreCase) ||
        string.Equals(host, "127.0.0.1", StringComparison.Ordinal) ||
        string.Equals(host, "[::1]", StringComparison.Ordinal) ||
        string.Equals(host, "::1", StringComparison.Ordinal);

    private static bool IsSesnHost(string host) =>
        string.Equals(host, "sesn.io", StringComparison.OrdinalIgnoreCase) ||
        host.EndsWith(".sesn.io", StringComparison.OrdinalIgnoreCase);
}
