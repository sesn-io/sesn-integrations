# Sesn for Kodi (`script.sesn`)

## Version 0.2.9 — 21 September 2026

Parent-show identity, explicit Kodi pairing metadata and checked API responses
are included in the 0.2.9 ZIP. Eleven offline tests pass; installed-device
acceptance remains. The notification says an event was sent to Sesn, not that a
queued watch has already been saved. Manage pairing at Connections → Kodi, or
find and revoke an older unlabelled key under API keys and app sessions.


Log completed Kodi playback to your [Sesn](https://sesn.io) account and browse your
Watchlist, Up Next and lists. Requires **Kodi 19 (Matrix) or newer** with Python 3.
Live/PVR TV and music are never logged.

## Install and pair

1. Select the Kodi profile belonging to the person whose watches you want to log.
2. Follow the [repository installation guide](../README.md) for automatic updates,
   or install the current add-on ZIP through Kodi's **Install from zip file**.
3. Open **Sesn → Pair with a code**. Confirm the displayed code at
   [sesn.io/link](https://sesn.io/link) while signed in to the intended Sesn account.
4. Play a known film or episode and check the watch in Sesn after finishing it.

Manual key entry is an advanced fallback: create a key at
[sesn.io/api-keys](https://sesn.io/api-keys), then enter it under the add-on's
**Advanced → API key** setting. Pair separately in each Kodi profile on a shared box.

## Behavior and settings

- Playback sends start, pause/resume and stop events. Sesn applies its completion
  rule (80%); partially watched playback does not become a completed watch.
- Movie IDs come from the playing movie. Episode references use the parent show's
  library IDs and season/episode numbers. Streams without a known library parent
  fall back to the show title and episode numbers, never the episode's own IDs.
- Failed sends are queued on the device and retried.
- **Scrobbling** controls movie/episode logging and confirmation notifications.
- **Two-way sync** is the setting label for optional sync from Sesn, off by default.
  It marks matching local items watched and copies the associated ratings from new
  watched records. Later edits to existing ratings are not continuously reconciled.
- **Account → Disconnect** forgets this profile's key. Revoke access from the
  Sesn device/key page when you want the server to reject that credential as well.

The add-on sends identifying metadata, playback progress and its credential, never
media files or local file paths. Playback belongs to the account paired in the
active Kodi profile; the add-on cannot identify separate viewers within one profile.

Website setup: [Sesn Connections → Kodi](https://sesn.io/connections/kodi).
Support: **support@sesn.io**.
