using MediaBrowser.Controller;
using MediaBrowser.Controller.Library;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Sesn.Jellyfin.Services;

/// <summary>
/// Keeps Sesn's list of this server's users current and learns which of them
/// are tracked. Household mapping itself happens on sesn.io; this only reports
/// user names and ids — never libraries, history or anything else.
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
            try
            {
                await TickAsync(stoppingToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception exception)
            {
                _logger.LogWarning(exception, "Sesn viewer sync failed");
            }

            await Task.Delay(TimeSpan.FromSeconds(30), stoppingToken).ConfigureAwait(false);
        }
    }

    private async Task TickAsync(CancellationToken cancellationToken)
    {
        var plugin = Plugin.Instance;
        var config = plugin?.Configuration;
        if (plugin is null || config is null || string.IsNullOrWhiteSpace(config.ApiKey) ||
            !string.Equals(config.PairingStatus, "connected", StringComparison.Ordinal))
        {
            return;
        }

        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        if (now - config.ViewersSyncedAt < (long)Interval.TotalSeconds)
        {
            return;
        }

        var viewers = _userManager.GetUsers().Select(user => (NormalizeId(user.Id.ToString()), user.Username)).ToList();
        var report = await _client.ReportViewersAsync(config, _applicationHost.SystemId, _applicationHost.FriendlyName, viewers, cancellationToken)
            .ConfigureAwait(false);
        if (report is null)
        {
            return;
        }

        config.Household = report.Household;
        config.TrackedViewerIds = (report.Tracked ?? []).Select(NormalizeId).Distinct().ToArray();
        config.ViewersSyncedAt = now;
        plugin.SaveConfiguration();
    }
}
