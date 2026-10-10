using System.Globalization;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Plugins;
using MediaBrowser.Model.Serialization;
using Sesn.Jellyfin.Configuration;
using Sesn.Jellyfin.Services;

namespace Sesn.Jellyfin;

/// <summary>The native Sesn watch tracking plugin.</summary>
public sealed class Plugin : BasePlugin<PluginConfiguration>, IHasWebPages
{
    private readonly object _connectionGate = new();
    private long _connectionVersion;
    private CompletionAuthorization? _completionAuthorization;
    private long _viewerPermissionConfirmedAt;

    /// <summary>Five-minute refresh cadence plus one minute of scheduling grace.</summary>
    public static readonly TimeSpan HouseholdPermissionLifetime = TimeSpan.FromMinutes(6);
    private CancellationTokenSource _deliveryCancellation = new();

    /// <summary>The stable plugin identifier used by Jellyfin and the catalog.</summary>
    public static readonly Guid PluginId = Guid.Parse("d214481e-2ec6-4b17-91db-b45b705a06aa");

    /// <summary>Initializes the plugin.</summary>
    public Plugin(IApplicationPaths applicationPaths, IXmlSerializer xmlSerializer)
        : base(applicationPaths, xmlSerializer)
    {
        Credentials = new CredentialStore(Path.Combine(applicationPaths.PluginConfigurationsPath, "Sesn"));
        Completions = new CompletionOutbox(Path.Combine(applicationPaths.PluginConfigurationsPath, "Sesn"));
        Configuration.ViewersSyncedAt = 0; // Capabilities must be revalidated after every restart.
        MigrateSecrets();
        // Upgrade/restart while disconnecting must not reactivate the old key.
        if (Configuration.DisconnectRequested || Configuration.PairingRequested)
        {
            Credentials.Retire(Configuration.SesnBaseUrl);
        }
        RefreshCredentialStatus();
        RefreshCompletionStatus();
        Instance = this;
    }

    /// <summary>Gets the private store for the Sesn key and pending device code.</summary>
    public CredentialStore Credentials { get; }

    /// <summary>Gets private completed-event storage; it never contains an API key.</summary>
    public CompletionOutbox Completions { get; }

    /// <inheritdoc />
    public override string Name => "Sesn";

    /// <inheritdoc />
    public override string Description => "Securely sync Jellyfin playback and completed watches with Sesn.";

    /// <inheritdoc />
    public override Guid Id => PluginId;

    /// <summary>Gets the active plugin instance.</summary>
    public static Plugin? Instance { get; private set; }

