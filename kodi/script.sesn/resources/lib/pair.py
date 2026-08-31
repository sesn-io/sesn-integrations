"""Device pairing — enter a short code at sesn.io/link instead of typing a key.

POST /api/v1/link/new -> show the short code + the URL in a progress dialog, then
POST /api/v1/link/poll every `interval` seconds until the user confirms on their
phone; the minted (scoped) key is stored in settings. Nothing sensitive is ever
typed on the remote.
"""
import time

import xbmc
import xbmcaddon
import xbmcgui

import sesn_api

ADDON = xbmcaddon.Addon()


def _display_url(url):
    return (url or "https://sesn.io/link").replace("https://", "").replace("http://", "")


def run():
    start = sesn_api.post_json("/api/v1/link/new", {"device_name": "Kodi"})
    if not start or not start.get("ok"):
        xbmcgui.Dialog().notification(
            "Sesn", "Couldn't start pairing — check your connection", xbmcgui.NOTIFICATION_ERROR
        )
        return

    code = start.get("user_code_display") or start.get("user_code") or ""
    device_code = start.get("device_code")
    url = _display_url(start.get("verification_url"))
    interval = int(start.get("interval") or 5)
    expires_in = int(start.get("expires_in") or 900)

    # ⚠ Kodi 19+ DialogProgress.create/update take (heading, message) — a SINGLE
    # message string with \n, NOT the old 4-line signature (that throws TypeError).
    msg = (
        "On your phone or computer, go to [B]%s[/B]\n"
        "and enter this code:\n\n"
        "[B][COLOR FFFFC107]%s[/COLOR][/B]"
    ) % (url, code)
    dlg = xbmcgui.DialogProgress()
    dlg.create("Pair with Sesn", msg)

    deadline = time.time() + expires_in
    try:
        while time.time() < deadline:
            # Sleep the poll interval, staying responsive to Cancel.
            for _ in range(interval):
                if dlg.iscanceled():
                    return
                xbmc.sleep(1000)
            pct = int(100 * (1 - (deadline - time.time()) / float(expires_in)))
            dlg.update(min(99, max(0, pct)), msg)

            poll = sesn_api.post_json("/api/v1/link/poll", {"device_code": device_code})
            if not poll:
                continue
            status = poll.get("status")
            if status == "authorized":
                ADDON.setSettingString("api_key", poll.get("api_key") or "")
                dlg.close()
                xbmcgui.Dialog().notification(
                    "Sesn", "Paired — you're connected.", xbmcgui.NOTIFICATION_INFO
                )
                return
            if status == "expired":
                dlg.close()
                xbmcgui.Dialog().notification(
                    "Sesn", "That code expired — try again", xbmcgui.NOTIFICATION_WARNING
                )
                return
    finally:
        if not dlg.iscanceled():
            dlg.close()
    xbmcgui.Dialog().notification("Sesn", "Pairing timed out — try again", xbmcgui.NOTIFICATION_WARNING)
