# Sesn add-on for Kodi (`script.sesn`)

## Version 0.2.9 — 21 September 2026

This release includes corrected episode parent IDs, pairing
registration in Connections and explicit API acknowledgement handling. A sent
event is no longer labelled as already saved. Eleven offline tests pass; installed
Kodi acceptance remains.


Scrobble what you watch in Kodi to your **[Sesn](https://sesn.io)** account — automatically, per
episode, on any box Kodi runs on (Windows, Linux, macOS, Fire TV, a Raspberry Pi on LibreELEC /
CoreELEC). Setup and capabilities: **[Sesn Connections → Kodi](https://sesn.io/connections/kodi)**.

**Requires Kodi 19 (Matrix) or newer** (Python 3). Pure Python — no compiled parts.

## Install

### Recommended — the Sesn repository (auto-updates)
1. In Kodi: **Settings → System → Add-ons → Unknown sources** → enable.
2. **Settings → Add-ons → Install from zip file** → install
   [`repository.sesn-1.0.0.zip`](repository.sesn-1.0.0.zip) (download it from this folder first).
3. **Install from repository → Sesn Repository → Program/Video add-ons → Sesn → Install.**
4. Future updates arrive on their own.

### Direct — a single zip (no auto-update)
Download the latest ZIP from [`repo/script.sesn`](repo/script.sesn) and use *Install from zip file*.

## Connect
Open the add-on and choose **Pair with a code** — you'll get a short code to confirm at
[sesn.io/link](https://sesn.io/link). Nothing to type on the remote. (Advanced: paste a key from
[sesn.io/api-keys](https://sesn.io/api-keys) instead.)

On a shared box, select the intended **Kodi profile** before pairing. Pair each profile with
that person's own Sesn account; playback is attributed to the account paired in the active profile.

## What it does
- **Scrobble** — logs a film or episode when you finish it (past ~80%), with rewatch support.
- **Sync from Sesn** *(off by default)* — bring new watched records and their associated ratings
  into matching items in the local Kodi library. Later rating edits are not continuously reconciled.
- **Browse** — your Watchlist / Up Next / lists inside Kodi; items already on the box are playable.
- **Scoped key** — a key on the box can log + read your own data, never touch your account.
- **Never** scrobbles live/PVR TV, touches your files, or fetches anything you don't have.

## Layout
```
kodi/
  script.sesn/        the add-on (service scrobbler + browse plugin)
  repository.sesn/    the Kodi repository add-on (for auto-updates)
  repo/               the hosted repository (addons.xml + zips)
```

Support: **support@sesn.io**.
