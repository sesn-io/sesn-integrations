"""Thin Sesn scrobble client.

Posts to POST {server}/api/v1/scrobble, the same endpoint Plex/Jellyfin use. It
speaks the Trakt-style protocol (action start|pause|stop + progress); the SERVER
decides completion at 80%, so the addon never holds a threshold. Sends only ids +
title/year/S-E + position + the user's key — never the library or file paths.

The key is only ever sent to an https:// Sesn address (plain http is allowed for
localhost testing). If Sesn answers 401 the key is remembered as invalid (by a
fingerprint, never the key itself) and nothing more is sent with it until the
device is paired again.
"""
import email.utils
import hashlib
import json
import time
from urllib.parse import urlsplit

import xbmc
import xbmcaddon

try:
    import requests  # script.module.requests
except ImportError:  # pragma: no cover - only if the dependency is missing
    requests = None

ADDON = xbmcaddon.Addon()
TIMEOUT = 15
DEFAULT_SERVER = "https://sesn.io"
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
MAX_RETRY_AFTER = 6 * 3600

# send_result() outcomes.
OK = "ok"  # Sesn acknowledged the event
RETRY = "retry"  # network/server trouble, 408 or 429: keep and back off
DROP = "drop"  # any other 4xx: the event itself is unacceptable, discard it
UNAUTHORIZED = "unauthorized"  # 401 (or a key already known to be invalid)
NO_KEY = "no_key"  # not paired; no new capture or unverified replay
BLOCKED = "blocked"  # the configured server address is not allowed

_warned_host = None


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log("[script.sesn] " + msg, level)


def _debug(msg):
    if ADDON.getSettingBool("debug"):
        _log(msg, xbmc.LOGINFO)


def _setting(name):
    try:
        return ADDON.getSettingString(name) or ""
    except Exception:
        return ""


def server_base():
    """The configured Sesn origin, or None if it is not safe to send a key to.

    Requires https://, except http:// to localhost / 127.0.0.1 for testing.
    Logs a warning (once per host) when the host is not sesn.io.
    """
    global _warned_host
    raw = (_setting("server_url").strip() or DEFAULT_SERVER).rstrip("/")
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
    except ValueError:
        parts, host = None, ""
    if not parts or not host:
        _log("server address %r is not a valid URL; nothing will be sent" % raw, xbmc.LOGERROR)
        return None
    scheme = parts.scheme.lower()
    if not (scheme == "https" or (scheme == "http" and host in LOOPBACK_HOSTS)):
        _log(
            "server address must start with https:// (http only for localhost); nothing will be sent to %s" % host,
            xbmc.LOGERROR,
        )
        return None
    if host != "sesn.io" and not host.endswith(".sesn.io") and _warned_host != host:
        _warned_host = host
        _log("server address points at %s, not sesn.io; your Sesn key is sent there" % host, xbmc.LOGWARNING)
    return raw


def _key():
    return _setting("api_key").strip()


def _fingerprint(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def key_fingerprint():
    """Short hash of the stored key ("" if none) — lets callers notice a re-pair."""
    key = _key()
    return _fingerprint(key) if key else ""


def transport_fingerprint():
    """Opaque binding to this exact credential AND destination; never the key."""
    key, base = _key(), server_base()
    return hashlib.sha256(json.dumps([base, key]).encode("utf-8")).hexdigest() if key and base else ""


def is_configured():
    return bool(_key())


def key_invalid():
    """True when Sesn has rejected the key currently stored (401)."""
    key = _key()
    return bool(key) and _setting("invalid_key") == _fingerprint(key)


def mark_key_invalid(expected_key=None):
    """Remember (by fingerprint) that Sesn rejected this key and ask to pair again.
    A newly paired or pasted key has a different fingerprint, which clears it."""
    key = _key()
    if not key or key_invalid() or (expected_key is not None and expected_key != key):
        return
    try:
        ADDON.setSettingString("invalid_key", _fingerprint(key))
    except Exception:
        pass
    _log("Sesn rejected this device's key (HTTP 401); sending is paused until you pair again", xbmc.LOGWARNING)
    try:
        import xbmcgui

        xbmcgui.Dialog().notification(
            "Sesn", "This device is no longer connected. Open Sesn and pair again.", xbmcgui.NOTIFICATION_WARNING, 8000
        )
    except Exception:
        pass


def retry_after_seconds(resp):
    """Parse a Retry-After header (seconds or HTTP date). None if absent/invalid."""
    try:
        headers = getattr(resp, "headers", None) or {}
        value = headers.get("Retry-After")
    except Exception:
        return None
    if not value:
        return None
    value = str(value).strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
            seconds = when.timestamp() - time.time()
        except Exception:
            return None
    return max(0, min(MAX_RETRY_AFTER, int(seconds)))


def classify_status(status):
    """Map an HTTP status to a send outcome (2xx is decided by the body)."""
    if status == 401:
        return UNAUTHORIZED
    if status in (408, 429) or status >= 500:
        return RETRY
    if 400 <= status < 500:
        return DROP
    return RETRY


def send_result(payload, expected_transport=None, durable=False):
    """POST one event and return (outcome, retry_after_seconds_or_None).

    Never raises. The API key rides the X-Api-Key header, never a query string or
    a log line.
    """
    if requests is None:
        _log("script.module.requests missing — cannot scrobble", xbmc.LOGERROR)
        return RETRY, None
    key = _key()
    if not key:
        _debug("no api key set; holding scrobble")
        return NO_KEY, None
    if key_invalid():
        return UNAUTHORIZED, None
    base = server_base()
    if base is None:
        return BLOCKED, None
    if expected_transport is not None and hashlib.sha256(json.dumps([base, key]).encode("utf-8")).hexdigest() != expected_transport:
        return BLOCKED, None
    try:
        resp = requests.post(
            base + ("/api/v1/scrobble/events" if durable else "/api/v1/scrobble"),
            headers={"X-Api-Key": key, "Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=TIMEOUT,
            allow_redirects=False,  # never forward the key to another address
        )
        status = resp.status_code
        if 200 <= status < 300:
            body = resp.json()
            outcome = OK if isinstance(body, dict) and body.get("ok") is True else RETRY
            # Older servers acknowledge the request with HTTP 200 while refusing
            # delivery at their hourly quota. That is not a durable acceptance.
            if isinstance(body, dict) and body.get("ignored") == "rate_limited":
                return RETRY, retry_after_seconds(resp) or 3600
            if durable:
                outcome = OK if (isinstance(body, dict) and body.get("ok") is True
                    and body.get("protocol_version") == 1 and body.get("durable") is True
                    and isinstance(payload.get("event_id"), str) and bool(payload.get("event_id"))
                    and body.get("event_id") == payload.get("event_id")
                    and body.get("state") in ("queued", "applied")) else RETRY
        else:
            outcome = classify_status(status)
        # Log bounded event metadata and acceptance, never raw response bodies.
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
                status,
                "accepted" if outcome == OK else outcome,
            )
        )
        if outcome == UNAUTHORIZED:
            mark_key_invalid(key)
        elif outcome != OK:
            _log("scrobble failed: HTTP %s (%s)" % (status, outcome), xbmc.LOGWARNING)
        return outcome, retry_after_seconds(resp)
    except Exception as err:  # network down, DNS, timeout, bad JSON — queue and retry
        _debug("scrobble error: %s" % type(err).__name__)
        return RETRY, None


