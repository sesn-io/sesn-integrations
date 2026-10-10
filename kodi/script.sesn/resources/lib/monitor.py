"""Playback watcher -> Sesn scrobbles.

Subclass xbmc.Player for the playback callbacks. Fire the start on onAVStarted
(the stream/infotag is ready there, not on onPlayBackStarted), and close on BOTH
onPlayBackEnded and onPlayBackStopped because either can silently fail to fire.
Live/PVR and non-video are excluded.

Callbacks read local metadata and commit a small SQLite outbox record. They do
no network I/O. A separate network worker delivers records, while the service
samples playback independently of slow requests or down-sync.
"""
import threading
import math
import time
import uuid

import xbmc
import xbmcaddon
import xbmcgui

import scrobble_queue
import kodi_library
import account

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
    """Optional numeric provider IDs must fit the strict JSON integer contract."""
    if not isinstance(v, str) or not v.isascii() or not v.isdigit():
        return None
    digits = v.lstrip("0") or "0"
    if len(digits) > 16:
        return None
    value = int(digits)
    return value if 0 < value <= 9007199254740991 else None


def _imdb_id(v):
    return v if v.isascii() and v.startswith("tt") and v[2:].isdigit() else None


def _parent_show(tag):
    """Read the parent series locally; never reuse the playing episode's IDs."""
    try:
        episode_id = tag.getDbId()
        if not isinstance(episode_id, int) or episode_id <= 0:
            return {}
        episode = kodi_library.rpc('VideoLibrary.GetEpisodeDetails', {
            'episodeid': episode_id, 'properties': ['tvshowid'],
        }).get('episodedetails', {})
        show_id = episode.get('tvshowid')
        if not isinstance(show_id, int) or show_id <= 0:
            return {}
        show = kodi_library.rpc('VideoLibrary.GetTVShowDetails', {
            'tvshowid': show_id, 'properties': ['title', 'year', 'uniqueid'],
        }).get('tvshowdetails', {})
        return show if isinstance(show, dict) else {}
    except Exception:
        # Plugin streams may have no library item. Title/season/episode is safer
        # than using IDs belonging to a different level of the catalogue.
        return {}


def _position(player):
    try:
        total, position = float(player.getTotalTime()), float(player.getTime())
        if math.isfinite(total) and total > 0 and math.isfinite(position) and position >= 0:
            return position, total
    except Exception:
        pass
    return None


def _monotonic():
    return time.monotonic()


