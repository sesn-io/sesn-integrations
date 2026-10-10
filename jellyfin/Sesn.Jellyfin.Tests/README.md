# Jellyfin lifecycle and durable completion regressions

Run with the .NET 10 SDK:

```sh
dotnet restore jellyfin/Sesn.Jellyfin.Tests --source https://api.nuget.org/v3/index.json
dotnet run --project jellyfin/Sesn.Jellyfin.Tests --no-restore
```

This executable harness uses the actual plugin classes and Jellyfin SDK types.
HTTP responses, Jellyfin interfaces, and configuration serialization are mocked;
credential persistence uses disposable files in the OS temporary directory. All
accounts, keys, server origins, viewers, and playback events are synthetic.
The process exits nonzero if any assertion fails. It never contacts a running
Jellyfin or Sesn service, performs sign-in, or runs packaging/release commands.

Coverage includes immediate disconnect, cancellation, retry backoff and restart,
revocation exhaustion, key replacement/origin isolation, stale configuration,
late HTTP and pairing responses, and normal pairing/delivery after recovery.
Completion tests cover persist-before-send, restart, duplicate provider callbacks,
immutable dates/payloads, strict body acknowledgements, permanent held failures,
unknown legacy files, capacity/expiry, transport backoff, and credential/viewer
routing generation changes. The original completed-stop loss regression fails
without the outbox integration. These are offline regression tests, not
installed-server acceptance tests.

The actual embedded settings script also has dependency-free Node 24 tests:

```sh
node --test jellyfin/Sesn.Jellyfin.Tests/ConfigPageTests.mjs
```

## Completion rollout dependency and limits

The native outbox requires a compatible strict-completion backend. See
[completed-watch delivery and compatibility](../README.md#completed-watch-delivery-and-compatibility)
for the response contract and installation requirements.
Until a successful authenticated viewer report advertises `durable_completion:1`
and a credential generation, completions are retained locally and not sent to any
legacy endpoint. A generation missing at original capture is never filled in
later. Unsupported endpoints, malformed receipts, and legacy 200 responses never
acknowledge a stored completion; there is no completion fallback to the old route.

Solo viewer-discovery tests confirm that only the selected local identity is
reported. Missing selections report no identities. Full local name/ID discovery
requires an authenticated household-mode response; pause and disconnect stop it.
Household playback permissions expire six minutes after the last authenticated
viewer report (five-minute refresh plus one minute of scheduling grace). Discovery
can still retry while playback is paused. Existing captured completions remain
held under their original generations; playback observed during the permission
pause is not captured or sent and can be recovered using manual server Sync.
Restart/settings changes require fresh permission. Single-viewer playback is not
gated by this household lease. The backend must filter mappings against their
current permitted targets when reporting allowed viewers.

Only completed stops enter private `completion-outbox.json`. Records contain
immutable source event/time, recipient and generation metadata plus a local
one-way credential/destination fingerprint, never the API key. Pending and
permanent failed records share the 500-item cap and seven-day retention. Held
records never move to replacement credentials or newly mapped recipients.
Over-capacity and expired records produce visible counts/warnings and point to
manual server Sync recovery. Unknown queue formats or missing ownership fields
are preserved byte for byte and cannot be replayed or overwritten.

Event IDs use the provider's valid PlaySessionId, server, viewer, item and owner
when available; duplicate callbacks keep the first captured payload/date. If
Jellyfin supplies no usable session ID, each callback gets a fresh occurrence ID,
so separate callbacks without source identity cannot be reliably coalesced.
Only an explicit matching durable receipt removes a queued record. A receipt
means Sesn accepted the completion durably; future `queued` receipts need not mean
history is already visible.

Start/progress and incomplete stops remain one-shot legacy activity events; they
are never added to or replayed from the completion outbox. Completed stops use
only the strict completion endpoint, which intentionally does not clear live
cards without ordered session protection. A completed title may remain on its
live card until activity expires. No delayed clear-only legacy stop is sent, because it could clear a newer
session under the same connection.

The production project's advertised ABI compilation matrix is separate:

```sh
dotnet build jellyfin/Sesn.Jellyfin/Sesn.Jellyfin.csproj -c Release -p:SesnTargetFramework=net9.0 -p:JellyfinVersion=10.11.11 -p:ExtensionsVersion=9.0.11
dotnet build jellyfin/Sesn.Jellyfin/Sesn.Jellyfin.csproj -c Release -p:SesnTargetFramework=net10.0 -p:JellyfinVersion=12.0.0 -p:ExtensionsVersion=10.0.11
dotnet build jellyfin/Sesn.Jellyfin/Sesn.Jellyfin.csproj -c Release -p:SesnTargetFramework=net10.0 -p:JellyfinVersion=12.1.0 -p:ExtensionsVersion=10.0.11
```

## Native parent-series identity

Payload version 1 includes `SeriesProvider_tmdb`, `SeriesProvider_imdb` and
`SeriesProvider_tvdb` from the actual Jellyfin Series object. Episode-level
`Provider_*` fields remain episode IDs. Legacy and strict Jellyfin adapters use
only the explicit versioned parent fields, then the existing year/name fallback.
Malformed supplied parent IDs receive an error instead of guessing by name.
Season zero and combined episode ranges are preserved. This does not infer or
convert alternate episode orderings, nor add parent discovery to Emby.
Existing queued payloads remain immutable and are not enriched during retry.
The compatible backend adapters must be installed before a native build that
emits version 1 is used. Backend adapter tests are maintained with the backend;
this harness verifies the actual native payload and preserves queued immutability.
