"""Kodi service: fast local sampling, independently scheduled network worker."""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "resources", "lib"))

import xbmc  # noqa: E402
import account  # noqa: E402
import monitor  # noqa: E402
import scrobble_queue  # noqa: E402
import sync  # noqa: E402
import sesn_api  # noqa: E402

TICK_SECONDS = 1
NETWORK_TICK = 10
SYNC_INTERVAL = 6 * 3600


class LibraryMonitor(xbmc.Monitor):
    def __init__(self):
        super().__init__()
        self.library_changed = threading.Event()

    def onNotification(self, sender, method, data):
        # A scan adds formerly absent items. Do not react to our own SetDetails
        # OnUpdate notifications, which would form a reconciliation feedback loop.
        if method in ("VideoLibrary.OnScanFinished", "VideoLibrary.OnCleanFinished"):
            self.library_changed.set()


def network_loop(mon):
    next_sync = time.time() + 30
    next_verify = 0
    binding = None
    while not mon.abortRequested():
        try:
            transport = sesn_api.transport_fingerprint()
            if transport != binding:
                binding, next_verify, next_sync = transport, 0, time.time() + 30
            owner = account.current()
            if not owner and time.time() >= next_verify:
                owner = account.verify_current()
                next_verify = time.time() + 300
                if not owner and sesn_api.is_configured() and not account.storage_failed():
                    scrobble_queue._warn("binding", "Account verification is pending. Playback capture is paused until this server confirms the connection.")
            if owner:
                scrobble_queue.flush()
                now = time.time()
                if mon.library_changed.is_set():
                    mon.library_changed.clear()
                    sync.invalidate_library()
                    next_sync = now
                if now >= next_sync:
                    complete = sync.run(full=sync.full_due(now))
                    # Failure or partial pages remain due, rather than marking a
                    # failed full reconcile as completed for another week.
                    next_sync = time.time() + (SYNC_INTERVAL if complete else NETWORK_TICK)
        except Exception as err:
            xbmc.log("[script.sesn] network loop error: %s" % type(err).__name__, xbmc.LOGWARNING)
        if mon.waitForAbort(NETWORK_TICK):
            break


def run():
    mon = LibraryMonitor()
    player = monitor.SesnPlayer()
    scrobble_queue.warn_legacy_queue()
    worker = threading.Thread(target=network_loop, args=(mon,), name="Sesn network", daemon=True)
    worker.start()
    xbmc.log("[script.sesn] service started", xbmc.LOGINFO)
    while not mon.abortRequested():
        player.sample_progress()
        if mon.waitForAbort(TICK_SECONDS):
            break
    # Captured events already live on disk; shutdown never needs to drain a
    # volatile event deque or wait indefinitely for a stalled network request.
    worker.join(timeout=2)
    xbmc.log("[script.sesn] service stopped", xbmc.LOGINFO)


if __name__ == "__main__":
    run()