def send(payload):
    """POST one event. True means Sesn accepted it, not necessarily saved it."""
    return send_result(payload)[0] == OK


def post_json_ex(path, payload):
    """POST JSON to {server}{path} WITHOUT the API key — used by device pairing,
    which authenticates via the device_code in the exchange itself.
    Returns (status, parsed JSON or None, retry_after); status 0 = not sent."""
    if requests is None:
        return 0, None, None
    base = server_base()
    if base is None:
        return 0, None, None
    try:
        resp = requests.post(
            base + path,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=TIMEOUT,
        )
        body = None
        try:
            body = resp.json()
        except Exception:
            pass
        if not 200 <= resp.status_code < 300:
            _log("POST %s -> %s" % (path, resp.status_code), xbmc.LOGWARNING)
        return resp.status_code, body, retry_after_seconds(resp)
    except Exception as err:
        _debug("POST %s error: %s" % (path, type(err).__name__))
    return 0, None, None


def post_json(path, payload):
    """Like post_json_ex but returns parsed JSON on 2xx, else None."""
    status, body, _ = post_json_ex(path, payload)
    return body if 200 <= status < 300 else None


def revoke_key():
    """Ask sesn.io to revoke the key this device holds (POST /api/v1/link/revoke,
    authenticated by the key itself). Returns True if Sesn confirmed it, False
    if it could not be reached — the caller still forgets the key locally."""
    if requests is None:
        return False
    key = _key()
    if not key:
        return False
    base = server_base()
    if base is None:
        return False
    try:
        resp = requests.post(base + "/api/v1/link/revoke", headers={"X-Api-Key": key}, timeout=TIMEOUT, allow_redirects=False)
        # 401 means the key was already revoked or never valid: nothing left to cut.
        if 200 <= resp.status_code < 300 or resp.status_code == 401:
            return True
        _log("POST /api/v1/link/revoke -> %s" % resp.status_code, xbmc.LOGWARNING)
    except Exception as err:
        _debug("revoke error: %s" % type(err).__name__)
    return False


def get_json(path, expected_transport=None):
    """GET {server}{path} with the API key. Returns parsed JSON, or None on any
    failure. Used by the browse plugin to read Up Next / lists / metadata."""
    if requests is None:
        return None
    key = _key()
    if not key or key_invalid():
        return None
    base = server_base()
    if base is None:
        return None
    if expected_transport is not None and hashlib.sha256(json.dumps([base, key]).encode("utf-8")).hexdigest() != expected_transport:
        return None
    try:
        resp = requests.get(base + path, headers={"X-Api-Key": key}, timeout=TIMEOUT, allow_redirects=False)
        if 200 <= resp.status_code < 300:
            return resp.json()
        if resp.status_code == 401:
            mark_key_invalid(key)
        _log("GET %s -> %s" % (path, resp.status_code), xbmc.LOGWARNING)
    except Exception as err:
        _debug("GET %s error: %s" % (path, type(err).__name__))
    return None


def register_account(expected_transport):
    """Register Kodi using its existing key; return a server-verified identity.

    Servers predating Kodi registration return no identity. Never guess an
    account from a key or silently adopt an old queue in that case.
    """
    key, base = _key(), server_base()
    if requests is None or not key or not base or key_invalid():
        return None
    if hashlib.sha256(json.dumps([base, key]).encode("utf-8")).hexdigest() != expected_transport:
        return None
    try:
        resp = requests.post(base + "/api/v1/link/register",
            headers={"X-Api-Key": key, "Content-Type": "application/json"},
            data=json.dumps({"provider": "kodi"}), timeout=TIMEOUT, allow_redirects=False)
        if resp.status_code == 401:
            mark_key_invalid(key)
        return resp.json() if 200 <= resp.status_code < 300 else None
    except Exception:
        return None
