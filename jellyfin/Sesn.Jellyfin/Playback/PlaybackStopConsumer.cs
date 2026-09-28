using MediaBrowser.Controller.Events;
using MediaBrowser.Controller.Library;

namespace Sesn.Jellyfin.Playback;

/// <summary>Sends stops and completion state.</summary>
public sealed class PlaybackStopConsumer : IEventConsumer<PlaybackStopEventArgs>
{
    private readonly PlaybackEventSender _sender;

    /// <summary>Initializes the consumer.</summary>
    public PlaybackStopConsumer(PlaybackEventSender sender) => _sender = sender;

    /// <inheritdoc />
    public Task OnEvent(PlaybackStopEventArgs eventArgs) =>
        _sender.SendAsync("PlaybackStop", eventArgs, eventArgs.PlayedToCompletion);
}
