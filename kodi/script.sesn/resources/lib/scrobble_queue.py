"""Offline-tolerant scrobble sender.

Each event is attempted immediately; anything that fails (no network, server
down) is appended to a small JSON file under the addon's profile dir and retried
on the next service tick. Bounded so a long outage can't grow it without limit.
This mirrors the server side, whose own queue absorbs premiere-night floods.
"""
import json

import xbmc
import xbmcaddon
import xbmcvfs

import sesn_api

ADDON = xbmcaddon.Addon()
PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
QUEUE_FILE = PROFILE + "queue.json"
MAX_QUEUED = 500  # hard cap; drop the oldest beyond this


def _log(msg):
    xbmc.log("[script.sesn] " + msg, xbmc.LOGINFO)


def _ensure_profile():
    if not xbmcvfs.exists(PROFILE):
        xbmcvfs.mkdirs(PROFILE)


def _read():
    try:
        f = xbmcvfs.File(QUEUE_FILE)
        raw = f.read()
        f.close()
        return json.loads(raw) if raw else []
    except Exception:
        return []


def _write(items):
    _ensure_profile()
    try:
        f = xbmcvfs.File(QUEUE_FILE, "w")
        f.write(json.dumps(items))
        f.close()
    except Exception as err:
        _log("queue write failed: %s" % err)


def enqueue(payload):
    """Send now; on failure, persist for retry. Returns True if it went through."""
    if sesn_api.send(payload):
        return True
    items = _read()
    items.append(payload)
    if len(items) > MAX_QUEUED:
        items = items[-MAX_QUEUED:]
    _write(items)
    return False


def flush():
    """Retry persisted events oldest-first. Stop at the first failure so a still-
    down server isn't hammered; keep the remainder for next time."""
    items = _read()
    if not items:
        return
    remaining = []
    stopped = False
    sent = 0
    for payload in items:
        if stopped:
            remaining.append(payload)
        elif sesn_api.send(payload):
            sent += 1
        else:
            stopped = True
            remaining.append(payload)
    if sent:
        _log("flushed %d queued scrobble(s), %d remaining" % (sent, len(remaining)))
    if len(remaining) != len(items):
        _write(remaining)
