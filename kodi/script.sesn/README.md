# Sesn for Kodi (`script.sesn`)

Scrobble what you watch in Kodi to your [Sesn](https://sesn.io) account — the
simple, private TV / film / anime tracker. Sends only the title/ids, position
and your key; never your library or file paths. **Live TV is never scrobbled.**

Requires **Kodi 19 (Matrix) or newer** (Python 3, `xbmc.python` 3.0.0). Pure
Python — runs the same on Windows, Linux, Android/Fire TV and LibreELEC /
CoreELEC / OSMC.

## Install

1. In Kodi: **Settings → System → Add-ons → Unknown sources** → enable.
2. **Settings → Add-ons → Install from zip file** → pick `script.sesn-x.y.z.zip`.
   (Or add the Sesn repository zip first, for automatic updates.)
3. The service starts automatically.

## Connect your account (v0.1 — paste a key)

1. On the web, go to **sesn.io/api-keys** and create a **scrobble** key
   (`sesn_…`). It's shown once.
2. In Kodi: **Add-ons → Sesn → Configure** (or Settings) → **Account** → paste the
   key into **API key**.

A device-code pairing flow (enter a short code at `sesn.io/activate`, nothing
typed on the remote) is planned to replace the paste step.

## What it does

- Sends a **start** when playback begins, **pause**/**resume** as you pause, and a
  **stop** with your final progress when it ends. Sesn logs the watch only when
  you finish (its 80% completion rule) — the addon holds no threshold itself.
- Reads stable ids (`getUniqueID` — imdb/tmdb/tvdb) when Kodi has them, falling
  back to title + year (+ show/season/episode) when it doesn't.
- Queues events offline and retries them, so a reboot or dropped connection
  doesn't lose a watch.

## Settings

- **Account:** API key, server URL (advanced).
- **Scrobbling:** scrobble movies / TV, notification on log.
- **Advanced:** verbose logging (for diagnosing; off by default).

## Notes

- **No live/PVR TV** and no music — only movies and episodes.
- A shared Kodi box scrobbles to whoever set up the key; Kodi exposes no
  per-viewer identity to gate on.
- Nothing but ids, title/runtime, position and your key ever leaves the device.

## Layout

```
script.sesn/
  addon.xml            service + settings-launcher extension points
  service.py           resident scrobbler (the loop)
  default.py           opens settings when you "run" the addon
  resources/
    settings.xml       account + scrobbling + advanced
    lib/
      sesn_api.py      POST to /api/v1/scrobble
      scrobble_queue.py  offline queue + retry
      monitor.py       xbmc.Player/Monitor -> scrobble events
    language/…/strings.po
```

Support: **support@sesn.io**.