class SesnPlayer(xbmc.Player):
    def __init__(self):
        super().__init__()
        self._lock = threading.RLock()
        self._current = None  # identity of what's playing, or None
        self._last_percent = 0
        self._owner = None
        self._session = None
        self._sequence = 0
        self._start_pending = False
        self._seek_pending = False
        self._seek_target_seconds = None
        self._seek_updated = None
        self._seek_distance = 0.0
        self._playback_speed = 1.0

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
            show = _parent_show(tag) if mediatype == "episode" else {}
            if show.get('title'):
                ref['title'] = show['title']
            if show.get('year'):
                ref['year'] = show['year']
            ids = show.get('uniqueid') or {}
            if isinstance(ids, dict):
                for kind in ('tmdb', 'tvdb'):
                    value = str(ids.get(kind) or '').strip()
                    if value.isdigit() and int(value) > 0:
                        ref[kind + '_id'] = int(value)
                imdb = str(ids.get('imdb') or '').strip()
                if imdb.startswith('tt') and imdb[2:].isdigit():
                    ref['imdb_id'] = imdb
            return ref

        if not ADDON.getSettingBool("scrobble_movies"):
            return None
        title = tag.getTitle()
        tmdb, tvdb, imdb = _as_id(tmdb), _as_id(tvdb), _imdb_id(imdb)
        # Omit malformed optional IDs; use only the title actually supplied by
        # Kodi, never a fabricated replacement. No valid identity means no event.
        if not ((isinstance(title, str) and title.strip()) or imdb or tmdb or tvdb):
            return None
        ref = {"type": "movie", "title": title}
        year = tag.getYear()
        if year:
            ref["year"] = year
        if tmdb:
            ref["tmdb_id"] = tmdb
        if imdb:
            ref["imdb_id"] = imdb
        if tvdb:
            ref["tvdb_id"] = tvdb
        return ref

    def _queue(self, action, progress=None):
        if not self._current or not self._owner or account.current() != self._owner:
            return
        if self._start_pending and action != "stop":
            return  # new-item transitions cannot be attributed to the old sitting
        payload = dict(self._current)
        payload["action"] = action
        payload["event_id"] = str(uuid.uuid4())
        payload["session_id"] = self._session
        payload["sequence"] = self._sequence
        payload["watched_at"] = int(time.time())
        self._sequence += 1
        if progress is not None:
            payload["progress"] = progress
        # Commit at capture time, never after the first network attempt. The
        # callback performs bounded local storage only, no HTTP.
        scrobble_queue.capture(payload, owner=self._owner)

    # ---- callbacks (Kodi thread — keep light) ------------------------------
    def onPlayBackStarted(self):
        with self._lock:
            self._start_pending = True

    def onAVStarted(self):
        with self._lock:
            owner = account.current()
            ref = self._build_ref() if owner else None
            if self._current and ref == self._current and owner == self._owner and not self._start_pending:
                return  # duplicate metadata-ready callback, same sitting
            if self._current:
                self._queue("stop", self._last_percent)
            self._current, self._owner = ref, owner
            self._last_percent = 0
            self._seek_pending = self._start_pending = False
            self._seek_target_seconds = None
            self._seek_updated = None
            self._seek_distance = 0.0
            self._playback_speed = 1.0
            self._session, self._sequence = str(uuid.uuid4()), 0
            if ref:
                self._queue("start")

    def onPlayBackPaused(self):
        with self._lock:
            self.sample_progress()
            self._advance_seek_clock()
            self._playback_speed = 0.0
            self._queue("pause", self._last_percent)

    def onPlayBackResumed(self):
        with self._lock:
            self._advance_seek_clock()
            self._playback_speed = 1.0
            self.sample_progress()
            self._queue("resume", self._last_percent)

    def onPlayBackEnded(self):
        with self._lock:
            self._queue("stop", 100)  # position-completion policy, not watch duration
            self._current = None
            self._last_percent = 0

    def onPlayBackStopped(self):
        # The player may already be torn down here, so trust the % sampled during
        # playback rather than reading getTime() now.
        with self._lock:
            self.sample_progress()  # use a final valid sample if the player is still alive
            self._queue("stop", self._last_percent)
            self._current = None
            self._last_percent = 0

    def onPlayBackSeek(self, time, seekOffset):
        """Kodi callback arguments are milliseconds; getTotalTime is seconds."""
        with self._lock:
            if self._start_pending:
                return  # the seek belongs to the next item, whose identity is pending
            # Clear the pre-seek sample first. A nonpositive/unknown duration
            # is just as unusable as an exception from the player.
            self._last_percent = 0
            self._seek_pending = True
            self._seek_target_seconds = None
            self._seek_updated = _monotonic()
            self._seek_distance = 0.0
            try:
                target = float(time) / 1000.0
                if math.isfinite(target) and target >= 0:
                    self._seek_target_seconds = target
                total = float(self.getTotalTime())
                if self._current and self._seek_target_seconds is not None and math.isfinite(total) and total > 0:
                    self._last_percent = max(0, min(100, target / total * 100))
            except Exception:
                # A stale pre-seek percentage must never turn a backward seek
                # into a completed watch when playback tears down immediately.
                pass

    def onPlayBackSpeedChanged(self, speed):
        with self._lock:
            try:
                value = float(speed)
                if math.isfinite(value):
                    self._advance_seek_clock()
                    self._playback_speed = max(-32, min(32, value))
            except (TypeError, ValueError):
                pass

    def _advance_seek_clock(self):
        if self._seek_pending and self._seek_updated is not None:
            now = _monotonic()
            self._seek_distance += max(0, now - self._seek_updated) * abs(self._playback_speed)
            self._seek_updated = now

    # ---- driven by the service loop (off the callback thread) --------------
    def sample_progress(self):
        with self._lock:
            if self._start_pending:
                # Kodi may expose the next item's position before its metadata
                # is ready. Keep the previous sitting's final valid sample.
                return
            try:
                playing = self.isPlaying()
            except Exception:
                playing = False
            if self._current and playing:
                sample = _position(self)
                if sample is not None:
                    position, total = sample
                    if self._seek_pending:
                        if self._seek_target_seconds is None or self._seek_updated is None:
                            return
                        self._advance_seek_clock()
                        tolerance = min(0.25, total * 0.01)
                        # The first fresh sample can already be well beyond the
                        # target on a short video or after delayed metadata. Its
                        # distance must be plausible for elapsed playback time;
                        # an immediate stale pre-seek position is not plausible.
                        if abs(position - self._seek_target_seconds) > self._seek_distance + tolerance:
                            return
                    self._seek_pending = False
                    self._last_percent = max(0, min(100, position / total * 100))

    def drain(self):
        """Compatibility no-op: events are already persisted in each callback."""
        return None
