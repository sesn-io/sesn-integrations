"""Browse Sesn inside Kodi (xbmc.python.pluginsource).

Root → Watchlist / Up Next / My Lists as folders of titles. An item already on
THIS box is marked ▶ and is playable straight from the local library; an item
NOT on the box is shown for reference only and is NOT playable — Sesn never
fetches what you don't have (the piracy line: show a gap, never fill it).

⚠ Untested in a live Kodi against the running API — the item field names follow
the /api/v1/up-next + /lists/[id] shapes but should be verified on a real box.
"""
import os
import sys
from urllib.parse import urlencode, parse_qsl  # Kodi 19+ is Python 3

sys.path.insert(0, os.path.dirname(__file__))

import xbmcaddon  # noqa: E402
import xbmcgui  # noqa: E402
import xbmcplugin  # noqa: E402

import sesn_api  # noqa: E402
import kodi_library  # noqa: E402

ADDON = xbmcaddon.Addon()

HANDLE = int(sys.argv[1])
BASE = sys.argv[0]


def _url(**kwargs):
    return BASE + "?" + urlencode(kwargs)


def _enrich_ids(items):
    """Up Next / list items carry tmdb_id + type; pull imdb/tvdb from /metadata
    in ONE call so the library match can key on whatever id Kodi scraped."""
    refs = ["%s:%s" % (i.get("type"), i.get("tmdb_id")) for i in items if i.get("tmdb_id")]
    if not refs:
        return items
    data = sesn_api.get_json("/api/v1/metadata?ids=" + ",".join(refs[:50])) or {}
    by_id = {"%s:%s" % (m.get("type"), m.get("tmdb_id")): m for m in (data.get("items") or [])}
    for it in items:
        m = by_id.get("%s:%s" % (it.get("type"), it.get("tmdb_id")))
        if m:
            it["imdb_id"] = m.get("imdb_id")
            it["tvdb_id"] = m.get("tvdb_id")
    return items


def _render(items):
    items = _enrich_ids(list(items or []))
    avail = {(a.get("type"), a.get("tmdb_id")): a for a in kodi_library.available(items)}
    for it in items:
        name = it.get("name") or "Untitled"
        a = avail.get((it.get("type"), it.get("tmdb_id")))
        li = xbmcgui.ListItem(label=("▶ " + name) if a else name)
        poster = it.get("poster")
        if poster:
            li.setArt({"poster": poster, "thumb": poster})
        if a:
            li.setProperty("IsPlayable", "true")
            if a.get("kodi_movieid"):
                url = _url(action="play", movieid=a["kodi_movieid"])
            else:
                url = _url(action="play", episodeid=a["kodi_episodeid"])
            xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=False)
        else:
            # Not on this box — informational only, deliberately not playable.
            xbmcplugin.addDirectoryItem(HANDLE, _url(action="noop"), li, isFolder=False)
    xbmcplugin.setContent(HANDLE, "videos")
    xbmcplugin.endOfDirectory(HANDLE)


def _play(params):
    if params.get("movieid"):
        d = kodi_library.rpc(
            "VideoLibrary.GetMovieDetails",
            {"movieid": int(params["movieid"]), "properties": ["file"]},
        ).get("moviedetails", {})
    else:
        d = kodi_library.rpc(
            "VideoLibrary.GetEpisodeDetails",
            {"episodeid": int(params["episodeid"]), "properties": ["file"]},
        ).get("episodedetails", {})
    path = d.get("file")
    xbmcplugin.setResolvedUrl(HANDLE, bool(path), xbmcgui.ListItem(path=path or ""))


def _section(section):
    data = sesn_api.get_json("/api/v1/up-next") or {}
    _render((data.get("sections") or {}).get(section) or [])


def _lists():
    data = sesn_api.get_json("/api/v1/lists") or {}
    for l in data.get("lists") or []:
        li = xbmcgui.ListItem(label=l.get("name") or "List")
        xbmcplugin.addDirectoryItem(HANDLE, _url(action="list", id=l.get("id")), li, isFolder=True)
    xbmcplugin.endOfDirectory(HANDLE)


def _list(params):
    data = sesn_api.get_json("/api/v1/lists/%s" % params.get("id")) or {}
    _render(data.get("items") or [])


def _settings():
    """Open the addon's own settings dialog — where the API key is entered."""
    ADDON.openSettings()
    xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


def _disconnect():
    """Forget the key on this box. (The key stays valid server-side — revoke it
    fully on sesn.io/api-keys if you want.)"""
    ADDON.setSettingString("api_key", "")
    xbmcgui.Dialog().notification("Sesn", "Disconnected from this device", xbmcgui.NOTIFICATION_INFO)
    xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


def _root():
    key = (ADDON.getSettingString("api_key") or "").strip()
    if not key:
        # Not connected yet — lead with pairing (enter a short code on your phone,
        # nothing typed on the remote); manual key-paste is the fallback.
        pair_li = xbmcgui.ListItem(label="Pair with a code (recommended)")
        pair_li.setArt({"icon": "DefaultAddonService.png"})
        xbmcplugin.addDirectoryItem(HANDLE, _url(action="pair"), pair_li, isFolder=False)
        manual = xbmcgui.ListItem(label="Enter a key manually")
        manual.setArt({"icon": "DefaultAddonService.png"})
        xbmcplugin.addDirectoryItem(HANDLE, _url(action="settings"), manual, isFolder=False)
        xbmcplugin.endOfDirectory(HANDLE)
        return
    for label, action in (("Watchlist", "watchlist"), ("Up Next", "upnext"), ("My Lists", "lists")):
        li = xbmcgui.ListItem(label=label)
        xbmcplugin.addDirectoryItem(HANDLE, _url(action=action), li, isFolder=True)
    li = xbmcgui.ListItem(label="Settings")
    li.setArt({"icon": "DefaultAddonService.png"})
    xbmcplugin.addDirectoryItem(HANDLE, _url(action="settings"), li, isFolder=False)
    xbmcplugin.endOfDirectory(HANDLE)


def main():
    params = dict(parse_qsl(sys.argv[2][1:])) if len(sys.argv) > 2 else {}
    action = params.get("action")
    if action == "play":
        _play(params)
    elif action == "watchlist":
        _section("planned")
    elif action == "upnext":
        _section("ready")
    elif action == "lists":
        _lists()
    elif action == "list":
        _list(params)
    elif action == "settings":
        _settings()
    elif action == "disconnect":
        _disconnect()
    elif action == "pair":
        import pair  # lazy — only loaded when pairing is triggered

        pair.run()
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
    elif action == "noop":
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
    else:
        _root()


if __name__ == "__main__":
    main()
