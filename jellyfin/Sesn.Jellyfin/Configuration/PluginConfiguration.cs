using MediaBrowser.Model.Plugins;

namespace Sesn.Jellyfin.Configuration;

/// <summary>Persistent configuration for one Sesn/Jellyfin installation.</summary>
public sealed class PluginConfiguration : BasePluginConfiguration
{
    /// <summary>Initializes secure defaults.</summary>
    public PluginConfiguration()
    {
        SesnBaseUrl = "https://sesn.io";
        Enabled = true;
        PairingStatus = "not_connected";
    }

    /// <summary>Gets or sets the Sesn origin. This is configurable for local testing.</summary>
    public string SesnBaseUrl { get; set; }

    /// <summary>Gets or sets a value indicating whether event delivery is enabled.</summary>
    public bool Enabled { get; set; }

    /// <summary>Gets or sets the Jellyfin user mapped to the paired Sesn viewer.</summary>
    public string? JellyfinUserId { get; set; }

    /// <summary>Gets or sets the display-only Jellyfin user name.</summary>
    public string? JellyfinUserName { get; set; }

    /// <summary>Gets or sets a value requesting a fresh device pairing.</summary>
    public bool PairingRequested { get; set; }

    /// <summary>Gets or sets a value requesting server-side credential revocation.</summary>
    public bool DisconnectRequested { get; set; }

    /// <summary>
    /// Gets or sets a legacy field. Before 0.4 the pending device code was stored here;
    /// it is now moved to the private credential file on load and is always null.
    /// </summary>
    public string? DeviceCode { get; set; }

    /// <summary>Gets or sets the short code shown to the administrator.</summary>
    public string? UserCode { get; set; }

    /// <summary>Gets or sets the human-readable pairing URL.</summary>
    public string? VerificationUrl { get; set; }

    /// <summary>Gets or sets the Unix expiry of the current pairing.</summary>
    public long PairingExpiresAt { get; set; }

    /// <summary>Gets or sets not_connected, starting, pending, connected, rejected, disconnecting, expired, or error.</summary>
    public string PairingStatus { get; set; }

    /// <summary>
    /// Gets or sets a write-only field. A key posted here (by sesn.io's browser setup)
    /// is moved to the private credential file and cleared; it is never read back.
    /// Before 0.4 the key was stored here and is migrated on load.
    /// </summary>
    public string? ApiKey { get; set; }

    /// <summary>Gets or sets a value indicating whether a Sesn key is stored. Set by the server only.</summary>
    public bool HasCredential { get; set; }

    /// <summary>Gets or sets whether old keys still await remote revocation. Set by the server.</summary>
    public bool RevocationPending { get; set; }

    /// <summary>Gets or sets a safe manual-cleanup warning after revocation retries are exhausted.</summary>
    public string? RevocationWarning { get; set; }

    /// <summary>Gets or sets the most recent durable completion acceptance time.</summary>
    public DateTime? LastDeliveredAtUtc { get; set; }

    /// <summary>Gets or sets server-owned counts of completed watches awaiting delivery or review.</summary>
    public bool HouseholdPlaybackPaused { get; set; }
    public int PendingCompletions { get; set; }
    public int HeldCompletions { get; set; }
    public int FailedCompletions { get; set; }
    public int ExpiredCompletions { get; set; }
    public string? LastCompletionStatus { get; set; }

    /// <summary>Gets or sets a value indicating whether Sesn routes this server's users by household mapping.</summary>
    public bool Household { get; set; }

    /// <summary>Gets or sets the local user ids (lower-case, no dashes) Sesn accepts playback for.</summary>
    public string[] TrackedViewerIds { get; set; } = [];

    /// <summary>Gets or sets the Unix time the user list was last reported to Sesn.</summary>
    public long ViewersSyncedAt { get; set; }

    /// <summary>Gets or sets a safe status message; response payloads and secrets are never retained.</summary>
    public string? LastDeliveryStatus { get; set; }
}
