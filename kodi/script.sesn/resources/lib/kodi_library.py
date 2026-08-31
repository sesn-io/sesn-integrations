"""Local Kodi library access via JSON-RPC (in-process, localhost — no ports).

Shared by every Sesn feature that has to look at THIS box's own library:
- playable-now (is a Sesn Up Next item already on this box?)
- two-way sync (stamp playcount / lastplayed) — later
- send-to-TV / cast (resolve a command to a local item, then Player.Open) — later

⚠ Two traps solved ONCE here so callers never re-hit them:
  1. Kodi's uniqueid values are STRINGS ('27205'); Sesn ids are often NUMBERS
     (27205). Every comparison coerces BOTH sides to str, or nothing ever matches.
  2. An episode is resolved by its SHOW's id + season + episode, NEVER by the
     episode's own uniqueid (that is not the series id).
"""
import json

import xbmc


def rpc(method, params=None):
    """One JSON-RPC call to the local Kodi. Returns the `result` dict, or {}."""
    req = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}
    try:
        raw = xbmc.executeJSONRPC(json.dumps(req))
        return (json.loads(raw) or {}).get('result', {}) or {}
    except Exception as err:
        xbmc.log('[script.sesn] jsonrpc %s failed: %s' % (method, err), xbmc.LOGWARNING)
        return {}


def _uid_pairs(uniqueid):
    """Yield ('imdb', 'tt..'), ('tmdb', '27205'), … — value coerced to str."""
    if not isinstance(uniqueid, dict):
        return
    for src in ('imdb', 'tmdb', 'tvdb'):
        v = uniqueid.get(src)
        if v not in (None, ''):
            yield src, str(v)


def _index(rows, id_field):
    """Build { 'imdb:tt..': libid, 'tmdb:27205': libid, … } from library rows."""
    index = {}
    for r in rows or []:
        libid = r.get(id_field)
        for src, val in _uid_pairs(r.get('uniqueid')):
            index['%s:%s' % (src, val)] = libid
    return index


def movie_index():
    res = rpc('VideoLibrary.GetMovies', {'properties': ['uniqueid']})
    return _index(res.get('movies'), 'movieid')


def show_index():
    res = rpc('VideoLibrary.GetTVShows', {'properties': ['uniqueid']})
    return _index(res.get('tvshows'), 'tvshowid')


def _match(index, ids):
    """ids like {'imdb':'tt..','tmdb':27205,'tvdb':81189} → library id, or None.
    Coerces every id value to str before the lookup (trap #1)."""
    for src in ('imdb', 'tmdb', 'tvdb'):
        v = ids.get(src)
        if v in (None, ''):
            continue
        hit = index.get('%s:%s' % (src, str(v)))
        if hit is not None:
            return hit
    return None


def find_movie(ids, index=None):
    return _match(movie_index() if index is None else index, ids)


def find_episode(show_ids, season, episode, index=None):
    """Resolve an episode by its SHOW's ids + season/episode → episodeid (trap #2)."""
    tvshowid = _match(show_index() if index is None else index, show_ids)
    if tvshowid is None:
        return None
    try:
        season_i, episode_i = int(season), int(episode)
    except (TypeError, ValueError):
        return None
    res = rpc('VideoLibrary.GetEpisodes', {
        'tvshowid': tvshowid,
        'season': season_i,
        'properties': ['episode'],
    })
    for ep in res.get('episodes', []) or []:
        if ep.get('episode') == episode_i:
            return ep.get('episodeid')
    return None


def available(items):
    """Given Sesn items (each a dict with type + imdb_id/tmdb_id/tvdb_id and, for
    episodes, season/episode), return the subset present on THIS box, each tagged
    with its local `kodi_movieid` / `kodi_episodeid`. Builds each index at most once.
    """
    movies = shows = None
    out = []
    for it in items:
        ids = {'imdb': it.get('imdb_id'), 'tmdb': it.get('tmdb_id'), 'tvdb': it.get('tvdb_id')}
        if it.get('type') == 'episode' and it.get('season') is not None:
            if shows is None:
                shows = show_index()
            lid = find_episode(ids, it.get('season'), it.get('episode'), index=shows)
            if lid is not None:
                out.append(dict(it, kodi_episodeid=lid))
        else:
            if movies is None:
                movies = movie_index()
            lid = find_movie(ids, index=movies)
            if lid is not None:
                out.append(dict(it, kodi_movieid=lid))
    return out
