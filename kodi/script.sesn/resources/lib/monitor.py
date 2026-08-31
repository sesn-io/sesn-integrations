"""Playback watcher -> Sesn scrobbles.

Subclass xbmc.Player for the playback callbacks. Fire the start on onAVStarted
(the stream/infotag is ready there, not on onPlayBackStarted), and close on BOTH
onPlayBackEnded and onPlayBackStopped because either can silently fail to fire.
Live/PVR and non-video are excluded.

⚠ Callbacks run on Kodi's thread, so they do NO network I/O — they only read the
infotag (fast, local) and push an event onto an in-memory deque. The service loop
(a separate context) drains the deque and does the actual sending, so a slow POST
can never stall the UI.
"""
import collections

import xbmc
import xbmcaddon
import xbmcgui

import scrobble_queue

ADDON = xbmcaddon.Addon()

# Match the server's completion gate purely for the "Logged X" toast wording.
NOTIFY_AT = 80


def _notify(msg):
    if ADDON.getSettingBool("notify"):
        xbmcgui.Dialog().notification("Sesn", msg, xbmcgui.NOTIFICATION_INFO, 2500)


def _uid(tag, kind):
    try:
        v = tag.getUniqueID(kind)
        return v.strip() if v else ""
    except Exception:
        return ""


def _as_id(v):
    return int(v) if v.isdigit() else v


def _percent(player):
    try:
        total = player.getTotalTime()
        if total and total > 0:
            return round((player.getTime() / total) * 100)
    except Exception:
        pass
    return None


class SesnPlayer(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.events = collections.deque()  # (payload dicts) pending send
        self._current = None  # identity of what's playing, or None
        self._last_percent = 0

    # ---- identity ----------------------------------------------------------
    def _build_ref(self):
        if not self.isPlayingVideo():
            return None
        try:
            path = self.getPlayingFile()
        except Exception:
            path = ""
        if path.startswith("pvr://"):  # live TV — never scrobble
            return None
        try:
            tag = self.getVideoInfoTag()
        except Exception:
            return None

        mediatype = (tag.getMediaType() or "").lower()
        imdb, tmdb, tvdb = _uid(tag, "imdb"), _uid(tag, "tmdb"), _uid(tag, "tvdb")

        if mediatype == "episode" or tag.getTVShowTitle():
            if not ADDON.getSettingBool("scrobble_episodes"):
                return None
            season, episode = tag.getSeason(), tag.getEpisode()
            if season is None or episode is None or season < 0 or episode < 0:
                return None
            ref = {
                "type": "episode",
                "title": tag.getTVShowTitle(),
                "season": season,
                "episode": episode,
            }
            # Show-level ids aid resolution; the server resolves by series, and an
            # episode's own id is not the series id (same rule as the webhook).
            if tvdb:
                ref["tvdb_id"] = _as_id(tvdb)
            if tmdb:
                ref["tmdb_id"] = _as_id(tmdb)
            if imdb:
                ref["imdb_id"] = imdb
            return ref

        if not ADDON.getSettingBool("scrobble_movies"):
            return None
        title = tag.getTitle()
        if not (title or imdb or tmdb):
            return None
        ref = {"type": "movie", "title": title}
        year = tag.getYear()
        if year:
            ref["year"] = year
        if tmdb:
            ref["tmdb_id"] = _as_id(tmdb)
        if imdb:
            ref["imdb_id"] = imdb
        if tvdb:
            ref["tvdb_id"] = _as_id(tvdb)
        return ref

    def _queue(self, action, progress=None):
        if not self._current:
            return
        payload = dict(self._current)
        payload["action"] = action
        if progress is not None:
            payload["progress"] = progress
        self.events.append(payload)

    # ---- callbacks (Kodi thread — keep light) ------------------------------
    def onAVStarted(self):
        ref = self._build_ref()
        # If something was playing and we never saw its stop, flush it now.
        if self._current and ref != self._current:
            self._queue("stop", self._last_percent)
        self._current = ref
        self._last_percent = 0
        if ref:
            self._queue("start")

    def onPlayBackPaused(self):
        self._queue("pause")

    def onPlayBackResumed(self):
        self._queue("start")

    def onPlayBackEnded(self):
        self._queue("stop", 100)  # played to the natural end
        self._current = None
        self._last_percent = 0

    def onPlayBackStopped(self):
        # The player may already be torn down here, so trust the % sampled during
        # playback rather than reading getTime() now.
        self._queue("stop", self._last_percent)
        self._current = None
        self._last_percent = 0

    # ---- driven by the service loop (off the callback thread) --------------
    def sample_progress(self):
        if self._current and self.isPlaying():
            p = _percent(self)
            if p is not None:
                self._last_percent = p

    def drain(self):
        while self.events:
            payload = self.events.popleft()
            ok = scrobble_queue.enqueue(payload)  # sends now; persists on failure
            if (
                ok
                and payload.get("action") == "stop"
                and (payload.get("progress") or 0) >= NOTIFY_AT
            ):
                _notify("Logged %s" % payload.get("title", "this title"))
