# Kodi integrity changes: source-only, unreleased

Baseline: sesn-io/sesn-integrations fde25488a2fed44dab79325384b85741de41aa46,
shipped addon 0.2.11. The private monorepo's Kodi 0.2.8 is historical and is not
the source target. No version bump, package, release-branch write or deployment
is part of these changes.

## Required backend contracts

1. POST /api/v1/link/register with the existing key and {"provider":"kodi"}
   must return {"ok":true,"account_id":"<stable authenticated identity>"}.
   The account identifier is server-selected. Private backend commit 530373c
   introduces this Kodi registration support. No new credential is requested.
2. POST /api/v1/scrobble/events must accept immutable provider ("kodi"), event_id, session_id,
   sequence and watched_at (integer UTC seconds). Start/pause/resume/stop events
   share one session; a new sitting gets a new UUID. Completion creates history;
   lifecycle and below-threshold events do not.
3. A durable acknowledgement is {"ok":true,"protocol_version":1,
   "event_id":"<exact submitted ID>","durable":true,"state":"queued"} or
   state "applied". Backend persistence must deduplicate both request retries
   and repeated completion for the same sitting, while allowing real rewatches.
   The provider hint must match the authenticated key's registration. Same-account
   verified token rotation may resume an immutable event; a changed account,
   provider, target or payload may not.
   Backend completion and ordered lifecycle implementations are separate
   prerequisites. Neither source publication nor offline mocks establishes that
   they have been deployed.

No fallback to legacy /scrobble is allowed for persisted records. Missing
identity pauses capture; 404 or ambiguous event responses preserve the outbox.
Deploy and verify the matching backend before separately approving an addon
release. Source still has the old version deliberately; packaging that version
would violate release immutability.

## Local safety model

- SQLite uses FULL synchronization and atomic transactions for capture and
  acknowledgement. No key is stored in this database. Credentials remain in
  Kodi settings as before. State is scoped by configured server,
  server-verified account and Kodi profile path; a token fingerprint maps to
  that identity only after verification. Token rotation does not guess ownership.
- A callback commits before any POST. HTTP happens on a dedicated thread, with
  no open SQLite transaction during network waits. Acknowledgement removes only
  the exact row sent, preserving concurrent callback capture.
- The legacy JSON queue and legacy cursor are not adopted. They have no proven
  owner. Existing bytes remain untouched for explicit review/recovery; there is
  no automatic migration or deletion. Another owner's outbox remains quarantined.
- The 500-event cap is across owners in a profile. Full/corrupt storage warns and
  fails closed instead of evicting prior data. This is bounded durable buffering,
  not a promise that events can be captured indefinitely without free storage.
  Permanent server rejection is visible and keeps the event; it can block later
  delivery until the cause is corrected. No destructive recovery is automated.
- Down-sync never writes after a failed playcount read and requires the setter's
  confirmed OK result. Page progress is recorded only after acknowledged writes.
  Full-pass progress and last successful completion survive daily restarts and
  are account-bound. Library scan/clean completion invalidates drift state;
  ordinary OnUpdate is ignored to prevent our own writes creating a loop.
- Sampling runs every second independently of network latency. Seek callback
  milliseconds are converted to seconds; stale pre-seek samples cannot overwrite
  a backward seek. A final valid sample is used when available. Natural end is
  position completion, not proof of actual elapsed viewing duration.

## Offline verification

Run: python3 -m unittest discover -s kodi/tests -v

Fixtures cover separate accounts/origins, same-account token rotation,
disconnected capture, unbound legacy files, strict receipts, lost acknowledgements,
year-boundary timestamps, transaction rollback after process termination,
storage corruption/overflow, concurrent capture during stalled network, failed
JSON-RPC reads/setters, playcount preservation, restart-independent weekly drift,
resumable full pages, scan-added titles and backward-seek immediate stop.

## Remaining installed release gates

Test Kodi Matrix/Nexus/Omega and supported Piers builds on representative Windows,
Linux and Android TV devices. Verify Python sqlite3 availability and filesystem
durability, callback ordering/seek units and teardown behavior, prompt/notification
visibility, multi-profile account transitions, permission/storage failures,
pairing and revoked-key recovery, strict backend queue/consumer behavior, and
actual library state. No installed-client, production-key, production-history,
release-package or deployment test is implied by the offline suite.

Provider contracts: [Kodi JSON-RPC v13](https://kodi.wiki/view/JSON-RPC_API/v13),
[Kodi 21.3 player callbacks](https://github.com/xbmc/xbmc/blob/21.3-Omega/xbmc/interfaces/legacy/Player.h),
[release helper](tools/release.py). The pinned public source tree has no GitHub
Actions workflow definitions; release packaging is an explicit separate step.
