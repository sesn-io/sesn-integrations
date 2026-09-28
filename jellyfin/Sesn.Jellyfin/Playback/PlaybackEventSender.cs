using MediaBrowser.Controller;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Entities.TV;
using MediaBrowser.Controller.Library;
using Microsoft.Extensions.Logging;
using Sesn.Jellyfin.Services;

namespace Sesn.Jellyfin.Playback;

/// <summary>Filters Jellyfin events to the mapped viewer and creates the Sesn payload.</summary>
public sealed class PlaybackEventSender
{
    private readonly IServerApplicationHost _applicationHost;
    private readonly SesnApiClient _client;
    private readonly ILogger<PlaybackEventSender> _logger;

    /// <summary>Initializes the sender.</summary>
    public PlaybackEventSender(IServerApplicationHost applicationHost, SesnApiClient client, ILogger<PlaybackEventSender> logger)
    {
        _applicationHost = applicationHost;
        _client = client;
        _logger = logger;
    }

    /// <summary>Sends one event for the configured local user and skips all other users.</summary>
    public async Task SendAsync(string notificationType, PlaybackProgressEventArgs eventArgs, bool? playedToCompletion = null)
    {
        var config = Plugin.Instance?.Configuration;
        if (config is null || !config.Enabled || string.IsNullOrWhiteSpace(config.ApiKey) ||
            string.IsNullOrWhiteSpace(config.JellyfinUserId) || eventArgs.Item is null || eventArgs.Item.IsThemeMedia)
        {
            return;
        }

        var mappedUser = eventArgs.Users.FirstOrDefault(user => IdEquals(user.Id.ToString(), config.JellyfinUserId));
        if (mappedUser is null)
        {
            return;
        }

        var payload = BuildPayload(notificationType, eventArgs.Item, eventArgs, mappedUser.Id.ToString(), mappedUser.Username, playedToCompletion);
        try
        {
            await _client.SendPlaybackAsync(config, payload, CancellationToken.None).ConfigureAwait(false);
        }
        catch (Exception exception)
        {
            // Jellyfin playback must never fail because Sesn is unavailable.
            _logger.LogWarning(exception, "Could not deliver {NotificationType} to Sesn", notificationType);
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
