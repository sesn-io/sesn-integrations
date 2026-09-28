using MediaBrowser.Controller;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Sesn.Jellyfin.Services;

/// <summary>Completes short-code pairing without exposing a Sesn password to Jellyfin.</summary>
public sealed class PairingService : BackgroundService
{
    private readonly SesnApiClient _client;
    private readonly IServerApplicationHost _applicationHost;
    private readonly ILogger<PairingService> _logger;

    /// <summary>Initializes the pairing worker.</summary>
    public PairingService(SesnApiClient client, IServerApplicationHost applicationHost, ILogger<PairingService> logger)
    {
        _client = client;
        _applicationHost = applicationHost;
        _logger = logger;
    }

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
                _logger.LogWarning(exception, "Sesn pairing check failed");
            }

            await Task.Delay(TimeSpan.FromSeconds(3), stoppingToken).ConfigureAwait(false);
        }
    }

    private async Task TickAsync(CancellationToken cancellationToken)
    {
        var plugin = Plugin.Instance;
        if (plugin is null)
        {
            return;
        }

        var config = plugin.Configuration;
        if (config.DisconnectRequested)
        {
            config.PairingStatus = "disconnecting";
            plugin.SaveConfiguration();
            if (!await _client.RevokeAsync(config, cancellationToken).ConfigureAwait(false))
            {
                config.PairingStatus = "error";
                plugin.SaveConfiguration();
                return;
            }

            config.DisconnectRequested = false;
            config.PairingRequested = false;
            config.ApiKey = null;
            config.Household = false;
            config.TrackedViewerIds = [];
            config.ViewersSyncedAt = 0;
            config.DeviceCode = null;
            config.UserCode = null;
            config.VerificationUrl = null;
            config.PairingStatus = "not_connected";
            plugin.SaveConfiguration();
            return;
        }

        if (config.PairingRequested)
        {
            config.PairingRequested = false;
            config.PairingStatus = "starting";
            if (!await _client.RevokeAsync(config, cancellationToken).ConfigureAwait(false))
            {
                config.PairingStatus = "error";
                plugin.SaveConfiguration();
                return;
            }
            config.ApiKey = null;
            config.DeviceCode = null;
            config.UserCode = null;
            plugin.SaveConfiguration();

            var started = await _client.StartPairingAsync(config, _applicationHost.FriendlyName, cancellationToken).ConfigureAwait(false);
            if (started is null)
            {
                config.PairingStatus = "error";
                plugin.SaveConfiguration();
                return;
            }

            config.DeviceCode = started.DeviceCode;
            config.UserCode = started.UserCodeDisplay;
            config.VerificationUrl = started.VerificationUrl;
            config.PairingExpiresAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + started.ExpiresIn;
            config.PairingStatus = "pending";
            plugin.SaveConfiguration();
            return;
        }

        if (!string.Equals(config.PairingStatus, "pending", StringComparison.Ordinal) || string.IsNullOrWhiteSpace(config.DeviceCode))
        {
            return;
        }

        if (config.PairingExpiresAt <= DateTimeOffset.UtcNow.ToUnixTimeSeconds())
        {
            config.PairingStatus = "expired";
            config.DeviceCode = null;
            plugin.SaveConfiguration();
            return;
        }

        var result = await _client.PollPairingAsync(config, cancellationToken).ConfigureAwait(false);
        if (result is null || string.Equals(result.Status, "pending", StringComparison.Ordinal))
        {
            return;
        }

        if (string.Equals(result.Status, "authorized", StringComparison.Ordinal) && !string.IsNullOrWhiteSpace(result.ApiKey))
        {
            config.ApiKey = result.ApiKey;
            config.DeviceCode = null;
            config.PairingStatus = "connected";
            config.Household = false;
            config.TrackedViewerIds = [];
            config.ViewersSyncedAt = 0;
            plugin.SaveConfiguration();
            return;
        }

        config.DeviceCode = null;
        config.PairingStatus = "expired";
        plugin.SaveConfiguration();
    }
}
