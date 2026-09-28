using MediaBrowser.Common.Updates;
using MediaBrowser.Controller;
using MediaBrowser.Controller.Events;
using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.Plugins;
using Microsoft.Extensions.DependencyInjection;
using Sesn.Jellyfin.Playback;
using Sesn.Jellyfin.Services;

namespace Sesn.Jellyfin;

/// <summary>Registers pairing, delivery, and playback listeners with Jellyfin.</summary>
public sealed class PluginServiceRegistrator : IPluginServiceRegistrator
{
    /// <inheritdoc />
    public void RegisterServices(IServiceCollection services, IServerApplicationHost applicationHost)
    {
        services.AddHttpClient<SesnApiClient>(client =>
        {
            client.Timeout = TimeSpan.FromSeconds(15);
            client.DefaultRequestHeaders.UserAgent.ParseAdd("Sesn-Jellyfin/0.3");
        });
        services.AddScoped<PlaybackEventSender>();
        services.AddHostedService<PairingService>();
        services.AddHostedService<ViewerSyncService>();
        services.AddScoped<IEventConsumer<PlaybackStartEventArgs>, PlaybackStartConsumer>();
        services.AddScoped<IEventConsumer<PlaybackProgressEventArgs>, PlaybackProgressConsumer>();
        services.AddScoped<IEventConsumer<PlaybackStopEventArgs>, PlaybackStopConsumer>();
    }
}
