# Sesn for Jellyfin

Native Jellyfin server plugin by Object64 for securely sending a mapped viewer's
playback lifecycle to Sesn. Separate packages cover Jellyfin 10.11, 12.0 and
12.1 with their required .NET runtimes.

## What it does

- Reports the users on your Jellyfin server to Sesn, so you can choose on
  sesn.io who each one is.
- In a household setup, sends events only for the people you track; everyone
  else is filtered out on your own server. With a single viewer selected, only
  that viewer is sent.
- Pairs with Sesn using a short code, so no Sesn password is entered or stored
  on the Jellyfin server.
- Sends start, progress, pause, stop, and completion events.
- Revokes the Sesn credential server-side when disconnected or replaced.
- Never interrupts Jellyfin playback when Sesn is unavailable.

A household is the owner plus up to four other people. Each person's plays go
to their own Sesn account, and the owner never sees what anyone else watched.

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
   click **Pair**, then enter the short code at `https://sesn.io/link`.
4. Start a movie or episode as that viewer and confirm Sesn shows Watching Now.

Setup guide: https://sesn.io/help/jellyfin

## Build and package

Install the .NET 9 SDK, then run:

```sh
DOTNET=/path/to/dotnet node jellyfin/package.mjs
```

The three installable ZIPs and the generated repository manifest are written to
`jellyfin/artifacts/` (not committed; releases are published to sesn.io).

## Manual installation

Extract all three files from the ZIP matching your Jellyfin version (DLL,
`meta.json` and logo) into a new directory under Jellyfin's plugin directory,
restart Jellyfin, then pair as above.
