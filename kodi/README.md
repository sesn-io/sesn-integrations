# Sesn add-on for Kodi (`script.sesn`)

## Unreleased integrity changes

The source now uses a server/account/profile-bound SQLite outbox, immutable
playback event IDs and original timestamps, checked local writes, and persistent
weekly reconciliation. Network delivery no longer blocks playback sampling.
These changes are **not in the 0.2.11 ZIP**. See [INTEGRITY.md](INTEGRITY.md) for
required backend contracts, offline tests and installed-device release gates.
No addon version, release artifact or repository index was changed for this work.

## Version 0.2.11 and repository 1.0.1 — 4 October 2026

The add-on stops sending after Sesn rejects its key and asks you to pair again,
drops events Sesn refuses, backs off (up to 30 minutes, honouring Retry-After)
when Sesn is unavailable, expires queued plays after 7 days and only sends the
key to an `https://` Sesn address. Repository 1.0.1 reads add-ons from the
protected `kodi-repo` branch and has Kodi verify SHA-256 checksums for the index
and each download.

## Version 0.2.10 — 2 October 2026

Disconnect now revokes this device's key on sesn.io as well as forgetting it on
the box. If Sesn can't be reached, the key is still forgotten locally and Kodi says
to remove the device in Settings → Devices & API keys.

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
   [`repository.sesn-1.0.1.zip`](https://github.com/sesn-io/sesn-integrations/raw/kodi-repo/repository.sesn/repository.sesn-1.0.1.zip)
   (download it first; the same file is also in this folder).
3. **Install from repository → Sesn Repository → Program/Video add-ons → Sesn → Install.**
4. Leave automatic updates enabled for this add-on to receive future releases.

### Direct — a single zip (no auto-update)
Download the latest ZIP from the [`kodi-repo` branch](https://github.com/sesn-io/sesn-integrations/tree/kodi-repo/script.sesn)
and use *Install from zip file*.

## Connect
Open the add-on and choose **Pair with a code** — you'll get a short code to confirm at
[sesn.io/link](https://sesn.io/link). Nothing to type on the remote. (Advanced: paste a key from
[sesn.io/api-keys](https://sesn.io/api-keys) instead.)

On a shared box, select the intended **Kodi profile** before pairing. Pair each profile with
that person's own Sesn account; playback is attributed to the account paired in the active profile.

## What it does
- **Scrobble** — records position-complete playback (at least 80%, or natural end).
  Seeking to the end can qualify; this does not measure time actually watched.
  Released 0.2.11 has heuristic movie rewatch support; strict event-based movie
  and episode rewatches require the unreleased client and matching backend.
- **Sync from Sesn** *(off by default)* — bring new watched records and their associated ratings
  into matching items in the local Kodi library. Later rating edits are not continuously reconciled.
- **Browse** — your Watchlist / Up Next / lists inside Kodi; items already on the box are playable.
- **Per-device key** — a key on the box can log watches to your account (adding to your
  history and stats) and read your own data (Watchlist, Up Next, lists). Treat it like a
  password; revoke it at any time from Settings → Devices & API keys on sesn.io.
- **Never** scrobbles live/PVR TV or uploads media files/local paths. Browsing fetches
  metadata and artwork; playback opens only matching items already in your Kodi library.
  Optional sync changes local watched/rating state, and the add-on stores its own local settings/queue.

## Layout
```
kodi/
  script.sesn/        the add-on (service scrobbler + browse plugin)
  repository.sesn/    the Kodi repository add-on (for auto-updates)
  tools/release.py    builds the hosted repository tree for the kodi-repo branch
  repo/               old hosted location, still read by repository.sesn 1.0.0
  tests/              offline tests: python3 -m unittest discover -s kodi/tests
```

## Releasing
The repository is served from the protected `kodi-repo` branch; only the output of
`kodi/tools/release.py` is committed there.

Before releasing the current source, complete the backend and installed-device
gates in [INTEGRITY.md](INTEGRITY.md). Do not package it as the existing 0.2.11.

1. Bump `version` (and `<news>`) in the changed add-on's `addon.xml`. Published
   versions are immutable; the script refuses to overwrite one with different contents.
2. `git worktree add ../kodi-repo kodi-repo`, then
   `python3 kodi/tools/release.py --out ../kodi-repo`.
3. Review, commit and push the `kodi-repo` worktree.

Kodi checks `addons.xml` against `addons.xml.sha256` and each zip against its
`.sha256` file. These guard against corrupted or partial downloads; they come
from the same branch, so the branch protection is what stops tampering.

Support: **support@sesn.io**.
