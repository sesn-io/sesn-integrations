# Sesn integrations

Open-source clients that connect **[Sesn](https://sesn.io)** — a simple, private tracker for TV, film
and anime — to the apps you already watch in.

Everything here is client-side and open by design: you can read exactly what it does and what it
sends. The Sesn web app and API are separate and closed; these are the pieces that run on *your*
device.

| Integration | What it does | Status |
|---|---|---|
| **[Kodi add-on](kodi/)** | Scrobble what you watch to Sesn, two-way watched/ratings sync, browse your lists on the box | ✅ available |
| Browser extension | Log what you watch on streaming sites (Netflix first) | 🚧 planned |
| Stremio add-on | Scrobble + your lists as catalogs | 🚧 planned |

## Privacy

These clients send **only what a watch needs** — a title's ids, how far you got, and your key —
never your library, your files, or streaming passwords. Keys are **scoped**: a key on your device can
log and read your own data, but can't change or delete your account.

## Licence

[MIT](LICENSE). These clients are original work; Sesn itself and its API are separate.

## Attribution

This product uses the TMDB API but is not endorsed, certified, or otherwise approved by TMDB.
Title metadata and artwork shown by these clients originate from
[The Movie Database (TMDB)](https://www.themoviedb.org/), served through the Sesn API.

<p align="center"><a href="https://www.themoviedb.org/"><img src="https://www.themoviedb.org/assets/2/v4/logos/v2/blue_short-8e7b30f73a4020692ccca9c88bafe5dcb6f8a62a4c6bc55cd9ba82bb2cd95f6c.svg" alt="TMDB" height="26"></a></p>
