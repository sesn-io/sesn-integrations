"""Server-verified account bindings, stable across a same-account token rotation."""
import hashlib
import json
import re

import xbmc
import xbmcgui

import local_state
import sesn_api

_storage_failed = False
_storage_warned = False


def storage_failed():
    return _storage_failed


def _storage_error():
    global _storage_failed, _storage_warned
    _storage_failed = True
    if not _storage_warned:
        _storage_warned = True
        message = "Local account storage is unavailable. Capture is paused; existing data was left in place."
        xbmc.log("[script.sesn] " + message, xbmc.LOGWARNING)
        try:
            xbmcgui.Dialog().notification("Sesn", message, xbmcgui.NOTIFICATION_WARNING, 8000)
        except Exception:
            pass


def _owner(base, account_id):
    identity = json.dumps([base, account_id, local_state.PROFILE], separators=(",", ":"))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def current():
    """No network: return the verified current owner, or fail closed."""
    global _storage_failed
    transport = sesn_api.transport_fingerprint()
    if not transport or sesn_api.key_invalid():
        return None
    try:
        with local_state.database() as db:
            row = db.execute("SELECT owner FROM bindings WHERE transport=?", (transport,)).fetchone()
        _storage_failed = False
        return row[0] if row else None
    except Exception:
        _storage_error()
        return None


def verify_current():
    """Network worker only. Bind the exact key/origin that the server verified."""
    snapshot = sesn_api.transport_fingerprint()
    base = sesn_api.server_base()
    if not snapshot or not base or sesn_api.key_invalid():
        return None
    if current():
        return current()
    data = sesn_api.register_account(expected_transport=snapshot)
    account_id = data.get("account_id") if isinstance(data, dict) and data.get("ok") is True else None
    if not isinstance(account_id, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", account_id):
        return None
    if sesn_api.transport_fingerprint() != snapshot:
        return None
    owner = _owner(base, account_id)
    try:
        with local_state.database() as db:
            db.execute("INSERT OR REPLACE INTO bindings(transport,owner) VALUES(?,?)", (snapshot, owner))
    except Exception:
        _storage_error()
        return None
    return owner
