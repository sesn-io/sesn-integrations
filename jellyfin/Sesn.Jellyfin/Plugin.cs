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
    /// <summary>The stable plugin identifier used by Jellyfin and the catalog.</summary>
    public static readonly Guid PluginId = Guid.Parse("d214481e-2ec6-4b17-91db-b45b705a06aa");

    /// <summary>Initializes the plugin.</summary>
    public Plugin(IApplicationPaths applicationPaths, IXmlSerializer xmlSerializer)
        : base(applicationPaths, xmlSerializer)
    {
        Credentials = new CredentialStore(Path.Combine(applicationPaths.PluginConfigurationsPath, "Sesn"));
        MigrateSecrets();
        Instance = this;
    }

    /// <summary>Gets the private store for the Sesn key and pending device code.</summary>
    public CredentialStore Credentials { get; }

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
        var incoming = (PluginConfiguration)configuration;
        var current = Configuration;
        var newKey = !string.IsNullOrWhiteSpace(incoming.ApiKey);
        if (newKey)
        {
            Credentials.Set(incoming.ApiKey, null);
        }

        // A rejected key stays rejected until someone pairs, disconnects or supplies a
        // new key; a stale copy of the settings page must not mark it connected again.
        if (string.Equals(current.PairingStatus, "rejected", StringComparison.Ordinal) && !newKey &&
            !incoming.PairingRequested && !incoming.DisconnectRequested)
        {
            incoming.PairingStatus = "rejected";
        }

        incoming.ApiKey = null;
        incoming.DeviceCode = null;
        incoming.HasCredential = Credentials.HasApiKey;
        base.UpdateConfiguration(incoming);
    }

    /// <summary>Marks the stored key as rejected by Sesn so nothing more is sent until the server is paired again.</summary>
    public void MarkCredentialRejected()
    {
        var config = Configuration;
        if (string.Equals(config.PairingStatus, "rejected", StringComparison.Ordinal))
        {
            return;
        }

        config.PairingStatus = "rejected";
        config.LastDeliveryStatus = "Sesn no longer accepts this server's key. Connect again.";
        SaveConfiguration();
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
