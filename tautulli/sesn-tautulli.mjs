#!/usr/bin/env node
// Sesn configurator for Tautulli — https://sesn.io/connections/tautulli
//
// Runs on your own network. It pairs with Sesn using a short code, reports your
// Tautulli (Plex) users so you can choose on sesn.io who is tracked, and creates
// one Sesn webhook agent in Tautulli that only fires for those users.
//
// Your Tautulli API key is sent directly to the configured Tautulli server, not
// to Sesn. Both it and the paired Sesn key are stored in sesn-tautulli.json next
// to this script; the Sesn key is also stored in Tautulli's webhook settings.
//
//   node sesn-tautulli.mjs setup    pair, report users, create the webhook agent
//   node sesn-tautulli.mjs sync     re-report users and update who is sent
//   node sesn-tautulli.mjs remove   delete the webhook agent and revoke the pairing
//
// Set TAUTULLI_URL and TAUTULLI_API_KEY to skip setup's address/key prompts.
// Initial pairing still requires browser approval. Sync/remove use saved values.
//
// Requires Node.js 18 or newer. No dependencies.
import { readFileSync, writeFileSync, existsSync, chmodSync } from 'node:fs';
import { createInterface } from 'node:readline/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const VERSION = '1.1.1';
const CONFIG = join(dirname(fileURLToPath(import.meta.url)), 'sesn-tautulli.json');
const WEBHOOK_AGENT_ID = 25; // Tautulli's Webhook notification agent
const TRIGGERS = ['on_play', 'on_pause', 'on_resume', 'on_stop', 'on_watched'];
// Same data Sesn's manual Tautulli setup uses; {action} tells play/pause/stop/watched apart.
// `play` is how it was played (device, time played, quality) for your Sesn stats.
// No IP address or location is requested, so Tautulli never sends them.
const BODY = JSON.stringify({
  event: '{action}', media_type: '{media_type}', title: '{title}', show_name: '{show_name}', year: '{year}',
  show_year: '{show_year}', season: '{season_num}', episode: '{episode_num}', tmdb_id: '{themoviedb_id}',
  tvdb_id: '{thetvdb_id}', imdb_id: '{imdb_id}', progress_percent: '{progress_percent}', user_id: '{user_id}',
  username: '{username}', server_id: '{server_machine_id}', unixtime: '{unixtime}',session_id:'{session_id}',rating_key:'{rating_key}',machine_id:'{machine_id}',started_at:'{started_unixtime}',stopped_at:'{stopped_unixtime}',
  play: {
    seconds_played: '{stream_duration_sec}', runtime_seconds: '{duration_sec}', position_seconds: '{progress_duration_sec}',
    player: '{player}', product: '{product}', platform: '{platform}', device: '{device}', library: '{library_name}',
    transcode: '{transcode_decision}', video_decision: '{video_decision}', audio_decision: '{audio_decision}',
    resolution: '{video_full_resolution}', stream_resolution: '{stream_video_full_resolution}',
    dynamic_range: '{video_dynamic_range}', stream_dynamic_range: '{stream_video_dynamic_range}',
    video_codec: '{video_codec}', stream_video_codec: '{stream_video_codec}', audio_codec: '{audio_codec}',
    stream_audio_codec: '{stream_audio_codec}', audio_channels: '{audio_channel_layout}',
    stream_audio_channels: '{stream_audio_channel_layout}', audio_language: '{stream_audio_language}',
    subtitle_language: '{stream_subtitle_language}', bitrate: '{stream_bitrate}', container: '{container}',
    optimized: '{optimized_version}', relayed: '{relayed}', secure: '{secure}', live: '{live}', session_id: '{session_id}',
  },
}, null, 2);

const say = (...lines) => console.log(lines.join('\n'));
const fail = (message) => { console.error(`\n✖ ${message}`); process.exit(1); };

