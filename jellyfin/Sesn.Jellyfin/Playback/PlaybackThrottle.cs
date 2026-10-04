using System.Collections.Concurrent;

namespace Sesn.Jellyfin.Playback;

/// <summary>
/// Limits Jellyfin's progress ticks (about one every 10 s per stream) to one
/// delivery per <see cref="ProgressInterval"/> per playback session. Start and stop
/// always go through, and so does a progress tick that changes the paused state
/// (that is how Jellyfin reports pause and resume).
/// </summary>
public sealed class PlaybackThrottle
{
    /// <summary>The minimum gap between two progress deliveries for one session.</summary>
    public static readonly TimeSpan ProgressInterval = TimeSpan.FromSeconds(90);

    private static readonly TimeSpan StaleAfter = TimeSpan.FromHours(12);
    private readonly ConcurrentDictionary<string, SessionState> _sessions = new(StringComparer.Ordinal);

    /// <summary>Gets the number of sessions currently tracked.</summary>
    public int Count => _sessions.Count;

    /// <summary>Decides whether an event should be delivered, and records it if so.</summary>
    public bool ShouldSend(string notificationType, string sessionKey, bool isPaused, DateTime nowUtc)
    {
        switch (notificationType)
        {
            case "PlaybackStart":
                _sessions[sessionKey] = new SessionState(nowUtc, isPaused);
                Prune(nowUtc);
                return true;
            case "PlaybackStop":
                _sessions.TryRemove(sessionKey, out _);
                return true;
        }

        if (_sessions.TryGetValue(sessionKey, out var last) &&
            last.Paused == isPaused &&
            nowUtc - last.SentAtUtc < ProgressInterval)
        {
            return false;
        }

        _sessions[sessionKey] = new SessionState(nowUtc, isPaused);
        return true;
    }

    private void Prune(DateTime nowUtc)
    {
        if (_sessions.Count < 256)
        {
            return;
        }

        foreach (var (key, state) in _sessions)
        {
            if (nowUtc - state.SentAtUtc > StaleAfter)
            {
                _sessions.TryRemove(key, out _);
            }
        }
    }

    private readonly record struct SessionState(DateTime SentAtUtc, bool Paused);
}
