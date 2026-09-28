using MediaBrowser.Controller.Events;
using MediaBrowser.Controller.Library;

namespace Sesn.Jellyfin.Playback;

/// <summary>Sends playback starts.</summary>
public sealed class PlaybackStartConsumer : IEventConsumer<PlaybackStartEventArgs>
{
    private readonly PlaybackEventSender _sender;

    /// <summary>Initializes the consumer.</summary>
    public PlaybackStartConsumer(PlaybackEventSender sender) => _sender = sender;

    /// <inheritdoc />
    public Task OnEvent(PlaybackStartEventArgs eventArgs) => _sender.SendAsync("PlaybackStart", eventArgs);
}
