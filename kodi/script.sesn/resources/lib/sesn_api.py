"""Thin Sesn scrobble client.

Posts to POST {server}/api/v1/scrobble, the same endpoint Plex/Jellyfin use. It
speaks the Trakt-style protocol (action start|pause|stop + progress); the SERVER
decides completion at 80%, so the addon never holds a threshold. Sends only ids +
title/year/S-E + position + the user's key — never the library or file paths.
"""
import json

import xbmc
import xbmcaddon

try:
    import requests  # script.module.requests
except ImportError:  # pragma: no cover - only if the dependency is missing
    requests = None

ADDON = xbmcaddon.Addon()
TIMEOUT = 15


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log("[script.sesn] " + msg, level)


def _debug(msg):
    if ADDON.getSettingBool("debug"):
        _log(msg, xbmc.LOGINFO)


def _endpoint():
    base = (ADDON.getSettingString("server_url") or "https://sesn.io").rstrip("/")
    return base + "/api/v1/scrobble"


def is_configured():
    return bool((ADDON.getSettingString("api_key") or "").strip())


def send(payload):
    """POST one scrobble event. Returns True on a 2xx, False otherwise.

    Never raises — the caller (queue) treats False as "retry later". The API key
    rides the X-Api-Key header, never a query string or a log line.
    """
    if requests is None:
        _log("script.module.requests missing — cannot scrobble", xbmc.LOGERROR)
        return False
    key = (ADDON.getSettingString("api_key") or "").strip()
    if not key:
        _debug("no api key set; dropping scrobble")
        return False
    try:
        resp = requests.post(
            _endpoint(),
            headers={"X-Api-Key": key, "Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=TIMEOUT,
        )
        ok = 200 <= resp.status_code < 300
        # Log the whole payload (minus the key) + the server's reply, so a "did it
        # match?" is answerable from the log instead of guessed.
        _debug(
            "scrobble %s type=%s title=%r year=%s tmdb=%s imdb=%s tvdb=%s S%sE%s progress=%s -> %s %s"
            % (
                payload.get("action"),
                payload.get("type"),
                payload.get("title"),
                payload.get("year"),
                payload.get("tmdb_id"),
                payload.get("imdb_id"),
                payload.get("tvdb_id"),
                payload.get("season"),
                payload.get("episode"),
                payload.get("progress"),
                resp.status_code,
                (resp.text or "")[:200],
            )
        )
        if not ok:
            _log("scrobble failed: HTTP %s" % resp.status_code, xbmc.LOGWARNING)
        return ok
    except Exception as err:  # network down, DNS, timeout — queue and retry
        _debug("scrobble error: %s" % err)
        return False


def post_json(path, payload):
    """POST JSON to {server}{path} WITHOUT the API key — used by device pairing,
    which authenticates via the device_code in the exchange itself. Returns
    parsed JSON, or None on failure."""
    if requests is None:
        return None
    base = (ADDON.getSettingString("server_url") or "https://sesn.io").rstrip("/")
    try:
        resp = requests.post(
            base + path,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=TIMEOUT,
        )
        if 200 <= resp.status_code < 300:
            return resp.json()
        _log("POST %s -> %s" % (path, resp.status_code), xbmc.LOGWARNING)
    except Exception as err:
        _debug("POST %s error: %s" % (path, err))
    return None


def get_json(path):
    """GET {server}{path} with the API key. Returns parsed JSON, or None on any
    failure. Used by the browse plugin to read Up Next / lists / metadata."""
    if requests is None:
        return None
    key = (ADDON.getSettingString("api_key") or "").strip()
    if not key:
        return None
    base = (ADDON.getSettingString("server_url") or "https://sesn.io").rstrip("/")
    try:
        resp = requests.get(base + path, headers={"X-Api-Key": key}, timeout=TIMEOUT)
        if 200 <= resp.status_code < 300:
            return resp.json()
        _log("GET %s -> %s" % (path, resp.status_code), xbmc.LOGWARNING)
    except Exception as err:
        _debug("GET %s error: %s" % (path, err))
    return None
