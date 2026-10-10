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


class RpcError(RuntimeError):
    """A local operation was not acknowledged; callers must not checkpoint it."""


def rpc_checked(method, params=None):
    """Return a confirmed JSON-RPC result or raise, including protocol errors."""
    req = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}
    try:
        raw = xbmc.executeJSONRPC(json.dumps(req))
        response = json.loads(raw)
        if not isinstance(response, dict) or 'error' in response or 'result' not in response:
            raise RpcError('missing result or JSON-RPC error')
        return response['result']
    except Exception as err:
        raise RpcError('jsonrpc %s was not acknowledged' % method) from err


def rpc(method, params=None):
    """Best-effort read for the browse UI; sync uses rpc_checked instead."""
    try:
        return rpc_checked(method, params) or {}
    except RpcError as err:
        xbmc.log('[script.sesn] %s' % err, xbmc.LOGWARNING)
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


def _library_rows(method, params, key, strict, id_field):
    res = (rpc_checked if strict else rpc)(method, params)
    if not strict:
        return res.get(key, []) if isinstance(res, dict) else []
    limits = res.get('limits') if isinstance(res, dict) else None
    if not isinstance(limits, dict) or any(type(limits.get(k)) is not int for k in ('start', 'end', 'total')):
        raise RpcError('jsonrpc %s did not confirm complete library limits' % method)
    start, end, total = limits['start'], limits['end'], limits['total']
    # We requested the entire collection, not a page. Never mistake a truncated
    # or malformed index for evidence that a watched title is absent locally.
    if start != 0 or end != total or total < 0:
        raise RpcError('jsonrpc %s returned a truncated library' % method)
    rows = res.get(key, [])
    if not isinstance(rows, list) or len(rows) != total:
        raise RpcError('jsonrpc %s returned incomplete library data' % method)
    if any(not isinstance(row, dict) or type(row.get(id_field)) is not int or row[id_field] <= 0 for row in rows):
        raise RpcError('jsonrpc %s returned an invalid library ID' % method)
    return rows


def movie_index(strict=False):
    rows = _library_rows('VideoLibrary.GetMovies', {'properties': ['uniqueid']}, 'movies', strict, 'movieid')
    return _index(rows, 'movieid')


def show_index(strict=False):
    rows = _library_rows('VideoLibrary.GetTVShows', {'properties': ['uniqueid']}, 'tvshows', strict, 'tvshowid')
    return _index(rows, 'tvshowid')


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


def find_show(ids, index=None):
    """Resolve a show's ids → its local tvshowid, or None."""
    return _match(show_index() if index is None else index, ids)


def find_episode(show_ids, season, episode, index=None, strict=False):
    """Resolve an episode by its SHOW's ids + season/episode → episodeid (trap #2)."""
    tvshowid = _match(show_index(strict=strict) if index is None else index, show_ids)
    if tvshowid is None:
        return None
    try:
        season_i, episode_i = int(season), int(episode)
    except (TypeError, ValueError):
        return None
    rows = _library_rows('VideoLibrary.GetEpisodes', {
        'tvshowid': tvshowid,
        'season': season_i,
        'properties': ['episode'],
    }, 'episodes', strict, 'episodeid')
    for ep in rows:
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