// The Sesn key is only ever sent over https (plain http is allowed to this
// machine for local testing). A host other than sesn.io is allowed but warned.
function sesnOrigin(value, warn = true) {
  const raw = (value || 'https://sesn.io').trim().replace(/\/+$/, '');
  let url;
  try { url = new URL(raw); } catch { fail('SESN_URL is not a valid origin.'); }
  const host = url.hostname.toLowerCase();
  const loopback = ['localhost', '127.0.0.1', '[::1]', '::1'].includes(host);
  if (!(url.protocol === 'https:' || (url.protocol === 'http:' && loopback))) {
    fail('SESN_URL must start with https:// (http:// is only allowed for localhost).');
  }
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/') fail('SESN_URL must contain only an origin, without credentials, a path, query or fragment.');
  if (warn && !loopback && host !== 'sesn.io' && !host.endsWith('.sesn.io')) {
    console.warn(`⚠ SESN_URL points at ${host}, not sesn.io. Use only an origin you trust; saved keys remain bound to their original origin.`);
  }
  return url.origin;
}
const SESN = sesnOrigin(process.env.SESN_URL);
const load = () => (existsSync(CONFIG) ? JSON.parse(readFileSync(CONFIG, 'utf8')) : {});
const save = (config) => { writeFileSync(CONFIG, `${JSON.stringify(config, null, 2)}\n`); try { chmodSync(CONFIG, 0o600); } catch { /* Windows */ } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fingerprint = (key) => createHash('sha256').update(key).digest('hex');
const validId = (value) => /^(?:[1-9][0-9]*)$/.test(String(value)) && Number.isSafeInteger(Number(value));
const webhook = (config) => `${config.sesnOrigin}/api/v1/webhooks/tautulli`;

function tautulliBase(value) {
  let url;
  try { url = new URL(value); } catch { fail('The Tautulli address must be an absolute http:// or https:// URL.'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    fail('The Tautulli address must use http:// or https:// without credentials, a query or fragment.');
  }
  return `${url.origin}${url.pathname.replace(/\/+$/, '')}`;
}

function assertSavedBindings(config) {
  if (((config.sesnKey || config.notifierId) && !config.sesnOrigin) || (config.tautulliApiKey && !config.tautulliKeyUrl)) {
    fail('This saved configuration has no credential origin binding and cannot be reused. No credentials were sent. Manually remove any old agent in Tautulli and revoke its pairing in Sesn, then remove sesn-tautulli.json and run setup for a fresh pairing.');
  }
  if (config.sesnOrigin && (sesnOrigin(config.sesnOrigin, false) !== config.sesnOrigin || config.sesnOrigin !== SESN)) {
    fail('SESN_URL does not match the saved pairing origin. Restore the original SESN_URL; remove that pairing before setting up a different origin.');
  }
  if (config.tautulliApiKey && tautulliBase(config.tautulliUrl) !== config.tautulliKeyUrl) {
    fail('The Tautulli address does not match the saved credential binding. Restore the original address before continuing.');
  }
}

function notifierProof(config) {
  return { notifierId: String(config.notifierId), tautulliUrl: config.tautulliKeyUrl,
    serverId: config.serverId, hook: webhook(config), credentialHash: fingerprint(config.sesnKey) };
}

function assertNotifierProof(config) {
  const proof = config.notifierProof;
  if (!validId(config.notifierId) || !proof || proof.notifierId !== String(config.notifierId)
    || proof.tautulliUrl !== config.tautulliKeyUrl || proof.serverId !== config.serverId
    || typeof config.serverId !== 'string' || !config.serverId || proof.hook !== webhook(config)
    || !/^[a-f0-9]{64}$/.test(proof.credentialHash)
    || config.sesnKey && proof.credentialHash !== fingerprint(config.sesnKey)) {
    throw new Error('Saved Tautulli agent ownership is unverified. Review and clean up the agent manually before removing the saved configuration and running setup for a fresh pairing.');
  }
  return proof;
}

async function assertServerIdentity(config) {
  const identity = await tautulli(config, 'get_server_identity');
  if (!config.serverId || (Array.isArray(identity) ? identity[0] : identity)?.machine_identifier !== config.serverId) {
    throw new Error('Tautulli now reports a different Plex server. Restore the original server or review its agents manually.');
  }
}

function agentCredentialMatches(agent, hash) {
  if (!agent.actions || typeof agent.actions !== 'object' || Array.isArray(agent.actions)
    || !agent.notify_text || typeof agent.notify_text !== 'object' || Array.isArray(agent.notify_text)) return false;
  let checked = 0;
  for (const trigger of new Set([...Object.keys(agent.actions), ...Object.keys(agent.notify_text)])) {
    const enabled = agent.actions[trigger];
    const subject = agent.notify_text[trigger]?.subject;
    if (enabled !== 0 && enabled !== 1) return false;
    if (!enabled && !subject) continue;
    if (typeof subject !== 'string' || subject.length > 4096) return false;
    let headers;
    try { headers = JSON.parse(subject); } catch { return false; }
    if (!headers || typeof headers !== 'object' || Array.isArray(headers)) return false;
    const keys = Object.entries(headers).filter(([name]) => name.toLowerCase() === 'x-api-key');
    if (keys.length !== 1 || typeof keys[0][1] !== 'string' || !keys[0][1] || keys[0][1].length > 512
      || fingerprint(keys[0][1]) !== hash) return false;
    checked++;
  }
  return checked > 0;
}

async function assertNotifierOwnership(config, newlyCreated = false) {
  const proof = assertNotifierProof(config);
  await assertServerIdentity(config);
  const agent = await tautulli(config, 'get_notifier_config', { notifier_id: config.notifierId });
  const sameAgent = agent && String(agent.id ?? agent.notifier_id) === proof.notifierId && agent.agent_id === WEBHOOK_AGENT_ID;
  // Only this invocation's newly created, still-empty agent may be initialized.
  // A saved id alone can never authorize replacing an existing configuration.
  const emptyNewAgent = newlyCreated && sameAgent && agent.config?.hook === ''
    && agent.actions && Object.keys(agent.actions).length > 0 && Object.values(agent.actions).every(value => value === 0)
    && agent.notify_text && Object.values(agent.notify_text).every(text => !text.subject && !text.body);
  if (!emptyNewAgent && !(sameAgent && agent.config?.hook === proof.hook && agent.config?.method === 'POST'
    && agentCredentialMatches(agent, proof.credentialHash))) {
    throw new Error('Tautulli agent ownership changed or could not be verified. Review the saved agent in Tautulli; its settings were not changed.');
  }
}

async function notifierAbsent(config) {
  await assertServerIdentity(config);
  const agents = await tautulli(config, 'get_notifiers');
  return Array.isArray(agents) && agents.every(agent => agent && validId(agent.id ?? agent.notifier_id))
    && !agents.some(agent => String(agent.id ?? agent.notifier_id) === String(config.notifierId));
}

async function tautulli(config, cmd, params = {}, method = 'GET') {
  assertSavedBindings(config);
  const url = new URL(`${config.tautulliUrl}/api/v2`);
  url.searchParams.set('cmd', cmd);
  const body = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]));
  if (method === 'GET') for (const [k, v] of body) url.searchParams.set(k, v);
  const response = await fetch(url, {
    method, headers: { 'X-Api-Key': config.tautulliApiKey, ...(method === 'POST' ? { 'content-type': 'application/x-www-form-urlencoded' } : {}) },
    body: method === 'POST' ? body : undefined, signal: AbortSignal.timeout(15000), redirect: 'error',
  }).catch((error) => { throw new Error(`Could not reach Tautulli at ${config.tautulliUrl} (${error.message}).`); });
  const json = await response.json().catch(() => null);
  const out = json?.response;
  if (!response.ok || out?.result !== 'success') throw new Error(`Tautulli ${cmd} failed: ${out?.message ?? response.status}`);
  return out.data;
}

