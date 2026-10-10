# Sesn for Tautulli

An optional configurator that connects [Tautulli](https://tautulli.com) to
[Sesn](https://sesn.io). It runs on your own network and creates a Sesn webhook
notification agent in Tautulli. The script is a setup/sync tool, not a background
playback service; Tautulli sends subsequent notifications directly to Sesn.

For browser-assisted setup instead, open
[Connections → Tautulli](https://sesn.io/connections/tautulli). Your browser must
be able to reach your Tautulli server; its API key is used directly with that
server and is not sent to Sesn.

## Source-only safety changes

The current source binds saved credentials to their original destinations and
verifies notification-agent ownership before changing or deleting an agent.
These protections are not a claim about the separately hosted script or the
browser-assisted setup. Updating this repository does not publish either one.
An existing configuration without bindings is rejected; it is never migrated
or adopted. Follow [Remove and recover](#remove-and-recover) before fresh setup.

## Requirements

- A working Tautulli server connected to Plex, reachable from the machine running the script.
- Your Tautulli API key from Settings → Web Interface → API.
- Node.js 18 or newer with built-in `fetch`; prefer a currently supported Node.js release. No npm dependencies are needed.
- A Sesn account and a browser to approve the initial pairing code.

## Use the source checkout

From this repository's root:

```sh
node tautulli/sesn-tautulli.mjs setup
```

Enter your Tautulli address and API key, then approve the displayed code in Sesn.
The script reports the active local viewer IDs/names and creates its notification
agent. Open [Connections → Tautulli](https://sesn.io/connections/tautulli), choose
who is tracked, then run:

```sh
node tautulli/sesn-tautulli.mjs sync
```

Run `sync` again whenever the tracked people or Plex users change. An empty
tracked list configures a non-matching user condition, so no playback is sent.
The script does not continually refresh mappings on its own.

## Hosted download

The hosted script is a separately published copy and may lag this source. Do not
assume it has the credential-binding and ownership protections described here.
For those protections, use the source checkout above until a matching hosted
copy has been published and verified. To use the hosted copy in a new directory:

```sh
curl --fail --location --output sesn-tautulli.mjs https://sesn.io/downloads/tautulli/sesn-tautulli.mjs
node sesn-tautulli.mjs setup
node sesn-tautulli.mjs sync
```

Use commands for the copy you actually installed. The configuration file lives
next to that script, not in your current working directory. Do not create a second
copy/configuration unintentionally: it can create another notification agent.

## Credentials and data sent

- Setup and sync report active local viewer IDs/names plus the Plex server ID/name to Sesn, including viewers whose playback is not tracked.
- The webhook is conditioned on the tracked user IDs and movie/episode media types. Untracked playback is filtered by Tautulli before sending; viewer discovery still occurs as described above.
- Notifications include title/provider IDs, viewer/server identifiers, progress, session/item/device IDs, timestamps and playback-quality/codec/language/library-name metadata. The template does not request media files, local paths, IP addresses or location.
- Both the Tautulli API key and the paired Sesn key are stored in `sesn-tautulli.json` next to the script. The script attempts owner-only permissions; secure the directory and its backups, especially on Windows. Never commit or share this file.
- The Tautulli API key is sent only to the configured Tautulli server. The Sesn key is also stored in that server's webhook configuration, so Tautulli administrators can access it. Protect both credentials.

Set `TAUTULLI_URL` and `TAUTULLI_API_KEY` to skip the address/key prompts during
`setup`. Initial device-code approval still needs a person; these variables do
not make first-time pairing unattended. `sync` and `remove` use the saved
configuration and do not replace those saved values from the environment.

`SESN_URL` defaults to `https://sesn.io`. It requires HTTPS, except for loopback
HTTP testing, and warns for external non-Sesn hosts. Use the same trusted
`SESN_URL` for setup, sync and removal. A fresh pairing binds the Sesn key to its
canonical origin (scheme, host and port); the Tautulli key is bound to its base
URL, including the path. Changed destinations are rejected before saved keys
are sent. Credential-bearing requests never follow redirects. Remove the
original setup before choosing another origin or server. Use HTTPS or a trusted
local network for the Tautulli address as well.

Before updating or deleting a saved agent, the configurator checks the Plex
server identity and the live agent's ID, webhook type, destination, method and
credential fingerprint. A name such as “Sesn” or a saved ID alone is not proof of
ownership. A saved empty agent is not adopted, even after interrupted setup.

## Remove and recover

For the source checkout:

```sh
node tautulli/sesn-tautulli.mjs remove
```

For the standalone download, use `node sesn-tautulli.mjs remove` beside that copy.
For a bound configuration, the current source independently removes its verified
Tautulli agent and revokes the Sesn pairing. An unconfirmed step exits
unsuccessfully and retains the local information needed to retry, including the
agent fingerprint if revocation succeeded first. Restore connectivity and run
`remove` again with the original destinations. If ownership changed or an empty
agent remains after interrupted setup, review it manually in Tautulli rather than
editing the saved ID or binding fields. Keep the configuration until cleanup is
complete. Removing the integration does not delete Sesn history.

For an older configuration without bindings, all commands stop before sending
credentials. There is no migration or adoption command. Manually remove the old
agent in Tautulli and revoke its pairing on Sesn; only after cleanup, remove
`sesn-tautulli.json` and run `setup` for a fresh provider-specific pairing. Do not
copy an unscoped API key into a new configuration or add binding fields by hand.

Use one selected live source per Plex server, either native Plex webhooks or
Tautulli, as configured on sesn.io. Source selection and duplicate prevention are
backend responsibilities; this configurator does not itself reconcile two feeds.

## Offline checks

From the repository root with Node.js 24:

```sh
node --test tautulli/tests/*.test.mjs
```

The tests use synthetic responses, in-memory configuration and loopback-only
HTTP fixtures. They cover setup/sync filtering, data-template identity, origin
binding, live agent ownership, rejected legacy adoption, independent cleanup,
retry retention and redirect refusal without contacting real Tautulli, Plex or
Sesn services. Installed-server behavior and the separately hosted download
still need release-time verification.
