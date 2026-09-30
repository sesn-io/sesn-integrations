#!/usr/bin/env node
// Sesn configurator for Tautulli — https://sesn.io/connections/tautulli
//
// Runs on your own network. It pairs with Sesn using a short code, reports your
// Tautulli (Plex) users so you can choose on sesn.io who is tracked, and creates
// one Sesn webhook agent in Tautulli that only fires for those users.
//
// Your Tautulli API key never leaves this machine: it is used only to talk to
// Tautulli directly and is stored in sesn-tautulli.json next to this script.
//
//   node sesn-tautulli.mjs setup    pair, report users, create the webhook agent
//   node sesn-tautulli.mjs sync     re-report users and update who is sent
//   node sesn-tautulli.mjs remove   delete the webhook agent and revoke the pairing
//
// Unattended: set TAUTULLI_URL and TAUTULLI_API_KEY instead of answering prompts.
//
// Requires Node.js 18 or newer. No dependencies.
import { readFileSync, writeFileSync, existsSync, chmodSync } from 'node:fs';
import { createInterface } from 'node:readline/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const VERSION = '1.1.0';
const CONFIG = join(dirname(fileURLToPath(import.meta.url)), 'sesn-tautulli.json');
const SESN = (process.env.SESN_URL ?? 'https://sesn.io').replace(/\/+$/, '');
const WEBHOOK_AGENT_ID = 25; // Tautulli's Webhook notification agent
const TRIGGERS = ['on_play', 'on_pause', 'on_resume', 'on_stop', 'on_watched'];
// Same data Sesn's manual Tautulli setup uses; {action} tells play/pause/stop/watched apart.
// `play` is how it was played (device, time played, quality) for your Sesn stats.
// No IP address or location is requested, so Tautulli never sends them.
const BODY = JSON.stringify({
  event: '{action}', media_type: '{media_type}', title: '{title}', show_name: '{show_name}', year: '{year}',
  show_year: '{show_year}', season: '{season_num}', episode: '{episode_num}', tmdb_id: '{themoviedb_id}',
  tvdb_id: '{thetvdb_id}', imdb_id: '{imdb_id}', progress_percent: '{progress_percent}', user_id: '{user_id}',
  username: '{username}', server_id: '{server_machine_id}', unixtime: '{unixtime}',
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
const load = () => (existsSync(CONFIG) ? JSON.parse(readFileSync(CONFIG, 'utf8')) : {});
const save = (config) => { writeFileSync(CONFIG, `${JSON.stringify(config, null, 2)}\n`); try { chmodSync(CONFIG, 0o600); } catch { /* Windows */ } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function tautulli(config, cmd, params = {}, method = 'GET') {
  const url = new URL(`${config.tautulliUrl}/api/v2`);
  url.searchParams.set('cmd', cmd);
  const body = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]));
  if (method === 'GET') for (const [k, v] of body) url.searchParams.set(k, v);
  const response = await fetch(url, {
    method, headers: { 'X-Api-Key': config.tautulliApiKey, ...(method === 'POST' ? { 'content-type': 'application/x-www-form-urlencoded' } : {}) },
    body: method === 'POST' ? body : undefined, signal: AbortSignal.timeout(15000),
  }).catch((error) => fail(`Could not reach Tautulli at ${config.tautulliUrl} (${error.message}).`));
  const json = await response.json().catch(() => null);
  const out = json?.response;
  if (!response.ok || out?.result !== 'success') fail(`Tautulli ${cmd} failed: ${out?.message ?? response.status}`);
  return out.data;
}

async function sesn(path, body, key) {
  const response = await fetch(`${SESN}${path}`, {
    method: 'POST', signal: AbortSignal.timeout(15000),
    headers: { 'content-type': 'application/json', 'user-agent': `Sesn-Tautulli/${VERSION}`, ...(key ? { 'x-api-key': key } : {}) },
    body: JSON.stringify(body ?? {}),
  }).catch((error) => fail(`Could not reach Sesn (${error.message}).`));
  const json = await response.json().catch(() => ({}));
  return { status: response.status, json };
}

async function ask(config) {
  // Environment variables allow unattended runs; otherwise prompt line by line
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
  return { ...config, tautulliUrl: url.replace(/\/+$/, ''), tautulliApiKey: key };
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
  }, config.sesnKey);
  if (result.status === 401) fail('Sesn no longer accepts this pairing. Run setup again.');
  if (result.status !== 200) fail(`Sesn did not accept the user list (${result.status}): ${result.json.error ?? ''}`);
  return { viewers, tracked: result.json.tracked ?? [] };
}

async function applyNotifier(config, tracked) {
  // Only tracked users trigger the agent; everyone else never leaves Tautulli.
  // An empty list uses an impossible id so nothing is sent until you choose.
  const conditions = [
    { parameter: 'user_id', operator: 'is', value: tracked.length ? tracked : ['-1'], type: 'int' },
    { parameter: 'media_type', operator: 'is', value: ['movie', 'episode'], type: 'str' },
  ];
  const params = {
    notifier_id: config.notifierId, agent_id: WEBHOOK_AGENT_ID, friendly_name: 'Sesn',
    webhook_hook: `${SESN}/api/v1/webhooks/tautulli`, webhook_method: 'POST',
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
  let config = await ask(load());
  const identity = await tautulli(config, 'get_server_identity');
  const info = await tautulli(config, 'get_server_info').catch(() => ({}));
  config.serverId = (Array.isArray(identity) ? identity[0] : identity)?.machine_identifier;
  config.serverName = info?.pms_name || 'Plex';
  if (!config.serverId) fail('Tautulli did not report a Plex server id. Check Tautulli is connected to Plex.');
  say(`Connected to Tautulli for “${config.serverName}”.`);
  if (!config.sesnKey) { config.sesnKey = await pair(config.serverName); save(config); say('Paired with Sesn.'); }
  if (!config.notifierId) {
    const added = await tautulli(config, 'add_notifier_config', { agent_id: WEBHOOK_AGENT_ID }, 'POST');
    config.notifierId = added?.notifier_id ?? added;
    if (!config.notifierId) fail('Tautulli did not return the new agent id.');
    save(config);
  }
  const { viewers, tracked } = await report(config);
  await applyNotifier(config, tracked);
  save(config);
  say('', `Reported ${viewers.length} Tautulli users to Sesn. ${tracked.length} ${tracked.length === 1 ? 'is' : 'are'} tracked right now.`,
    `Choose who is tracked at ${SESN}/connections/tautulli, then run:  node sesn-tautulli.mjs sync`,
    'Run sync again whenever you change who is tracked or add a Plex user.');
}

async function sync() {
  const config = load();
  if (!config.sesnKey || !config.notifierId) fail('Run setup first.');
  const { viewers, tracked } = await report(config);
  await applyNotifier(config, tracked);
  say(`Reported ${viewers.length} users. Tautulli now sends playback for ${tracked.length} tracked ${tracked.length === 1 ? 'user' : 'users'}.`);
}

async function remove() {
  const config = load();
  if (config.notifierId) await tautulli(config, 'delete_notifier', { notifier_id: config.notifierId }, 'POST');
  if (config.sesnKey) await sesn('/api/v1/link/revoke', {}, config.sesnKey);
  save({ tautulliUrl: config.tautulliUrl });
  say('Removed the Sesn agent from Tautulli and revoked the pairing. Your Sesn history is unchanged.');
}

const command = process.argv[2] ?? 'setup';
if (!['setup', 'sync', 'remove'].includes(command)) fail('Usage: node sesn-tautulli.mjs [setup|sync|remove]');
await ({ setup, sync, remove })[command]();
