# Sesn for Tautulli

> **You probably don't need this.** Open
> <https://sesn.io/connections/tautulli>, enter your Tautulli address and API
> key, and Sesn sets Tautulli up from your browser. Nothing to download, and the
> key is not sent to Sesn. This script is for people who would rather automate
> the same thing.

A small configurator that connects [Tautulli](https://tautulli.com) to
[Sesn](https://sesn.io) for everyone in your household. It runs on your own
network and needs only Node.js 18 or newer — no install, no dependencies.

- Pairs with your Sesn account using a short code (no password).
- Reports your Tautulli (Plex) users so you can choose on sesn.io who is
  tracked: you, a child, an adult without email, an adult invited by email,
  or nobody.
- Creates one **Sesn** webhook agent in Tautulli that fires only for tracked
  users. Everyone else never leaves Tautulli.
- Your Tautulli API key stays on this machine. It is used only to talk to
  Tautulli and is saved in `sesn-tautulli.json` next to the script.

## Use

```sh
curl -O https://sesn.io/downloads/tautulli/sesn-tautulli.mjs
node sesn-tautulli.mjs setup     # pair, report users, create the agent
```

Then open <https://sesn.io/connections/tautulli>, choose who is tracked, and run:

```sh
node sesn-tautulli.mjs sync      # re-run after changing who is tracked
node sesn-tautulli.mjs remove    # delete the agent and revoke the pairing
```

For unattended runs set `TAUTULLI_URL` and `TAUTULLI_API_KEY` instead of
answering the prompts.

A Plex server uses exactly one live source: the native Plex webhook **or**
Tautulli. If both are set up, choose which one on sesn.io; plays never count twice.
