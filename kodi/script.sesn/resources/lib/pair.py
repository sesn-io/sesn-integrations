"""Device pairing — enter a short code at sesn.io/link instead of typing a key.

POST /api/v1/link/new -> show the short code + the URL in a progress dialog, then
POST /api/v1/link/poll every `interval` seconds until the user confirms on their
phone; the minted (scoped) key is stored in settings. Nothing sensitive is ever
typed on the remote.

The poll interval is never below MIN_INTERVAL whatever the server says, and grows
by SLOW_DOWN_STEP on HTTP 429 or an RFC 8628 "slow_down" answer.
"""
import time

import xbmc
import xbmcaddon
import xbmcgui

import sesn_api

ADDON = xbmcaddon.Addon()
MIN_INTERVAL = 5
MAX_INTERVAL = 60
SLOW_DOWN_STEP = 5


def clamp_interval(value, default=MIN_INTERVAL):
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = default
    return max(MIN_INTERVAL, min(MAX_INTERVAL, seconds))


def next_interval(interval, status, body, retry_after=None):
    """The wait before the next poll, given the last poll's response."""
    slow = status == 429 or (
        isinstance(body, dict) and "slow_down" in (body.get("status"), body.get("error"))
    )
    if slow:
        interval = interval + SLOW_DOWN_STEP
    if retry_after:
        interval = max(interval, int(retry_after))
    return clamp_interval(interval)


def _display_url(url):
    return (url or "https://sesn.io/link").replace("https://", "").replace("http://", "")


def run():
    start = sesn_api.post_json("/api/v1/link/new", {"device_name": "Kodi", "provider": "kodi"})
    if not start or not start.get("ok"):
        xbmcgui.Dialog().notification(
            "Sesn", "Couldn't start pairing — check your connection", xbmcgui.NOTIFICATION_ERROR
        )
        return

    code = start.get("user_code_display") or start.get("user_code") or ""
    device_code = start.get("device_code")
    url = _display_url(start.get("verification_url"))
    interval = clamp_interval(start.get("interval"))
    try:
        expires_in = max(60, min(3600, int(start.get("expires_in") or 900)))
    except (TypeError, ValueError):
        expires_in = 900

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

            http_status, poll, retry_after = sesn_api.post_json_ex(
                "/api/v1/link/poll", {"device_code": device_code}
            )
            interval = next_interval(interval, http_status, poll, retry_after)
            if not isinstance(poll, dict):
                continue
            status = poll.get("status")
            if status == "authorized" and poll.get("api_key") and 200 <= http_status < 300:
                ADDON.setSettingString("api_key", poll.get("api_key"))
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
