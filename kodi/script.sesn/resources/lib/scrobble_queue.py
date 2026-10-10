"""Account-bound transactional outbox. Callback capture never performs network I/O.

Legacy queue.json is intentionally left untouched: it has no trustworthy owner.
A new credential must be verified before any capture or replay. Old owners' rows
remain quarantined, including across server changes, until that owner reconnects.
"""
import json
import os
import random
import threading
import time
import uuid

import xbmc
import xbmcaddon
import xbmcgui

import account
import local_state
import sesn_api

MAX_QUEUED = 500
BASE_DELAY = 10
MAX_DELAY = 30 * 60
FLUSH_LIMIT = 20
_lock = threading.RLock()
_failures = 0
_next_attempt = 0.0
_backoff_key = None
_warned = set()


def _log(msg):
    xbmc.log("[script.sesn] " + msg, xbmc.LOGINFO)


def _warn(kind, message):
    _log(message)
    if kind not in _warned:
        _warned.add(kind)
        try:
            xbmcgui.Dialog().notification("Sesn", message, xbmcgui.NOTIFICATION_WARNING, 8000)
        except Exception:
            pass


def warn_legacy_queue():
    path = os.path.join(local_state.PROFILE, "queue.json")
    if os.path.isfile(path) and os.path.getsize(path):
        _warn("legacy", "An older unbound queue was preserved and will not be uploaded. Review it before removal.")


def capture(payload, owner=None, now=None):
    """Commit immutable event data BEFORE returning. False means not captured.

    Overflow/corruption never replaces existing data. A full outbox visibly
    refuses new events instead of silently evicting another recorded watch.
    """
    now = time.time() if now is None else now
    active = account.current()
    owner = active if owner is None else owner
    if not owner or active != owner:
        return False
    frozen = dict(payload)
    # The strict endpoint verifies this hint against the key's registered
    # providers; the hint alone grants nothing. Keep it immutable on retries.
    frozen["provider"] = "kodi"
    frozen.setdefault("event_id", str(uuid.uuid4()))
    frozen.setdefault("watched_at", int(now))
    try:
        with _lock, local_state.database() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM outbox WHERE owner=? AND event_id=?", (owner, frozen["event_id"])).fetchone():
                return True
            if db.execute("SELECT count(*) FROM outbox").fetchone()[0] >= MAX_QUEUED:
                _warn("full", "Local queue is full. Existing watches are safe; new events cannot be saved until it clears.")
                return False
            db.execute("INSERT INTO outbox(owner,event_id,payload,queued_at) VALUES(?,?,?,?)",
                       (owner, frozen["event_id"], json.dumps(frozen, separators=(",", ":")), now))
        return True
    except Exception:
        _warn("storage", "Local queue could not be saved. Existing data was left in place; check Kodi storage.")
        return False


def backoff_delay(failures, retry_after=None, rand=random.random):
    exp = min(MAX_DELAY, BASE_DELAY * (2 ** min(20, max(0, failures - 1))))
    delay = exp * (0.5 + 0.5 * rand())
    return max(delay, retry_after) if retry_after is not None else delay


def _note_failure(retry_after, now):
    global _failures, _next_attempt, _backoff_key
    _backoff_key = sesn_api.transport_fingerprint()
    _failures += 1
    _next_attempt = now + backoff_delay(_failures, retry_after)


def _note_success():
    global _failures, _next_attempt
    _failures, _next_attempt = 0, 0.0


def _paused(now):
    if _next_attempt and sesn_api.transport_fingerprint() != _backoff_key:
        _note_success()
    return now < _next_attempt or not account.current()


def flush(now=None, limit=FLUSH_LIMIT):
    """Deliver one bounded batch for the verified current owner only.

    The network call does not hold the SQLite transaction/lock. If capture occurs
    during a slow POST, acknowledgement deletes only the exact row sent.
    """
    now = time.time() if now is None else now
    if _paused(now):
        return 0
    owner = account.current()
    transport = sesn_api.transport_fingerprint()
    sent = 0
    try:
        with local_state.database() as db:
            items = db.execute("SELECT id,payload FROM outbox WHERE owner=? ORDER BY id LIMIT ?",
                               (owner, max(1, min(FLUSH_LIMIT, limit)))).fetchall()
        for row_id, raw in items:
            if account.current() != owner or sesn_api.transport_fingerprint() != transport:
                break
            payload = json.loads(raw)
            outcome, retry_after = sesn_api.send_result(payload, expected_transport=transport, durable=True)
            if outcome != sesn_api.OK:
                # Even permanent/unsupported responses remain visible in the
                # outbox. No server rejection is permission to destroy a watch.
                _note_failure(retry_after, now)
                if outcome in (sesn_api.DROP, sesn_api.BLOCKED):
                    _warn("refused", "Queued events were retained because the server could not accept them.")
                break
            with _lock, local_state.database() as db:
                db.execute("DELETE FROM outbox WHERE id=? AND owner=?", (row_id, owner))
            _note_success()
            sent += 1
            if (payload.get("action") == "stop" and (payload.get("progress") or 0) >= 80
                and now - payload.get("watched_at", 0) < 60 and xbmcaddon.Addon().getSettingBool("notify")):
                try:
                    xbmcgui.Dialog().notification("Sesn", "Sent %s to Sesn" % payload.get("title", "this title"),
                                                 xbmcgui.NOTIFICATION_INFO, 2500)
                except Exception:
                    pass
    except Exception:
        _warn("storage", "Local queue could not be read or acknowledged. Existing data was left in place.")
    return sent


def enqueue(payload, now=None):
    """Compatibility helper: persist first, then attempt a bounded delivery."""
    if not capture(payload, now=now):
        return False
    return bool(flush(now=now, limit=1))
