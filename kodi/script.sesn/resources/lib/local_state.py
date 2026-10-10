"""Transactional, profile-local state. Never store an API key in this database."""
from contextlib import contextmanager
import os
import sqlite3

import xbmcaddon
import xbmcvfs

PROFILE = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo("profile"))
DATABASE = os.path.join(PROFILE, "sesn-state.sqlite3")


@contextmanager
def database():
    os.makedirs(PROFILE, exist_ok=True)
    db = sqlite3.connect(DATABASE, timeout=5)
    try:
        # FULL + the rollback journal keep both insertion and acknowledgement
        # atomic across process termination. Each short transaction owns a new
        # connection, so callback and network threads never share a handle.
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE IF NOT EXISTS bindings (transport TEXT PRIMARY KEY, owner TEXT NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY, owner TEXT NOT NULL, event_id TEXT NOT NULL,
            payload TEXT NOT NULL, queued_at REAL NOT NULL, UNIQUE(owner,event_id))""")
        db.execute("CREATE INDEX IF NOT EXISTS outbox_owner ON outbox(owner,id)")
        db.execute("""CREATE TABLE IF NOT EXISTS sync_state (
            owner TEXT PRIMARY KEY, cursor INTEGER NOT NULL DEFAULT 0,
            full_cursor INTEGER, last_full REAL NOT NULL DEFAULT 0)""")
        with db:
            yield db
    finally:
        db.close()
