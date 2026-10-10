# Sesn integrations

Open-source clients that connect **[Sesn](https://sesn.io)** to the apps you watch in.
This repository contains the **Kodi add-on, Jellyfin server plugin and optional
Tautulli configurator**. The Sesn web app, API, browser extension and hosted
Stremio/Nuvio service are maintained separately; their source is not included here.

## Source and downloads

**The latest source is ahead of the published clients.** Kodi's account-bound
SQLite outbox and Jellyfin's durable completion/connection-ownership changes are
unreleased. They require compatible backend endpoints and installed-device
acceptance before a new release. Updating `main` does not update installed clients.

| Client | Published version | Source and setup |
|---|---|---|
| Kodi add-on | 0.2.11; repository 1.0.1 | [Kodi guide](kodi/README.md), [unreleased compatibility requirements](kodi/INTEGRITY.md) |
| Jellyfin plugin | 0.4 family: 0.4.0.0 / 0.4.1.0 / 0.4.2.0 for the corresponding server ABI | [Jellyfin guide and version matrix](jellyfin/README.md) |
| Tautulli configurator | Single-file script; the hosted copy is published separately | [Script setup, privacy and removal](tautulli/README.md) |

Kodi downloads are served from the `kodi-repo` branch. Jellyfin packages and the
hosted Tautulli script are served from sesn.io. No installable browser extension
or Stremio/Nuvio client ZIP is provided by this repository.

## Installation

- **Kodi:** download the [repository ZIP](https://github.com/sesn-io/sesn-integrations/raw/kodi-repo/repository.sesn/repository.sesn-1.0.1.zip), install it in Kodi, then install Sesn from the added repository. Follow the [Kodi guide](kodi/README.md) and pair using the displayed code.
- **Jellyfin:** open [Connections → Jellyfin](https://sesn.io/connections/jellyfin) for browser-assisted setup. Your browser must be able to reach your Jellyfin server. The Jellyfin API key is used directly with that server, not sent to Sesn. Manual repository installation and pairing are in the [Jellyfin guide](jellyfin/README.md).
- **Tautulli:** open [Connections → Tautulli](https://sesn.io/connections/tautulli) for browser-assisted setup, or use the [optional script](tautulli/README.md) from a machine that can reach Tautulli. The Tautulli API key is used directly with your Tautulli server, not sent to Sesn.

### Other Sesn integrations

- **Browser extension:** maintained separately and in testing; no extension source or install package is included here.
- **Stremio / Nuvio:** open [Connections → Stremio](https://sesn.io/connections/stremio) to create a personal manifest URL for the hosted catalogue add-on. It serves Watchlist/list metadata, not streams, and installing that catalogue alone does not log playback. Treat the personal URL as a credential and do not share it. Any separate account-sync feature is configured on sesn.io.

## Privacy and credentials

Playback clients send title/provider IDs, playback state, and a scoped Sesn key.
Depending on the integration, events also include viewer and server IDs/names,
client/device identifiers, original times and session IDs. Tautulli's webhook
includes playback-quality, codec, language and library-name metadata. It does not
request IP addresses or location in its notification template.

Viewer discovery is separate from playback filtering: Jellyfin and Tautulli
report local viewer names/IDs so accounts can be mapped on Sesn, including people
whose playback is not tracked. The unreleased Jellyfin source narrows discovery
to the selected viewer until Sesn authorizes household mode. Tautulli's script
reports its active viewer list during setup and sync. Review the individual guides
before connecting a shared server.

These clients do not upload media files, local file paths, streaming passwords,
or the entire media library. Kodi's optional down-sync does change matching local
watched/rating state. Keys can affect watch history and may read account data, so
treat them like passwords and revoke unused connections on sesn.io. Server
administrators and anyone who can read local credential files may access them.

Sesn requests require HTTPS except for explicit loopback HTTP development.
Custom HTTPS Sesn origins receive the Sesn credential. Use only a server you trust;
changing an address is not a way to migrate queued watches between accounts.

## Development checks

Run from the repository root with Python 3.9+ and Node.js 24 for these checks:

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s kodi/tests -v
node --test jellyfin/Sesn.Jellyfin.Tests/ConfigPageTests.mjs
node --test tautulli/tests/*.test.mjs
```

The first command checks local documentation links and stored Kodi release
integrity without network access or packaging. See the [Jellyfin test guide](jellyfin/Sesn.Jellyfin.Tests/README.md)
for .NET regression/build commands. Offline tests do not establish live backend
availability or installed-client compatibility. Release/package commands are
separate maintainer operations, not ordinary installation steps.

## Licence

[MIT](LICENSE). These clients are original work; Sesn itself and its API are separate.

## Attribution

This product uses the TMDB API but is not endorsed, certified, or otherwise approved by TMDB.
Title metadata and artwork shown by these clients originate from
[The Movie Database (TMDB)](https://www.themoviedb.org/), served through the Sesn API.

<p align="center"><a href="https://www.themoviedb.org/"><img src="https://www.themoviedb.org/assets/2/v4/logos/v2/blue_short-8e7b30f73a4020692ccca9c88bafe5dcb6f8a62a4c6bc55cd9ba82bb2cd95f6c.svg" alt="TMDB" height="26"></a></p>