    /// <summary>
    /// Accepts a configuration posted through Jellyfin's API. A Sesn key in it is
    /// moved to the private credential store and stripped, so it is never stored
    /// in, or served back from, the plugin configuration.
    /// </summary>
    public override void UpdateConfiguration(BasePluginConfiguration configuration)
    {
        ArgumentNullException.ThrowIfNull(configuration);
        lock (_connectionGate)
        {
            var incoming = (PluginConfiguration)configuration;
            var current = Configuration;
            var suppliedKey = string.IsNullOrWhiteSpace(incoming.ApiKey) ? null : incoming.ApiKey.Trim();
            var newKey = suppliedKey is not null;
            var activeKey = Credentials.ApiKey;
            var destinationChanged = !string.Equals(
                NormalizeDestination(current.SesnBaseUrl), NormalizeDestination(incoming.SesnBaseUrl), StringComparison.Ordinal);
            var freshKey = newKey && !string.Equals(suppliedKey, activeKey, StringComparison.Ordinal);
            if (newKey && !incoming.DisconnectRequested && !incoming.PairingRequested && Credentials.IsRetiredKey(suppliedKey!))
            {
                throw new InvalidOperationException("This key is being revoked. Connect with a newly issued key.");
            }
            InvalidateDelivery();
            if (incoming.DisconnectRequested || incoming.PairingRequested || destinationChanged || freshKey)
            {
                // Capture the old destination as well as the old key. A replacement
                // may change SesnBaseUrl, but that must never redirect revocation.
                Credentials.Retire(current.SesnBaseUrl);
            }
            if (newKey && (!destinationChanged || freshKey) && !incoming.DisconnectRequested && !incoming.PairingRequested)
            {
                Credentials.Set(suppliedKey, null);
                incoming.PairingStatus = "connected";
            }
            else if (incoming.DisconnectRequested)
            {
                incoming.PairingRequested = false;
                incoming.PairingStatus = "disconnecting";
            }
            else if (incoming.PairingRequested)
            {
                incoming.PairingStatus = "starting";
            }
            else if (destinationChanged)
            {
                // Editing an address does not authorize reusing its predecessor's
                // credential there, including when the same key is posted again.
                incoming.PairingStatus = "not_connected";
                incoming.PairingRequested = false;
                incoming.DisconnectRequested = false;
                incoming.UserCode = null;
                incoming.VerificationUrl = null;
                incoming.PairingExpiresAt = 0;
                incoming.Household = false;
                incoming.TrackedViewerIds = [];
                incoming.ViewersSyncedAt = 0;
                incoming.LastDeliveryStatus = "Sesn address changed. Connect again for this address.";
            }
            else
            {
                // Connection state is server-owned. Stale settings cannot reconnect
                // an installation or cancel an already requested local disconnect.
                incoming.PairingStatus = current.PairingStatus;
                incoming.DisconnectRequested = current.DisconnectRequested;
                incoming.PairingRequested = current.PairingRequested;
            }

            // Capability and routing responses are server-owned, never trusted from a settings post.
            _completionAuthorization = null;
            _viewerPermissionConfirmedAt = 0;
            incoming.ViewersSyncedAt = 0;
            incoming.Household = current.Household;
            incoming.TrackedViewerIds = current.TrackedViewerIds;
            if (incoming.DisconnectRequested || incoming.PairingRequested || destinationChanged || freshKey)
            {
                incoming.Household = false;
                incoming.TrackedViewerIds = [];
            }
            incoming.ApiKey = null;
            incoming.DeviceCode = null;
            incoming.HasCredential = Credentials.HasApiKey;
            incoming.RevocationPending = Credentials.NextRevocation is not null;
            incoming.RevocationWarning = current.RevocationWarning;
            base.UpdateConfiguration(incoming);
            RefreshCompletionStatus();
        }
    }

    /// <summary>Captures a configuration generation so async replies cannot modify a replacement.</summary>
    public ConnectionState CaptureState()
    {
        lock (_connectionGate)
        {
            return new ConnectionState(Configuration, _connectionVersion, _deliveryCancellation.Token);
        }
    }

    /// <summary>Gets permission for delivery only while this installation is actively connected.</summary>
    public bool TryGetDelivery(out ConnectionState state, out string apiKey)
    {
        lock (_connectionGate)
        {
            state = CaptureState();
            apiKey = Credentials.ApiKey ?? string.Empty;
            var config = state.Configuration;
            return config.Enabled && !config.DisconnectRequested && !config.PairingRequested &&
                string.Equals(config.PairingStatus, "connected", StringComparison.Ordinal) && apiKey.Length > 0;
        }
    }

    /// <summary>Household playback pauses when cached permissions expire; discovery can still refresh them.</summary>
    public bool HasPlaybackPermission(ConnectionState state)
    {
        lock (_connectionGate) return IsCurrent(state) && HouseholdPermissionIsCurrent();
    }

    private bool HouseholdPermissionIsCurrent()
    {
        if (!Configuration.Household) return true;
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        return _viewerPermissionConfirmedAt > 0 && now >= _viewerPermissionConfirmedAt &&
            now - _viewerPermissionConfirmedAt < (long)HouseholdPermissionLifetime.TotalSeconds;
    }

    /// <summary>Returns only authenticated, currently permitted completion recipients.</summary>
    public CompletionAuthorization? GetCompletionAuthorization(ConnectionState state, string apiKey)
    {
        lock (_connectionGate)
        {
            if (!IsCurrent(state)) return null;
            var config = Configuration;
            var owner = CompletionOutbox.OwnerFingerprint(apiKey, config.SesnBaseUrl);
            var allowed = config.Household ? config.TrackedViewerIds :
                string.IsNullOrWhiteSpace(config.JellyfinUserId) ? [] : new[] { ViewerSyncService.NormalizeId(config.JellyfinUserId) };
            if (!HouseholdPermissionIsCurrent()) allowed = [];
            var known = _completionAuthorization;
            return new CompletionAuthorization(owner, known?.Owner == owner ? known.ProtocolVersion : 0,
                known?.Owner == owner ? known.CredentialGeneration : null, config.Household,
                known?.Owner == owner ? known.MappingGenerations : new Dictionary<string, string>(), allowed.ToArray());
        }
    }