async function sesn(path, body, config) {
  if (config) assertSavedBindings(config);
  const key = config?.sesnKey;
  const response = await fetch(`${SESN}${path}`, {
    method: 'POST', signal: AbortSignal.timeout(15000), redirect: 'error', // never forward the key elsewhere
    headers: { 'content-type': 'application/json', 'user-agent': `Sesn-Tautulli/${VERSION}`, ...(key ? { 'x-api-key': key } : {}) },
    body: JSON.stringify(body ?? {}),
  }).catch((error) => { throw new Error(`Could not reach Sesn (${error.message}).`); });
  const json = await response.json().catch(() => ({}));
  return { status: response.status, json };
}

async function ask(config) {
  // Environment variables skip address/key prompts; otherwise prompt line by line
  // (readline's question() drops answers when input is piped).
  let url = process.env.TAUTULLI_URL;
  let key = process.env.TAUTULLI_API_KEY;
  if (!url || !key) {
    const rl = createInterface({ input: process.stdin, terminal: false });
    const lines = rl[Symbol.asyncIterator]();
    const prompt = async (text) => { process.stdout.write(text); const next = await lines.next(); return next.done ? '' : next.value.trim(); };
    url = url || (await prompt(`Tautulli address [${config.tautulliUrl ?? 'http://localhost:8181'}]: `)) || config.tautulliUrl || 'http://localhost:8181';
    key = key || (await prompt(`Tautulli API key (Tautulli Settings → Web Interface → API)${config.tautulliApiKey ? ' [keep saved key]' : ''}: `)) || config.tautulliApiKey;
    rl.close();
  }
  if (!key) fail('A Tautulli API key is required.');
  const base = tautulliBase(url);
  if (config.tautulliKeyUrl && base !== config.tautulliKeyUrl) fail('TAUTULLI_URL does not match the saved credential binding. Remove the original setup before changing servers.');
  return { ...config, tautulliUrl: base, tautulliKeyUrl: base, tautulliApiKey: key };
}

