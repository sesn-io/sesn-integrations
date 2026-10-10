using MediaBrowser.Controller;
using MediaBrowser.Controller.Library;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Sesn.Jellyfin.Services;

/// <summary>
/// Keeps Sesn's list of this server's users current and learns which of them
/// are tracked. Solo mode reports only the selected local identity. An
/// authenticated household-mode response permits discovery of local names/ids
/// for mapping on sesn.io; this never reports libraries or history.
/// </summary>
public sealed class ViewerSyncService : BackgroundService
{
    private static readonly TimeSpan Interval = TimeSpan.FromMinutes(5);
    private readonly SesnApiClient _client;
    private readonly IServerApplicationHost _applicationHost;
    private readonly IUserManager _userManager;
    private readonly ILogger<ViewerSyncService> _logger;

    /// <summary>Initializes the viewer sync worker.</summary>
    public ViewerSyncService(SesnApiClient client, IServerApplicationHost applicationHost, IUserManager userManager, ILogger<ViewerSyncService> logger)
    {
        _client = client;
        _applicationHost = applicationHost;
        _userManager = userManager;
        _logger = logger;
    }

    /// <summary>Normalizes a Jellyfin user id the same way Sesn does.</summary>
    public static string NormalizeId(string id) => id.Replace("-", string.Empty, StringComparison.Ordinal).ToLowerInvariant();

    /// <inheritdoc />
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            var wait = TimeSpan.FromSeconds(30);
            try
            {
                if (!await TickAsync(stoppingToken).ConfigureAwait(false))
                {
                    // Any failure (including 401) waits the full interval, not 30 s.
                    wait = Interval;
                }
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception exception)
            {
                _logger.LogWarning(exception, "Sesn viewer sync failed");
                wait = Interval;
            }

            await Task.Delay(wait, stoppingToken).ConfigureAwait(false);
        }
    }

    /// <summary>Runs one sync check. Returns false when a report was attempted and failed.</summary>
    private async Task<bool> TickAsync(CancellationToken cancellationToken)
    {
        var plugin = Plugin.Instance;
        if (plugin is null || !plugin.TryGetDelivery(out var state, out var apiKey)) return true;
        var config = state.Configuration;

        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        if (now - config.ViewersSyncedAt < (long)Interval.TotalSeconds)
        {
            return true;
        }

        var selectedViewer = string.IsNullOrWhiteSpace(config.JellyfinUserId) ? null : NormalizeId(config.JellyfinUserId);
        // A missing/removed solo selection is not permission to discover everyone.
        // Household is server-owned and learned only from an authenticated reply.
        var viewers = _userManager.GetUsers()
            .Where(user => config.Household || (selectedViewer is not null && NormalizeId(user.Id.ToString()) == selectedViewer))
            .Select(user => (NormalizeId(user.Id.ToString()), user.Username)).ToList();
        using var delivery = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, state.CancellationToken);
        var (report, status) = await _client.ReportViewersAsync(config, apiKey, _applicationHost.SystemId, _applicationHost.FriendlyName, viewers, delivery.Token)
            .ConfigureAwait(false);
        if (status == 401)
        {
            plugin.MarkCredentialRejected(state);
            return false;
        }

        if (report is null)
        {
            return false;
        }

        plugin.ApplyViewerReport(state, apiKey, report, now);
        return true;
    }
}