    /// <summary>Applies capability and immutable generation metadata from the current authenticated report.</summary>
    public bool ApplyViewerReport(ConnectionState state, string apiKey, ViewerReport report, long now)
    {
        lock (_connectionGate)
        {
            if (!IsCurrent(state)) return false;
            InvalidateDelivery();
            Configuration.Household = report.Household;
            Configuration.TrackedViewerIds = (report.Tracked ?? []).Take(100).Where(id => !string.IsNullOrWhiteSpace(id))
                .Select(ViewerSyncService.NormalizeId).Distinct(StringComparer.Ordinal).ToArray();
            Configuration.ViewersSyncedAt = now;
            _viewerPermissionConfirmedAt = now;
            var mappings = (report.MappingGenerations ?? []).Take(100)
                .Where(pair => !string.IsNullOrWhiteSpace(pair.Key) && CompletionOutbox.IsGeneration(pair.Value))
                .GroupBy(pair => ViewerSyncService.NormalizeId(pair.Key), StringComparer.Ordinal)
                .Where(group => group.Count() == 1 && Configuration.TrackedViewerIds.Contains(group.Key, StringComparer.Ordinal))
                .ToDictionary(group => group.Key, group => group.Single().Value, StringComparer.Ordinal);
            _completionAuthorization = new(CompletionOutbox.OwnerFingerprint(apiKey, Configuration.SesnBaseUrl),
                report.DurableCompletion == 1 && CompletionOutbox.IsGeneration(report.CredentialGeneration) ? 1 : 0,
                report.CredentialGeneration, report.Household, mappings, []);
            RefreshCompletionStatus();
            SaveConfiguration();
            return true;
        }
    }

    /// <summary>Exposes counts and fixed safe messages, never payloads, identities or credentials.</summary>
    public void RefreshCompletionStatus(string? storageError = null)
    {
        lock (_connectionGate)
        {
            var previous = (Configuration.PendingCompletions, Configuration.HeldCompletions, Configuration.FailedCompletions,
                Configuration.ExpiredCompletions, Configuration.LastDeliveredAtUtc, Configuration.LastCompletionStatus, Configuration.HouseholdPlaybackPaused);
            try
            {
                var auth = TryGetDelivery(out var state, out var key) ? GetCompletionAuthorization(state, key) : null;
                Configuration.HouseholdPlaybackPaused = auth is not null && !HouseholdPermissionIsCurrent();
                var status = Completions.Status(auth);
                Configuration.PendingCompletions = status.Pending;
                Configuration.HeldCompletions = status.Held;
                Configuration.FailedCompletions = status.Failed;
                Configuration.ExpiredCompletions = status.Expired;
                Configuration.LastDeliveredAtUtc = status.LastAcceptedAt is { } timestamp ? DateTimeOffset.FromUnixTimeSeconds(timestamp).UtcDateTime : null;
                Configuration.LastCompletionStatus = storageError ?? (Configuration.HouseholdPlaybackPaused
                    ? "Household playback is paused while viewer permissions refresh. Queued completions stay held; use Sync from your server to recover watches missed during the pause."
                    : status.LastError);
            }
            catch (Exception)
            {
                Configuration.LastCompletionStatus = "Completion storage is unavailable. Use Sync from your server to recover missing watches.";
            }
            var current = (Configuration.PendingCompletions, Configuration.HeldCompletions, Configuration.FailedCompletions,
                Configuration.ExpiredCompletions, Configuration.LastDeliveredAtUtc, Configuration.LastCompletionStatus, Configuration.HouseholdPlaybackPaused);
            if (previous != current) SaveConfiguration();
        }
    }

    /// <summary>Reports whether a captured connection is still current.</summary>
    public bool IsCurrent(ConnectionState state)
    {
        lock (_connectionGate)
        {
            return state.Version == _connectionVersion && ReferenceEquals(state.Configuration, Configuration);
        }
    }