async function pair(serverName) {
  const start = await sesn('/api/v1/link/new', { provider: 'tautulli', device_name: `Tautulli · ${serverName}` });
  if (start.status !== 200 || !start.json.device_code) fail(`Sesn could not start pairing (${start.status}).`);
  say('', `On a signed-in device open ${start.json.verification_url ?? `${SESN}/link`} and enter:`, '', `    ${start.json.user_code_display}`, '', 'Waiting for you to approve…');
  const deadline = Date.now() + (start.json.expires_in ?? 900) * 1000;
  while (Date.now() < deadline) {
    await sleep(3000);
    const poll = await sesn('/api/v1/link/poll', { device_code: start.json.device_code });
    if (poll.json.status === 'authorized' && poll.json.api_key) return poll.json.api_key;
    if (poll.json.status === 'expired') fail('That code expired. Run setup again.');
  }
  return fail('That code expired. Run setup again.');
}

async function report(config) {
  const users = (await tautulli(config, 'get_users')) ?? [];
  const viewers = users.filter((u) => u.user_id && u.user_id !== 0 && u.is_active !== 0)
    .map((u) => ({ id: String(u.user_id), name: u.friendly_name || u.username || String(u.user_id) }));
  const result = await sesn('/api/v1/connections/viewers', {
    provider: 'tautulli', server_id: config.serverId, server_name: config.serverName, viewers,
  }, config);
  if (result.status === 401) fail('Sesn no longer accepts this pairing. Run setup again.');
  if (result.status !== 200) fail(`Sesn did not accept the user list (${result.status}): ${result.json.error ?? ''}`);
  return { viewers, tracked: result.json.tracked ?? [] };
}

async function applyNotifier(config, tracked, newlyCreated = false) {
  await assertNotifierOwnership(config, newlyCreated);
  // Only tracked users trigger playback; viewer discovery is reported separately.
  // An empty list uses an impossible id so nothing is sent until you choose.
  const conditions = [
    { parameter: 'user_id', operator: 'is', value: tracked.length ? tracked : ['-1'], type: 'int' },
    { parameter: 'media_type', operator: 'is', value: ['movie', 'episode'], type: 'str' },
  ];
  const params = {
    notifier_id: config.notifierId, agent_id: WEBHOOK_AGENT_ID, friendly_name: 'Sesn',
    webhook_hook: webhook(config), webhook_method: 'POST',
    custom_conditions: JSON.stringify(conditions), custom_conditions_logic: '{1} and {2}',
  };
  for (const trigger of TRIGGERS) {
    params[trigger] = 1;
    params[`${trigger}_subject`] = JSON.stringify({ 'Content-Type': 'application/json', 'X-Api-Key': config.sesnKey });
    params[`${trigger}_body`] = BODY;
  }
  await tautulli(config, 'set_notifier_config', params, 'POST');
}

