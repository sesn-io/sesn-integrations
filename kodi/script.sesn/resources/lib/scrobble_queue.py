"""Offline-tolerant scrobble sender.

Each event is attempted immediately; anything that fails for a temporary reason
(no network, server down, 408/429) is appended to a small JSON file under the
addon's profile dir and retried later. Bounded so a long outage can't grow it
without limit. This mirrors the server side, whose own queue absorbs
premiere-night floods.

Retry rules:
- other 4xx answers drop the event (resending an unacceptable event never helps);
- 401 stops all sending until the device is paired again (sesn_api flags the key);
- temporary failures back off exponentially with jitter, capped at 30 minutes,
  and honour a Retry-After header;
- queued events older than 7 days are discarded.
"""
import json
import random
import time

import xbmc
import xbmcaddon
import xbmcvfs

import sesn_api

ADDON = xbmcaddon.Addon()
PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
QUEUE_FILE = PROFILE + "queue.json"
MAX_QUEUED = 500  # hard cap; drop the oldest beyond this
MAX_AGE = 7 * 24 * 3600  # queued events expire after a week
BASE_DELAY = 10  # first retry after ~10 s
MAX_DELAY = 30 * 60  # backoff cap

# In-memory backoff state (the service is one long-lived process).
_failures = 0
_next_attempt = 0.0
_backoff_key = None  # fingerprint of the key the backoff applies to


def _log(msg):
    xbmc.log("[script.sesn] " + msg, xbmc.LOGINFO)


def _ensure_profile():
    if not xbmcvfs.exists(PROFILE):
        xbmcvfs.mkdirs(PROFILE)


def _wrap(payload, now):
    return {"payload": payload, "queued_at": now}


def _read(now=None):
    """Queued entries as {"payload", "queued_at"}. Pre-0.2.11 files held bare
    payloads; those are treated as queued now so they still expire in time."""
    now = time.time() if now is None else now
    try:
        f = xbmcvfs.File(QUEUE_FILE)
        raw = f.read()
        f.close()
        items = json.loads(raw) if raw else []
    except Exception:
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and isinstance(item.get("payload"), dict):
            out.append({"payload": item["payload"], "queued_at": float(item.get("queued_at") or now)})
        elif isinstance(item, dict):
            out.append(_wrap(item, now))
    return out


def _write(items):
    _ensure_profile()
    try:
        f = xbmcvfs.File(QUEUE_FILE, "w")
        f.write(json.dumps(items))
        f.close()
    except Exception as err:
        _log("queue write failed: %s" % err)


def _persist(items, entry):
    items.append(entry)
    if len(items) > MAX_QUEUED:
        items = items[-MAX_QUEUED:]
    _write(items)


def backoff_delay(failures, retry_after=None, rand=random.random):
    """Exponential backoff with jitter: a random delay in [50%, 100%] of
    BASE_DELAY * 2^(failures-1), capped at MAX_DELAY; never less than Retry-After."""
    exp = min(MAX_DELAY, BASE_DELAY * (2 ** max(0, failures - 1)))
    delay = exp * (0.5 + 0.5 * rand())
    if retry_after is not None:
        delay = max(delay, retry_after)
    return delay


def _note_failure(retry_after, now):
    global _failures, _next_attempt, _backoff_key
    _backoff_key = sesn_api.key_fingerprint()
    _failures += 1
    delay = backoff_delay(_failures, retry_after)
    _next_attempt = now + delay
    _log("scrobble not delivered; retrying queued scrobbles in %d s" % int(delay))


def _note_success():
    global _failures, _next_attempt
    _failures = 0
    _next_attempt = 0.0


def _paused(now):
    """True while sending must wait: backing off, unpaired or key rejected.
    A new key (re-pair) ends any backoff left over from the old one."""
    if _next_attempt and sesn_api.key_fingerprint() != _backoff_key:
        _note_success()
    return now < _next_attempt or not sesn_api.is_configured() or sesn_api.key_invalid()


def enqueue(payload, now=None):
    """Send now; on a temporary failure, persist for retry. Returns True if Sesn
    accepted it. While backing off (or unpaired) the event is queued unsent."""
    now = time.time() if now is None else now
    items = _read(now)
    if items or _paused(now):
        # Keep order behind anything already waiting; flush() sends it.
        _persist(items, _wrap(payload, now))
        return False
    outcome, retry_after = sesn_api.send_result(payload)
    if outcome == sesn_api.OK:
        _note_success()
        return True
    if outcome == sesn_api.DROP:
        _log("Sesn refused a %s event; not retrying it" % payload.get("action"))
        return False
    # Temporary failure, 401 (key now flagged), blocked address or no key:
    # keep it and back off so nothing is retried every tick.
    _note_failure(retry_after, now)
    _persist(items, _wrap(payload, now))
    return False


def flush(now=None):
    """Retry persisted events oldest-first. Stops at the first temporary failure
    (and backs off) so a still-down server isn't hammered; keeps the rest."""
    now = time.time() if now is None else now
    items = _read(now)
    if not items:
        return
    fresh = [item for item in items if now - item["queued_at"] <= MAX_AGE]
    expired = len(items) - len(fresh)
    if expired:
        _log("discarded %d queued scrobble(s) older than 7 days" % expired)
    if _paused(now):
        if expired:
            _write(fresh)
        return
    remaining = []
    sent = dropped = 0
    stopped = False
    for item in fresh:
        if stopped:
            remaining.append(item)
            continue
        outcome, retry_after = sesn_api.send_result(item["payload"])
        if outcome == sesn_api.OK:
            sent += 1
            _note_success()
        elif outcome == sesn_api.DROP:
            dropped += 1
        else:
            _note_failure(retry_after, now)
            stopped = True
            remaining.append(item)
    if sent or dropped:
        _log("flushed %d queued scrobble(s), dropped %d, %d remaining" % (sent, dropped, len(remaining)))
    if len(remaining) != len(items):
        _write(remaining)
