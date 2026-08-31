"""Two-way sync — the DOWN half: stamp Sesn's watched state + ratings into THIS
box's Kodi library. OFF by default (it writes to your library — opt-in).

Pulls watched plays from GET /api/v1/sync/watched keyed on a persisted logs.id
cursor, matches each to a local item via kodi_library, and sets playcount /
lastplayed / userrating over local JSON-RPC.

⚠ Only ever ADDS watched state. A local item already watched (playcount > 0) is
NOT reset — that would clobber a rewatch count you built on the box. Un-watch is
never synced. A `full=True` pass re-scans from 0 as a reconcile (drift repair),
and setting playcount=1 on an already-1 item is a harmless no-op we skip anyway.
"""
import json
import time

import xbmc
import xbmcaddon
import xbmcvfs

import sesn_api
import kodi_library

ADDON = xbmcaddon.Addon()
PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
CURSOR_FILE = PROFILE + "sync_cursor.json"
PAGE_LIMIT = 200


def _log(msg):
    xbmc.log("[script.sesn] " + msg, xbmc.LOGINFO)


def _read_cursor():
    try:
        f = xbmcvfs.File(CURSOR_FILE)
        raw = f.read()
        f.close()
        return int(json.loads(raw).get("cursor", 0)) if raw else 0
    except Exception:
        return 0


def _write_cursor(c):
    try:
        if not xbmcvfs.exists(PROFILE):
            xbmcvfs.mkdirs(PROFILE)
        f = xbmcvfs.File(CURSOR_FILE, "w")
        f.write(json.dumps({"cursor": c}))
        f.close()
    except Exception as err:
        _log("cursor write failed: %s" % err)


def _lastplayed(watched_at):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(watched_at)) if watched_at else None


def _stamp(kind, libid, watched_at, rating):
    """kind = 'movie' | 'episode'. Set watched (only if not already) + rating."""
    get_m = "VideoLibrary.GetMovieDetails" if kind == "movie" else "VideoLibrary.GetEpisodeDetails"
    set_m = "VideoLibrary.SetMovieDetails" if kind == "movie" else "VideoLibrary.SetEpisodeDetails"
    id_key = "movieid" if kind == "movie" else "episodeid"
    detail_key = "moviedetails" if kind == "movie" else "episodedetails"

    cur = kodi_library.rpc(get_m, {id_key: libid, "properties": ["playcount"]}).get(detail_key, {})
    already_watched = (cur.get("playcount") or 0) > 0

    params = {id_key: libid}
    if not already_watched:
        params["playcount"] = 1
        lp = _lastplayed(watched_at)
        if lp:
            params["lastplayed"] = lp
    if rating is not None:
        try:
            params["userrating"] = int(round(float(rating)))
        except (TypeError, ValueError):
            pass
    if len(params) == 1:  # nothing to change (already watched, no rating)
        return False
    kodi_library.rpc(set_m, params)
    return True


def run(full=False):
    """One down-sync pass. full=True reconciles from cursor 0 (drift repair)."""
    if not ADDON.getSettingBool("sync_down"):
        return
    cursor = 0 if full else _read_cursor()
    movies = kodi_library.movie_index()
    shows = kodi_library.show_index()
    stamped = 0
    while True:
        data = sesn_api.get_json("/api/v1/sync/watched?since=%d&limit=%d" % (cursor, PAGE_LIMIT))
        if not data or not data.get("ok"):
            break
        for it in data.get("items") or []:
            ids = {"imdb": it.get("imdb_id"), "tmdb": it.get("tmdb_id"), "tvdb": it.get("tvdb_id")}
            if it.get("type") == "episode":
                eid = kodi_library.find_episode(ids, it.get("season"), it.get("episode"), index=shows)
                if eid is not None and _stamp("episode", eid, it.get("watched_at"), it.get("rating")):
                    stamped += 1
            else:
                mid = kodi_library.find_movie(ids, index=movies)
                if mid is not None and _stamp("movie", mid, it.get("watched_at"), it.get("rating")):
                    stamped += 1
        cursor = data.get("cursor", cursor)
        if not full:
            _write_cursor(cursor)
        if not data.get("has_more"):
            break
    if full:
        _write_cursor(cursor)  # deltas resume from where the reconcile reached
    # Loud-ish: report what the pass changed so drift repair is visible in the log.
    _log("down-sync pass done (%s), stamped %d item(s)" % ("full" if full else "delta", stamped))
