# Sesn for Jellyfin

> Source status: this branch contains unreleased reliability changes. Existing
> downloadable packages do not contain these changes. Install instructions below
> refer to the published release; do not package or install this source until the
> compatible backend is deployed and a new plugin release has been approved.

Native Jellyfin server plugin by Object64 for securely sending a mapped viewer's
playback lifecycle to Sesn. Separate packages cover Jellyfin 10.11, 12.0 and
12.1 with their required .NET runtimes.

## What it does

- In single-viewer mode, reports only the selected local viewer identity. Full
  local viewer discovery starts only after Sesn confirms household mode, so you
  can map the people you want to track on sesn.io.
- In a household setup, sends events only for the people you track; everyone
  else is filtered out on your own server. With a single viewer selected, only
  that viewer is sent. Household playback permission expires within six minutes
  without a successful refresh; discovery keeps retrying automatically. Turning
  off tracking pauses both playback and viewer reports.
- Pairs with Sesn using a short code, so no Sesn password is entered or stored
  on the Jellyfin server.
- Sends start, pause, resume and incomplete-stop activity once, plus at most one
  progress update every 90 seconds per stream. Completed watches are persisted
  locally before delivery, then retried using immutable event IDs and dates.
- Keeps episode provider IDs separate from explicit parent-series provider IDs,
  including specials and combined-episode ranges.
- Stops new delivery immediately on disconnect or replacement and cancels
  in-flight delivery. The old key is retained only for revocation at its original
  address. Revocation retries survive restart; exhausted retries leave a visible
  warning to remove the old connection on sesn.io. A key already revoked counts
  as done. Changing the Sesn address requires a fresh key or pairing.
- Keeps the Sesn key in a private file (`plugins/configurations/Sesn/credentials.json`
  under Jellyfin's data directory, owner-only on Linux and macOS), not in the
  plugin settings that the dashboard reads. The settings only show whether a key
  is stored. Keys from versions before 0.4 are moved there automatically.
- Stops sending if Sesn rejects the key (HTTP 401, or HTTP 403 during completed-
  watch delivery) until you connect again.
- Only talks to an `https://` Sesn address (plain `http://` is allowed for
  localhost testing) and logs a warning if the address isn't sesn.io.
- Never interrupts Jellyfin playback when Sesn is unavailable.

A household is the owner plus up to four other people. Mapped plays are sent to
the intended Sesn account. Account visibility still follows Sesn’s account and
household permissions.

The connection key can log watches and read its owner’s Sesn watch history,
ratings attached to watches, lists and Up Next. Read access is not limited to
plays from this server. Only connect servers whose administrators you trust;
anyone with access to the stored key can use those permissions.

## Install

**Easiest:** open <https://sesn.io/connections/jellyfin>, enter your Jellyfin
address and an API key (Dashboard → API Keys), and confirm the restart. Your
browser adds this plugin, restarts Jellyfin once and connects it. The key is
used in your browser and is not sent to Sesn.

**By hand:**

1. In Jellyfin, open Dashboard → Plugins → Repositories and add a repository
   named **Sesn** with the URL `https://sesn.io/downloads/jellyfin/manifest.json`.
2. Open Plugins → Catalog, install **Sesn**, then restart Jellyfin. Jellyfin
   only offers the build that matches your server version.
3. Open Plugins → My Plugins → Sesn → Settings, choose the Jellyfin viewer,
   click **Connect to Sesn**, then enter the short code at `https://sesn.io/link`.
4. Confirm the intended Sesn account. For a new single-viewer connection, complete
   one watch as the selected viewer and confirm it appears in history. This first
   completion binds the viewer and server; initial start/progress events do not.
   Start another title to check Watching Now. Household setups need an active
   viewer mapping first.

Setup guide: https://sesn.io/help/jellyfin

## Build and package

Install the .NET 10 SDK, then run:

```sh
DOTNET=/path/to/dotnet node jellyfin/package.mjs
```

The three installable ZIPs and the generated repository manifest are written to
`jellyfin/artifacts/` (not committed; releases are published to sesn.io).

## Manual installation

Extract all three files from the ZIP matching your Jellyfin version (DLL,
`meta.json` and logo) into a new directory under Jellyfin's plugin directory,
restart Jellyfin, then pair as above.

## Completed-watch delivery and compatibility

Completed-watch retries require a compatible Sesn backend. A successful viewer
report must advertise `durable_completion: 1` and a credential generation before
a completion is captured for automatic delivery. Household completions also
capture the original viewer mapping generation. Missing generations are never
filled in later, and records never move to another credential or mapped viewer.

The plugin posts completions only to `/api/v1/webhooks/jellyfin/events`. Only a
matching version-1 receipt with `ok: true`, `durable: true`, the original `event_id`
and an `applied` or `queued` state acknowledges a record. A missing endpoint,
legacy success body or malformed receipt leaves it pending; there is no fallback
to the legacy completion route. Acknowledged `queued` events may take time to
appear in history. Permanent validation or mapping errors remain visible as
failed records rather than being silently discarded.

The private `completion-outbox.json` stores at most 500 records for seven days.
It contains the original completion metadata and a one-way connection fingerprint,
never the API key. Pending, held, failed and expired counts appear in the plugin
settings with the last confirmed acceptance time. Held records preserve their
original ownership. Unknown queue formats are preserved without replay or
replacement. Use server Sync in Sesn to recover watches that could not be
captured, expired, or exceeded the limit; original dates may be limited by the
history Jellyfin retains.

A provider PlaySessionId allows repeated stop callbacks to share an event ID and
keep the first captured date. Without a usable provider session ID, separate
callbacks cannot reliably be recognized as duplicates. Start/progress and
incomplete stops are never queued. Completed stops do not send an unordered
legacy clear, so a completed title may remain on its live card until activity
expires.

Native payload version 1 uses `SeriesProvider_tmdb`, `SeriesProvider_imdb` and
`SeriesProvider_tvdb` for parent-series identity. Compatible backend adapters
must understand these fields before a build using them is installed. Episode
`Provider_*` values remain episode IDs; alternate episode orderings are not
inferred or converted. Existing queued payloads are never enriched during retry.

Build and offline regression commands are in [the test guide](Sesn.Jellyfin.Tests/README.md).
Compilation against an SDK does not replace installation and upgrade checks on
that Jellyfin server version.
