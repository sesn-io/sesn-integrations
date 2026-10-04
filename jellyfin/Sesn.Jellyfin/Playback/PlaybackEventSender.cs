using MediaBrowser.Controller;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Entities.TV;
using MediaBrowser.Controller.Library;
using Microsoft.Extensions.Logging;
using Sesn.Jellyfin.Services;

namespace Sesn.Jellyfin.Playback;

/// <summary>Filters Jellyfin events to tracked viewers and creates the Sesn payload.</summary>
public sealed class PlaybackEventSender
{
    private readonly IServerApplicationHost _applicationHost;
    private readonly SesnApiClient _client;
    private readonly PlaybackThrottle _throttle;
    private readonly ILogger<PlaybackEventSender> _logger;

    /// <summary>Initializes the sender.</summary>
    public PlaybackEventSender(IServerApplicationHost applicationHost, SesnApiClient client, PlaybackThrottle throttle, ILogger<PlaybackEventSender> logger)
    {
        _applicationHost = applicationHost;
        _client = client;
        _throttle = throttle;
        _logger = logger;
    }

    /// <summary>
    /// Sends the event for each tracked local user. Household mode tracks the users
    /// Sesn lists; otherwise only the single selected viewer. Everyone else is skipped
    /// here, so an untracked user's playback never leaves the server.
    /// </summary>
    public async Task SendAsync(string notificationType, PlaybackProgressEventArgs eventArgs, bool? playedToCompletion = null)
    {
        var plugin = Plugin.Instance;
        var config = plugin?.Configuration;
        var apiKey = plugin?.Credentials.ApiKey;
        if (plugin is null || config is null || !config.Enabled || string.IsNullOrWhiteSpace(apiKey) || eventArgs.Item is null || eventArgs.Item.IsThemeMedia)
        {
            return;
        }

        // After Sesn rejects the key (401) nothing is sent until the server is paired again.
        if (string.Equals(config.PairingStatus, "rejected", StringComparison.Ordinal))
        {
            return;
        }

        var tracked = config.Household
            ? eventArgs.Users.Where(user => config.TrackedViewerIds.Contains(ViewerSyncService.NormalizeId(user.Id.ToString()), StringComparer.Ordinal))
            : eventArgs.Users.Where(user => !string.IsNullOrWhiteSpace(config.JellyfinUserId) && IdEquals(user.Id.ToString(), config.JellyfinUserId));
        var now = DateTime.UtcNow;
        foreach (var user in tracked.ToList())
        {
            var session = string.Join(
                '|',
                user.Id.ToString("N"),
                eventArgs.PlaySessionId ?? eventArgs.DeviceId ?? string.Empty,
                eventArgs.Item.Id.ToString("N"));
            if (!_throttle.ShouldSend(notificationType, session, eventArgs.IsPaused, now))
            {
                continue;
            }

            var payload = BuildPayload(notificationType, eventArgs.Item, eventArgs, user.Id.ToString(), user.Username, playedToCompletion);
            try
            {
                var status = await _client.SendPlaybackAsync(config, apiKey, payload, CancellationToken.None).ConfigureAwait(false);
                if (status == 401)
                {
                    _logger.LogWarning("Sesn rejected this server's key; playback delivery is paused until it is connected again");
                    plugin.MarkCredentialRejected();
                    return;
                }
            }
            catch (Exception exception)
            {
                // Jellyfin playback must never fail because Sesn is unavailable.
                _logger.LogWarning(exception, "Could not deliver {NotificationType} to Sesn", notificationType);
            }
        }
    }

    private Dictionary<string, object?> BuildPayload(
        string notificationType,
        BaseItem item,
        PlaybackProgressEventArgs eventArgs,
        string userId,
        string userName,
        bool? playedToCompletion)
    {
        var payload = new Dictionary<string, object?>(StringComparer.OrdinalIgnoreCase)
        {
            ["NotificationType"] = notificationType,
            ["ServerId"] = _applicationHost.SystemId,
            ["ServerName"] = _applicationHost.FriendlyName,
            ["ServerVersion"] = _applicationHost.ApplicationVersionString,
            ["UserId"] = userId,
            ["NotificationUsername"] = userName,
            ["ItemId"] = item.Id,
            ["ItemType"] = item.GetType().Name,
            ["Name"] = item.Name,
            ["Year"] = item.ProductionYear,
            ["RunTimeTicks"] = item.RunTimeTicks ?? 0,
            ["PlaybackPositionTicks"] = eventArgs.PlaybackPositionTicks ?? 0,
            ["IsPaused"] = eventArgs.IsPaused,
            ["DeviceId"] = eventArgs.DeviceId,
            ["DeviceName"] = eventArgs.DeviceName,
            ["ClientName"] = eventArgs.ClientName,
        };

        if (playedToCompletion.HasValue)
        {
            payload["PlayedToCompletion"] = playedToCompletion.Value;
        }

        if (item is Episode episode)
        {
            payload["SeriesName"] = episode.Series?.Name;
            payload["SeriesPremiereDate"] = episode.Series?.PremiereDate?.ToString("yyyy-MM-dd", System.Globalization.CultureInfo.InvariantCulture);
            payload["SeasonNumber"] = episode.Season?.IndexNumber;
            payload["EpisodeNumber"] = episode.IndexNumber;
            payload["EpisodeNumberEnd"] = episode.IndexNumberEnd;
        }

        foreach (var (provider, value) in item.ProviderIds)
        {
            payload[$"Provider_{provider.ToLowerInvariant()}"] = value;
        }

        return payload;
    }

    private static bool IdEquals(string left, string right) =>
        string.Equals(left.Replace("-", string.Empty, StringComparison.Ordinal), right.Replace("-", string.Empty, StringComparison.Ordinal), StringComparison.OrdinalIgnoreCase);
}
