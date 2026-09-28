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

    /// <summary>Gets or sets the secret device grant while pairing is pending.</summary>
    public string? DeviceCode { get; set; }

    /// <summary>Gets or sets the short code shown to the administrator.</summary>
    public string? UserCode { get; set; }

    /// <summary>Gets or sets the human-readable pairing URL.</summary>
    public string? VerificationUrl { get; set; }

    /// <summary>Gets or sets the Unix expiry of the current pairing.</summary>
    public long PairingExpiresAt { get; set; }

    /// <summary>Gets or sets not_connected, starting, pending, connected, expired, or error.</summary>
    public string PairingStatus { get; set; }

    /// <summary>Gets or sets the installation credential returned by Sesn.</summary>
    public string? ApiKey { get; set; }

    /// <summary>Gets or sets the most recent successful delivery time.</summary>
    public DateTime? LastDeliveredAtUtc { get; set; }

    /// <summary>Gets or sets a safe status message; response payloads and secrets are never retained.</summary>
    public string? LastDeliveryStatus { get; set; }
}
