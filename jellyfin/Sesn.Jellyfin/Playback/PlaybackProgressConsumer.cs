using MediaBrowser.Controller.Events;
using MediaBrowser.Controller.Library;

namespace Sesn.Jellyfin.Playback;

/// <summary>Sends progress, resume, and pause state.</summary>
public sealed class PlaybackProgressConsumer : IEventConsumer<PlaybackProgressEventArgs>
{
    private readonly PlaybackEventSender _sender;

    /// <summary>Initializes the consumer.</summary>
    public PlaybackProgressConsumer(PlaybackEventSender sender) => _sender = sender;

    /// <inheritdoc />
    public Task OnEvent(PlaybackProgressEventArgs eventArgs) => _sender.SendAsync("PlaybackProgress", eventArgs);
}