    /// <summary>Applies a current reply under the same lock used by configuration updates.</summary>
    public bool TryUpdateState(ConnectionState state, Action<PluginConfiguration> update)
    {
        lock (_connectionGate)
        {
            if (!IsCurrent(state)) return false;
            update(Configuration);
            RefreshCredentialStatus();
            return true;
        }
    }

    /// <summary>Rejects only the key which received the rejection, never its replacement.</summary>
    public void MarkCredentialRejected(ConnectionState state)
    {
        lock (_connectionGate)
        {
            if (!IsCurrent(state)) return;
            InvalidateDelivery();
            Configuration.PairingStatus = "rejected";
            Configuration.LastDeliveryStatus = "Sesn no longer accepts this server's key. Connect again.";
            SaveConfiguration();
        }
    }

    /// <summary>Refreshes only safe public status fields after independent revocation work.</summary>
    public void RefreshCredentialStatus(string? revocationWarning = null)
    {
        lock (_connectionGate)
        {
            Configuration.HasCredential = Credentials.HasApiKey;
            Configuration.RevocationPending = Credentials.NextRevocation is not null;
            if (revocationWarning is not null) Configuration.RevocationWarning = revocationWarning;
            SaveConfiguration();
        }
    }

    /// <summary>Discards a superseded pairing response without leaving a reusable old grant.</summary>
    public void DiscardPairingResult(ConnectionState state, string? deviceCode = null, string? apiKey = null)
    {
        lock (_connectionGate)
        {
            if (!string.IsNullOrWhiteSpace(apiKey) &&
                !(Credentials.ApiKey == apiKey && Configuration.SesnBaseUrl == state.Configuration.SesnBaseUrl))
            {
                Credentials.QueueRevocation(apiKey, state.Configuration.SesnBaseUrl);
            }

            if (deviceCode is not null && Credentials.DeviceCode == deviceCode && Configuration.PairingStatus == "pending")
            {
                // A settings update can supersede a poll without clearing its device
                // code. That grant must not be polled again after retiring its key.
                Credentials.SetDeviceCode(null);
                Configuration.UserCode = null;
                Configuration.VerificationUrl = null;
                Configuration.PairingStatus = "expired";
            }
            else if (deviceCode is null && Configuration.PairingStatus == "starting" &&
                     !Configuration.PairingRequested && !Configuration.DisconnectRequested)
            {
                Configuration.PairingStatus = "error";
            }
            RefreshCredentialStatus();
        }
    }

    private static string NormalizeDestination(string? configured)
    {
        var value = string.IsNullOrWhiteSpace(configured) ? SesnUrl.Default : configured.Trim();
        value = value.TrimEnd('/') + "/";
        return Uri.TryCreate(value, UriKind.Absolute, out var uri) ? uri.AbsoluteUri : value;
    }

    private void InvalidateDelivery()
    {
        _connectionVersion++;
        _deliveryCancellation.Cancel();
        _deliveryCancellation.Dispose();
        _deliveryCancellation = new CancellationTokenSource();
    }

    /// <inheritdoc />
    public IEnumerable<PluginPageInfo> GetPages()
    {
        return
        [
            new PluginPageInfo
            {
                Name = Name,
                EmbeddedResourcePath = string.Format(
                    CultureInfo.InvariantCulture,
                    "{0}.Configuration.configPage.html",
                    GetType().Namespace),
            },
        ];
    }

    private void MigrateSecrets()
    {
        // Before 0.4 the key and device code were kept in the configuration XML.
        var config = Configuration;
        var changed = false;
        if (!string.IsNullOrWhiteSpace(config.ApiKey) || !string.IsNullOrWhiteSpace(config.DeviceCode))
        {
            Credentials.Set(
                string.IsNullOrWhiteSpace(config.ApiKey) ? Credentials.ApiKey : config.ApiKey,
                string.IsNullOrWhiteSpace(config.DeviceCode) ? Credentials.DeviceCode : config.DeviceCode);
            config.ApiKey = null;
            config.DeviceCode = null;
            changed = true;
        }

        if (config.HasCredential != Credentials.HasApiKey)
        {
            config.HasCredential = Credentials.HasApiKey;
            changed = true;
        }

        if (changed)
        {
            SaveConfiguration();
        }
    }
}

/// <summary>One in-process connection generation and its cancellable delivery permission.</summary>
public readonly record struct ConnectionState(PluginConfiguration Configuration, long Version, CancellationToken CancellationToken);
