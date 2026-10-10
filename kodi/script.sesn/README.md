# Sesn for Kodi (`script.sesn`)

## Unreleased source

This checkout has account-bound durable delivery and sync fixes. It still
declares 0.2.11 because publishing a new addon is a separate release step.
The existing 0.2.11 downloadable ZIP does not include these changes.
See [the integration checklist](../INTEGRITY.md) before packaging this source.

## Version 0.2.11 — 4 October 2026

If Sesn rejects this device's key (it was revoked on sesn.io), the add-on stops
sending, keeps queued plays and asks you to pair again. Events Sesn refuses are
dropped instead of retried forever; when Sesn is unavailable the add-on backs off
(up to 30 minutes, honouring Retry-After) and queued plays expire after 7 days.
The key is only sent to an `https://` Sesn address. Pairing and sync poll more
gently.

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

Pairing explicitly requests a Kodi-scoped key. Use a fresh code pairing instead
of entering a generic or older unscoped API key; those keys are not automatically
adopted or migrated. The Advanced API key field does not grant a key Kodi access.
Manage or revoke old keys at [sesn.io/api-keys](https://sesn.io/api-keys).
Pair separately in each Kodi profile on a shared box.

## Behavior and settings

- Playback sends start, pause/resume and stop events. Sesn applies its completion
  rule (80%); partially watched playback does not become a completed watch.
- Movie IDs come from the playing movie. Episode references use the parent show's
  library IDs and season/episode numbers. Streams without a known library parent
  fall back to the show title and episode numbers, never the episode's own IDs.
- Events are committed to the profile's SQLite outbox before delivery, with
  original UTC timestamps and stable event/session IDs. Retries retain those
  fields. Server throttling (including older HTTP-200 responses) keeps the event.
- Pairing must be verified against the server before capture starts. Disconnect
  stops new capture. Another account or server cannot receive a previous owner's
  events; a verified new key for the same account can resume its own backlog.
- Older unbound queue.json is preserved and never uploaded automatically. Old
  account queues and rejected events are also preserved. The 500-event total cap
  visibly refuses new capture when full; it never evicts an existing event.
- The strict event endpoint must explicitly acknowledge durable acceptance.
  Unsupported servers and ambiguous replies retain the queue without falling
  back to the old endpoint. This source therefore requires the matching backend.
- If Sesn rejects the key (for example it was revoked on sesn.io), nothing more
  is sent until you pair again; the add-on shows a notice and keeps queued plays.
- The key is only sent to an `https://` Sesn address (the Advanced server
  setting accepts plain `http://` for localhost testing only).
- **Scrobbling** controls movie/episode logging and confirmation notifications.
- **Two-way sync** is the setting label for optional sync from Sesn, off by default.
  It marks matching local items watched and copies the associated ratings from new
  watched records. Failed local reads/writes leave the page pending. A persistent
  weekly full pass and finished-library-scan trigger revisit older state, including
  ratings; this is not a continuous rating-edit feed or unwatch/resume sync.
- **Account → Disconnect** revokes this profile's key on sesn.io and forgets it
  here. If Sesn can't be reached, the key is still forgotten and the add-on asks
  you to remove the device on sesn.io.

The add-on sends identifying metadata, playback progress and its credential, never
media files or local file paths. Playback belongs to the account paired in the
active Kodi profile; the add-on cannot identify separate viewers within one profile.

Website setup: [Sesn Connections → Kodi](https://sesn.io/connections/kodi).
Support: **support@sesn.io**.
