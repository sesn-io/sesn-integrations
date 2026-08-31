"""Sesn scrobbler — background service entry point.

Kodi launches this (addon.xml `xbmc.service`) on startup and expects it to run
resident until shutdown. The loop uses Monitor.waitForAbort as its sleep so
shutdown is instant; all network I/O happens here, off the player's callback
thread.
"""
import os
import sys
import time

# Flat imports for the modules under resources/lib.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "resources", "lib"))

import xbmc  # noqa: E402

import monitor  # noqa: E402
import scrobble_queue  # noqa: E402
import sync  # noqa: E402

TICK_SECONDS = 10
# Two-way down-sync cadence — deliberately slow (it's a background reconcile, not
# a hot path). A weekly FULL pass catches any drift the deltas missed.
SYNC_INTERVAL = 6 * 3600
FULL_INTERVAL = 7 * 24 * 3600


def run():
    mon = xbmc.Monitor()
    player = monitor.SesnPlayer()  # kept referenced for the service's life
    xbmc.log("[script.sesn] service started", xbmc.LOGINFO)

    next_sync = time.time() + 30  # first down-sync ~30s after boot
    next_full = time.time() + FULL_INTERVAL

    while not mon.abortRequested():
        try:
            player.sample_progress()  # cache last-known % while playing
            player.drain()  # send queued playback events
            scrobble_queue.flush()  # retry any persisted (offline) backlog
            now = time.time()
            if now >= next_sync:  # sync.run() no-ops unless the user turned it on
                full = now >= next_full
                sync.run(full=full)
                next_sync = now + SYNC_INTERVAL
                if full:
                    next_full = now + FULL_INTERVAL
        except Exception as err:
            xbmc.log("[script.sesn] loop error: %s" % err, xbmc.LOGWARNING)
        if mon.waitForAbort(TICK_SECONDS):
            break

    try:
        player.drain()  # best-effort final send on shutdown
    except Exception:
        pass
    xbmc.log("[script.sesn] service stopped", xbmc.LOGINFO)


if __name__ == "__main__":
    run()
