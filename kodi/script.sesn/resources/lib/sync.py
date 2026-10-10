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
import time

import xbmc
import xbmcaddon

import sesn_api
import kodi_library
import account
import local_state

ADDON = xbmcaddon.Addon()
FULL_INTERVAL = 7 * 24 * 3600
PAGE_LIMIT = 200
PAGE_PAUSE = 1  # seconds between pages, so a full reconcile is gentle on Sesn
MAX_PAGES = 500  # safety stop for one pass (100k plays)


def _log(msg):
    xbmc.log("[script.sesn] " + msg, xbmc.LOGINFO)


def _state(owner):
    with local_state.database() as db:
        row = db.execute("SELECT cursor,full_cursor,last_full FROM sync_state WHERE owner=?", (owner,)).fetchone()
    return row or (0, None, 0)


def _read_cursor(owner=None):
    owner = owner or account.current()
    return _state(owner)[0] if owner else 0


def _guard(owner, transport):
    if account.current() != owner or sesn_api.transport_fingerprint() != transport:
        raise kodi_library.RpcError("account changed during down-sync")


def _write_cursor(c, owner=None, transport=None):
    owner = owner or account.current()
    if not owner:
        return False
    try:
        with local_state.database() as db:
            if transport is not None:
                _guard(owner, transport)
            db.execute("INSERT INTO sync_state(owner,cursor) VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET cursor=MAX(cursor,excluded.cursor)", (owner, c))
        return True
    except Exception:
        _log("cursor write failed; page will be retried")
        return False


def full_due(now=None):
    owner = account.current()
    if not owner:
        return False
    now = time.time() if now is None else now
    _, pending, last = _state(owner)
    return pending is not None or not last or now - last >= FULL_INTERVAL


def invalidate_library():
    """A finished library scan can make formerly absent watched items match."""
    owner = account.current()
    if owner:
        with local_state.database() as db:
            db.execute("INSERT INTO sync_state(owner,last_full) VALUES(?,0) ON CONFLICT(owner) DO UPDATE SET last_full=0,full_cursor=NULL", (owner,))


def _save_full(owner, cursor, completed=False, transport=None):
    with local_state.database() as db:
        if transport is not None:
            _guard(owner, transport)
        if completed:
            db.execute("""INSERT INTO sync_state(owner,cursor,full_cursor,last_full) VALUES(?,?,NULL,?)
                ON CONFLICT(owner) DO UPDATE SET cursor=MAX(cursor,excluded.cursor),full_cursor=NULL,last_full=excluded.last_full""",
                (owner, cursor, time.time()))
        else:
            db.execute("INSERT INTO sync_state(owner,full_cursor) VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET full_cursor=excluded.full_cursor", (owner, cursor))


def _lastplayed(watched_at):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(watched_at)) if watched_at else None


def _stamp(kind, libid, watched_at, rating, owner, transport):
    """kind = 'movie' | 'episode'. Set watched (only if not already) + rating."""
    get_m = "VideoLibrary.GetMovieDetails" if kind == "movie" else "VideoLibrary.GetEpisodeDetails"
    set_m = "VideoLibrary.SetMovieDetails" if kind == "movie" else "VideoLibrary.SetEpisodeDetails"
    id_key = "movieid" if kind == "movie" else "episodeid"
    detail_key = "moviedetails" if kind == "movie" else "episodedetails"

    result = kodi_library.rpc_checked(get_m, {id_key: libid, "properties": ["playcount"]})
    _guard(owner, transport)
    cur = result.get(detail_key) if isinstance(result, dict) else None
    count = cur.get("playcount") if isinstance(cur, dict) else None
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise kodi_library.RpcError("library read did not return a valid playcount")
    already_watched = count > 0

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
    _guard(owner, transport)
    if kodi_library.rpc_checked(set_m, params) != "OK":
        raise kodi_library.RpcError("library write was not acknowledged")
    _guard(owner, transport)
    return True


def run(full=False):
    """One down-sync pass. full=True reconciles from cursor 0 (drift repair)."""
    if not ADDON.getSettingBool("sync_down"):
        return False
    owner = account.current()
    if not owner:
        return False
    transport = sesn_api.transport_fingerprint()
    cursor = (_state(owner)[1] or 0) if full else _read_cursor(owner)
    try:
        movies = kodi_library.movie_index(strict=True)
        shows = kodi_library.show_index(strict=True)
    except kodi_library.RpcError as err:
        _log("down-sync paused: %s" % err)
        return False
    stamped = 0
    pages = 0
    monitor = xbmc.Monitor()
    completed = False
    while True:
        if account.current() != owner or sesn_api.transport_fingerprint() != transport:
            break
        data = sesn_api.get_json("/api/v1/sync/watched?since=%d&limit=%d" % (cursor, PAGE_LIMIT), expected_transport=transport)
        if not isinstance(data, dict) or data.get("ok") is not True:
            break
        next_cursor = data.get("cursor")
        items = data.get("items")
        if (not isinstance(items, list) or any(not isinstance(it, dict) for it in items)
            or not isinstance(next_cursor, int) or isinstance(next_cursor, bool) or next_cursor < cursor
            or not isinstance(data.get("has_more"), bool) or (items and next_cursor == cursor)):
            _log("down-sync paused: incomplete server page")
            break
        pages += 1
        try:
            for it in items:
                _guard(owner, transport)
                ids = {"imdb": it.get("imdb_id"), "tmdb": it.get("tmdb_id"), "tvdb": it.get("tvdb_id")}
                if it.get("type") == "episode":
                    eid = kodi_library.find_episode(ids, it.get("season"), it.get("episode"), index=shows, strict=True)
                    if eid is not None and _stamp("episode", eid, it.get("watched_at"), it.get("rating"), owner, transport):
                        stamped += 1
                else:
                    mid = kodi_library.find_movie(ids, index=movies)
                    if mid is not None and _stamp("movie", mid, it.get("watched_at"), it.get("rating"), owner, transport):
                        stamped += 1
            _guard(owner, transport)
        except kodi_library.RpcError as err:
            # Retry the entire page; acknowledged writes are idempotent, whereas
            # acknowledging a failed read/write would permanently skip it.
            _log("down-sync paused without consuming page: %s" % err)
            break
        advanced = next_cursor > cursor
        if advanced:
            cursor = next_cursor
            if not full:
                if not _write_cursor(cursor, owner, transport):
                    break
            else:
                try:
                    _save_full(owner, cursor, transport=transport)
                except kodi_library.RpcError:
                    break
        if not data.get("has_more"):
            completed = True
            break
        if not advanced:
            # A server that says "more" without moving the cursor would loop forever.
            _log("down-sync stopped: cursor did not advance past %d" % cursor)
            break
        if pages >= MAX_PAGES:
            _log("down-sync paused after %d pages; continues next pass" % pages)
            break
        if monitor.waitForAbort(PAGE_PAUSE):
            break
    if full and completed:
        # Deltas resume from where the reconcile reached, never behind the saved cursor.
        try:
            _save_full(owner, cursor, completed=True, transport=transport)
        except kodi_library.RpcError:
            completed = False
    # Loud-ish: report what the pass changed so drift repair is visible in the log.
    _log("down-sync pass done (%s), stamped %d item(s)" % ("full" if full else "delta", stamped))
    return completed