async function setup() {
  const saved = load();
  assertSavedBindings(saved);
  if (saved.notifierId) assertNotifierProof(saved);
  const config = await ask(saved);
  const identity = await tautulli(config, 'get_server_identity');
  const info = await tautulli(config, 'get_server_info').catch(() => ({}));
  const serverId = (Array.isArray(identity) ? identity[0] : identity)?.machine_identifier;
  if (config.serverId && config.serverId !== serverId) fail('Tautulli now reports a different Plex server. Restore the original server before continuing.');
  config.serverId = serverId;
  config.serverName = info?.pms_name || 'Plex';
  if (!config.serverId) fail('Tautulli did not report a Plex server id. Check Tautulli is connected to Plex.');
  say(`Connected to Tautulli for “${config.serverName}”.`);
  if (config.notifierId && !config.sesnKey) fail('A previous removal is incomplete. Run remove again or review the remaining agent manually before setup.');
  if (config.notifierId) await assertNotifierOwnership(config);
  if (!config.sesnKey) { config.sesnKey = await pair(config.serverName); config.sesnOrigin = SESN; save(config); say('Paired with Sesn.'); }
  const { viewers, tracked } = await report(config);
  let newlyCreated = false;
  if (!config.notifierId) {
    const added = await tautulli(config, 'add_notifier_config', { agent_id: WEBHOOK_AGENT_ID }, 'POST');
    config.notifierId = added?.notifier_id ?? added;
    if (!validId(config.notifierId)) fail('Tautulli did not return a valid new agent id.');
    config.notifierProof = notifierProof(config);
    save(config);
    newlyCreated = true;
  }
  await applyNotifier(config, tracked, newlyCreated);
  save(config);
  say('', `Reported ${viewers.length} Tautulli users to Sesn. ${tracked.length} ${tracked.length === 1 ? 'is' : 'are'} tracked right now.`,
    `Choose who is tracked at ${SESN}/connections/tautulli, then run:  node sesn-tautulli.mjs sync`,
    'Run sync again whenever you change who is tracked or add a Plex user.');
}

async function sync() {
  const config = load();
  assertSavedBindings(config);
  if (!config.sesnKey || !config.notifierId) fail('Run setup first.');
  await assertNotifierOwnership(config);
  const { viewers, tracked } = await report(config);
  await applyNotifier(config, tracked);
  say(`Reported ${viewers.length} users. Tautulli now sends playback for ${tracked.length} tracked ${tracked.length === 1 ? 'user' : 'users'}.`);
}

async function remove() {
  const config = load();
  assertSavedBindings(config);
  const pending = [];
  if (config.notifierId) {
    let removed = false;
    try {
      assertNotifierProof(config);
      if (!await notifierAbsent(config)) {
        await assertNotifierOwnership(config);
        await tautulli(config, 'delete_notifier', { notifier_id: config.notifierId }, 'POST');
      }
      removed = true;
    } catch {
      // A previous attempt may have deleted it before its response was lost.
      // Only a successful explicit listing can confirm that it is absent.
      try {
        assertNotifierProof(config);
        removed = await notifierAbsent(config);
      } catch { /* retain the local cleanup handle */ }
    }
    if (removed) {
      delete config.notifierId;
      delete config.notifierProof;
      delete config.tautulliApiKey;
      delete config.tautulliKeyUrl;
      save(config);
    } else pending.push('Tautulli agent removal');
  }
  // Independent cleanup: a local Tautulli outage must not prevent revocation.
  if (config.sesnKey) {
    let revoked = false;
    try {
      const result = await sesn('/api/v1/link/revoke', {}, config);
      revoked = result.status === 401 || (result.status === 200 && result.json.ok === true);
    } catch { /* retain the key securely for an explicit retry */ }
    if (revoked) { delete config.sesnKey; save(config); }
    else pending.push('Sesn pairing revocation');
  }
  if (pending.length) {
    save(config);
    fail(`Removal is incomplete: ${pending.join(' and ')} could not be confirmed. Saved retry information is retained locally. Restore the original destinations and review the agent in Tautulli if its ownership changed; otherwise run remove again when the service is reachable.`);
  }
  save({ tautulliUrl: config.tautulliUrl });
  say('The Sesn agent is removed from Tautulli and the pairing is no longer active. Your Sesn history is unchanged.');
}

const command = process.argv[2] ?? 'setup';
if (!['setup', 'sync', 'remove'].includes(command)) fail('Usage: node sesn-tautulli.mjs [setup|sync|remove]');
try { await ({ setup, sync, remove })[command](); }
catch (error) { fail(error instanceof Error ? error.message : 'The command could not complete.'); }
