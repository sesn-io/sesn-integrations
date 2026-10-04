using MediaBrowser.Controller;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Sesn.Jellyfin.Services;

/// <summary>Completes short-code pairing without exposing a Sesn password to Jellyfin.</summary>
public sealed class PairingService : BackgroundService
{
    private readonly SesnApiClient _client;
    private readonly IServerApplicationHost _applicationHost;
    private const int MaxRevokeAttempts = 8;
    private const double MaxRevokeDelaySeconds = 1800;
    private const string GaveUpMessage =
        "Disconnected here, but Sesn could not be reached to revoke this server's key. Remove it under Settings, Devices & API keys on sesn.io.";

    private readonly ILogger<PairingService> _logger;
    private int _revokeFailures;
    private DateTimeOffset _nextRevokeAttempt = DateTimeOffset.MinValue;

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

    private enum RevokeStep
    {
        Done,
        Wait,
        GaveUp,
    }

    private async Task TickAsync(CancellationToken cancellationToken)
    {
        var plugin = Plugin.Instance;
        if (plugin is null)
        {
            return;
        }

        var config = plugin.Configuration;
        var credentials = plugin.Credentials;
        if (config.DisconnectRequested)
        {
            if (!string.Equals(config.PairingStatus, "disconnecting", StringComparison.Ordinal))
            {
                config.PairingStatus = "disconnecting";
                plugin.SaveConfiguration();
            }

            var step = await TryRevokeAsync(config, credentials.ApiKey, cancellationToken).ConfigureAwait(false);
            if (step == RevokeStep.Wait)
            {
                return;
            }

            credentials.Clear();
            config.DisconnectRequested = false;
            config.PairingRequested = false;
            config.HasCredential = false;
            config.Household = false;
            config.TrackedViewerIds = [];
            config.ViewersSyncedAt = 0;
            config.UserCode = null;
            config.VerificationUrl = null;
            config.PairingStatus = "not_connected";
            config.LastDeliveryStatus = step == RevokeStep.GaveUp ? GaveUpMessage : null;
            plugin.SaveConfiguration();
            return;
        }

        if (config.PairingRequested)
        {
            if (!string.Equals(config.PairingStatus, "starting", StringComparison.Ordinal))
            {
                config.PairingStatus = "starting";
                plugin.SaveConfiguration();
            }

            var step = await TryRevokeAsync(config, credentials.ApiKey, cancellationToken).ConfigureAwait(false);
            if (step == RevokeStep.Wait)
            {
                return;
            }

            config.PairingRequested = false;
            credentials.Clear();
            config.HasCredential = false;
            config.UserCode = null;
            config.LastDeliveryStatus = step == RevokeStep.GaveUp ? GaveUpMessage : null;
            plugin.SaveConfiguration();

            var started = await _client.StartPairingAsync(config, _applicationHost.FriendlyName, cancellationToken).ConfigureAwait(false);
            if (started is null)
            {
                config.PairingStatus = "error";
                plugin.SaveConfiguration();
                return;
            }

            credentials.Set(null, started.DeviceCode);
            config.UserCode = started.UserCodeDisplay;
            config.VerificationUrl = started.VerificationUrl;
            config.PairingExpiresAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + started.ExpiresIn;
            config.PairingStatus = "pending";
            plugin.SaveConfiguration();
            return;
        }

        var deviceCode = credentials.DeviceCode;
        if (!string.Equals(config.PairingStatus, "pending", StringComparison.Ordinal) || string.IsNullOrWhiteSpace(deviceCode))
        {
            return;
        }

        if (config.PairingExpiresAt <= DateTimeOffset.UtcNow.ToUnixTimeSeconds())
        {
            config.PairingStatus = "expired";
            credentials.SetDeviceCode(null);
            plugin.SaveConfiguration();
            return;
        }

        var result = await _client.PollPairingAsync(config, deviceCode, cancellationToken).ConfigureAwait(false);
        if (result is null || string.Equals(result.Status, "pending", StringComparison.Ordinal))
        {
            return;
        }

        if (string.Equals(result.Status, "authorized", StringComparison.Ordinal) && !string.IsNullOrWhiteSpace(result.ApiKey))
        {
            credentials.Set(result.ApiKey, null);
            config.HasCredential = true;
            config.PairingStatus = "connected";
            config.LastDeliveryStatus = null;
            config.Household = false;
            config.TrackedViewerIds = [];
            config.ViewersSyncedAt = 0;
            plugin.SaveConfiguration();
            return;
        }

        credentials.SetDeviceCode(null);
        config.PairingStatus = "expired";
        plugin.SaveConfiguration();
    }

    /// <summary>
    /// Revokes the stored key with exponential backoff. 401/403 already count as
    /// revoked (see <see cref="SesnApiClient.RevokeAsync"/>). After
    /// <see cref="MaxRevokeAttempts"/> failures the key is forgotten locally anyway, so a
    /// Sesn outage can never block disconnecting or pairing again.
    /// </summary>
    private async Task<RevokeStep> TryRevokeAsync(Configuration.PluginConfiguration config, string? apiKey, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(apiKey))
        {
            ResetRevokeBackoff();
            return RevokeStep.Done;
        }

        var now = DateTimeOffset.UtcNow;
        if (now < _nextRevokeAttempt)
        {
            return RevokeStep.Wait;
        }

        bool revoked;
        try
        {
            revoked = await _client.RevokeAsync(config, apiKey, cancellationToken).ConfigureAwait(false);
        }
        catch (Exception exception) when (exception is not OperationCanceledException || !cancellationToken.IsCancellationRequested)
        {
            _logger.LogWarning(exception, "Sesn credential revocation failed");
            revoked = false;
        }

        if (revoked)
        {
            ResetRevokeBackoff();
            return RevokeStep.Done;
        }

        _revokeFailures++;
        if (_revokeFailures >= MaxRevokeAttempts)
        {
            _logger.LogWarning("Sesn could not be reached to revoke this server's key after {Attempts} attempts; forgetting it locally", _revokeFailures);
            ResetRevokeBackoff();
            return RevokeStep.GaveUp;
        }

        var delay = TimeSpan.FromSeconds(Math.Min(MaxRevokeDelaySeconds, 5 * Math.Pow(2, _revokeFailures - 1)));
        _nextRevokeAttempt = now + delay;
        return RevokeStep.Wait;
    }

    private void ResetRevokeBackoff()
    {
        _revokeFailures = 0;
        _nextRevokeAttempt = DateTimeOffset.MinValue;
    }
}
