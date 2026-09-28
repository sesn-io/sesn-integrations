using System.Globalization;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Plugins;
using MediaBrowser.Model.Serialization;
using Sesn.Jellyfin.Configuration;

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
        Instance = this;
    }

    /// <inheritdoc />
    public override string Name => "Sesn";

    /// <inheritdoc />
    public override string Description => "Securely sync Jellyfin playback and completed watches with Sesn.";

    /// <inheritdoc />
    public override Guid Id => PluginId;

    /// <summary>Gets the active plugin instance.</summary>
    public static Plugin? Instance { get; private set; }

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
}
