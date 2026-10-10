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
        "An old Sesn key could not be revoked. Remove the old connection under Connections, Jellyfin on sesn.io.";

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
        if (plugin is null) return;
        var state = plugin.CaptureState();
        var config = state.Configuration;
        var credentials = plugin.Credentials;

        // The private queue contains retired keys only. It is independent of any
        // newly connected key and always uses each old key's original destination.
        await DrainRevocationAsync(plugin, cancellationToken).ConfigureAwait(false);
        if (!plugin.IsCurrent(state)) return;

        if (config.DisconnectRequested)
        {
            if (credentials.NextRevocation is not null) return;
            plugin.TryUpdateState(state, current =>
            {
                current.DisconnectRequested = false;
                current.PairingRequested = false;
                current.Household = false;
                current.TrackedViewerIds = [];
                current.ViewersSyncedAt = 0;
                current.UserCode = null;
                current.VerificationUrl = null;
                current.PairingStatus = "not_connected";
            });
            return;
        }

        if (config.PairingRequested)
        {
            if (credentials.NextRevocation is not null) return;
            if (!plugin.TryUpdateState(state, current =>
            {
                current.PairingRequested = false;
                current.PairingStatus = "starting";
                current.UserCode = null;
                current.LastDeliveryStatus = null;
            })) return;

            LinkStartResponse? started;
            try
            {
                started = await _client.StartPairingAsync(config, _applicationHost.FriendlyName, cancellationToken).ConfigureAwait(false);
            }
            catch
            {
                plugin.DiscardPairingResult(state);
                throw;
            }
            var accepted = plugin.TryUpdateState(state, current =>
            {
                if (started is null)
                {
                    current.PairingStatus = "error";
                    return;
                }
                credentials.Set(null, started.DeviceCode);
                current.UserCode = started.UserCodeDisplay;
                current.VerificationUrl = started.VerificationUrl;
                current.PairingExpiresAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + started.ExpiresIn;
                current.PairingStatus = "pending";
            });
            if (!accepted) plugin.DiscardPairingResult(state);
            return;
        }

        var deviceCode = credentials.DeviceCode;
        if (!string.Equals(config.PairingStatus, "pending", StringComparison.Ordinal) || string.IsNullOrWhiteSpace(deviceCode)) return;
        if (config.PairingExpiresAt <= DateTimeOffset.UtcNow.ToUnixTimeSeconds())
        {
            plugin.TryUpdateState(state, current =>
            {
                current.PairingStatus = "expired";
                credentials.SetDeviceCode(null);
            });
            return;
        }

        var result = await _client.PollPairingAsync(config, deviceCode, cancellationToken).ConfigureAwait(false);
        if (result is null || string.Equals(result.Status, "pending", StringComparison.Ordinal)) return;
        if (string.Equals(result.Status, "authorized", StringComparison.Ordinal) && !string.IsNullOrWhiteSpace(result.ApiKey))
        {
            var accepted = plugin.TryUpdateState(state, current =>
            {
                credentials.Set(result.ApiKey, null);
                current.PairingStatus = "connected";
                current.LastDeliveryStatus = null;
                current.Household = false;
                current.TrackedViewerIds = [];
                current.ViewersSyncedAt = 0;
            });
            if (!accepted)
            {
                // Approval may have completed just as the administrator disconnected.
                // Retire the returned key; never reactivate the superseded session.
                plugin.DiscardPairingResult(state, deviceCode, result.ApiKey);
            }
            return;
        }

        plugin.TryUpdateState(state, current =>
        {
            credentials.SetDeviceCode(null);
            current.PairingStatus = "expired";
        });
    }

    private async Task DrainRevocationAsync(Plugin plugin, CancellationToken cancellationToken)
    {
        var credentials = plugin.Credentials;
        var pending = credentials.NextRevocation;
        if (pending is null || DateTimeOffset.UtcNow < pending.NextAttemptAt) return;
        bool revoked;
        try
        {
            revoked = await _client.RevokeAsync(
                new Configuration.PluginConfiguration { SesnBaseUrl = pending.BaseUrl },
                pending.ApiKey, cancellationToken).ConfigureAwait(false);
        }
        catch (Exception exception) when (exception is not OperationCanceledException || !cancellationToken.IsCancellationRequested)
        {
            _logger.LogWarning(exception, "Sesn credential revocation failed");
            revoked = false;
        }

        var failures = pending.Failures + 1;
        if (revoked || failures >= MaxRevokeAttempts)
        {
            credentials.CompleteRevocation(pending.Id);
            plugin.RefreshCredentialStatus(revoked ? null : GaveUpMessage);
            return;
        }

        var delay = TimeSpan.FromSeconds(Math.Min(MaxRevokeDelaySeconds, 5 * Math.Pow(2, failures - 1)));
        credentials.DelayRevocation(pending.Id, failures, DateTimeOffset.UtcNow + delay);
        plugin.RefreshCredentialStatus();
    }
}
