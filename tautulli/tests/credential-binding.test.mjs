import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const source = await readFile(process.env.SESN_TAUTULLI_TEST_SOURCE
  || new URL('../sesn-tautulli.mjs', import.meta.url), 'utf8');
const origin = 'https://sesn.example';
const base = 'http://tautulli.example:8181/base';
const hook = `${origin}/api/v1/webhooks/tautulli`;
const key = 'synthetic-sesn-secret';
const localKey = 'synthetic-tautulli-secret';
const hash = createHash('sha256').update(key).digest('hex');
const initial = () => ({
  tautulliUrl: base, tautulliKeyUrl: base, tautulliApiKey: localKey,
  sesnOrigin: origin, sesnKey: key, serverId: 'fixture-server', serverName: 'Fixture Plex', notifierId: 77,
  notifierProof: { notifierId: '77', tautulliUrl: base, serverId: 'fixture-server', hook, credentialHash: hash },
});
const ownedAgent = () => ({
  id: 77, agent_id: 25, friendly_name: 'Renamed by member', config: { hook, method: 'POST' },
  actions: { on_watched: 1, on_play: 0 },
  notify_text: { on_watched: { subject: JSON.stringify({ 'X-Api-Key': key }), body: '{}' }, on_play: { subject: '', body: '' } },
});
const ok = data => Response.json({ response: { result: 'success', data } });

async function run({ config = initial(), command = 'sync', args = [], env = {}, agent = ownedAgent(), reply } = {}) {
  let saved = structuredClone(config), code = 0, saves = 0;
  const calls = [], messages = [];
  const names = ['readFileSync', 'writeFileSync', 'existsSync', 'chmodSync', 'dirname', 'join', 'fileURLToPath', 'createHash', 'createInterface', 'process', 'console', 'fetch', 'setTimeout'];
  const values = [() => JSON.stringify(saved), (_path, value) => { saved = JSON.parse(value); saves++; },
    () => true, () => {}, dirname, join, fileURLToPath, createHash,
    () => { throw Error('Unexpected interactive prompt'); },
    { argv: ['node', 'script', command, ...args], env: { SESN_URL: origin, TAUTULLI_URL: base, TAUTULLI_API_KEY: localKey, ...env },
      exit(n) { const error = Error('exit'); error.exitCode = n; throw error; } },
    { log: (...lines) => messages.push(lines.join(' ')), error: (...lines) => messages.push(lines.join(' ')), warn: (...lines) => messages.push(lines.join(' ')) },
    async (input, options) => {
      const url = new URL(input), cmd = url.searchParams.get('cmd');
      const call = { origin: url.origin, path: url.pathname, cmd, options, fields: Object.fromEntries(new URLSearchParams(options.body)) };
      calls.push(call);
      const response = await reply?.(call);
      if (response) return response;
      if (cmd === 'get_server_identity') return ok({ machine_identifier: 'fixture-server' });
      if (cmd === 'get_server_info') return ok({ pms_name: 'Fixture Plex' });
      if (cmd === 'get_users') return ok([{ user_id: 11, friendly_name: 'Fixture viewer', is_active: 1 }]);
      if (cmd === 'get_notifiers') return ok(agent ? [{ id: agent.id, agent_id: agent.agent_id }] : []);
      if (cmd === 'get_notifier_config') return ok(agent);
      if (cmd === 'add_notifier_config') {
        agent = { id: 77, agent_id: 25, config: { hook: '', method: 'POST' }, actions: { on_watched: 0 }, notify_text: { on_watched: { subject: '', body: '' } } };
        return ok(77);
      }
      if (cmd === 'set_notifier_config') return ok(true);
      if (cmd === 'delete_notifier') { agent = null; return ok(true); }
      if (url.pathname === '/api/v1/link/new') return Response.json({ device_code: 'fixture-code', user_code_display: 'FIXTURE', expires_in: 30 });
      if (url.pathname === '/api/v1/link/poll') return Response.json({ status: 'authorized', api_key: key });
      if (url.pathname === '/api/v1/connections/viewers') return Response.json({ tracked: ['11'] });
      if (url.pathname === '/api/v1/link/revoke') return Response.json({ ok: true });
      throw Error('Unexpected fixture request');
    }, callback => queueMicrotask(callback)];
  const body = source.replace(/^#!.*\n/, '').replace(/^import .*;\n/gm, '').replaceAll('import.meta.url', JSON.stringify('file:///fixture/sesn-tautulli.mjs'));
  try { await new (Object.getPrototypeOf(async () => {}).constructor)(...names, body)(...values); }
  catch (error) { if (error.exitCode !== undefined) code = error.exitCode; else throw error; }
  assert.doesNotMatch(messages.join('\n'), /synthetic-(?:sesn|tautulli)-secret/);
  return { config: saved, code, saves, calls, text: messages.join('\n'), writes: calls.filter(call => ['set_notifier_config', 'add_notifier_config', 'delete_notifier'].includes(call.cmd)) };
}

for (const command of ['setup', 'sync', 'remove']) {
  test(`${command} refuses a changed Sesn origin before any request`, async () => {
    for (const value of ['https://other.example', 'https://sesn.example:9443', 'http://localhost:8181']) {
      const result = await run({ command, env: { SESN_URL: value } });
      assert.equal(result.code, 1);
      assert.match(result.text, /origin/);
      assert.deepEqual(result.calls, []);
      assert.equal(result.saves, 0);
    }
  });
  test(`${command} refuses unbound keys and requires manual cleanup followed by fresh setup`, async () => {
    const config = initial();
    delete config.sesnOrigin; delete config.tautulliKeyUrl; delete config.notifierProof;
    const result = await run({ config, command });
    assert.equal(result.code, 1);
    assert.match(result.text, /manually/i);
    assert.match(result.text, /fresh pairing/i);
    assert.deepEqual(result.calls, []);
    assert.deepEqual(result.config, config);
  });
  test(`${command} refuses an edited Tautulli endpoint before sending the saved key`, async () => {
    for (const tautulliUrl of ['https://tautulli.example:8181/base', 'http://other.example:8181/base', 'http://tautulli.example:8182/base', 'http://tautulli.example:8181/other']) {
      const result = await run({ config: { ...initial(), tautulliUrl }, command });
      assert.equal(result.code, 1);
      assert.deepEqual(result.calls, []);
    }
  });
}

test('setup cannot carry a saved Tautulli key or notifier to a new environment URL', async () => {
  const result = await run({ command: 'setup', env: { TAUTULLI_URL: 'http://different.example:8181' } });
  assert.equal(result.code, 1);
  assert.deepEqual(result.calls, []);
});

test('origin parsing rejects URL credentials and discarded path, query or fragment without echoing secrets', async () => {
  for (const SESN_URL of ['https://synthetic-sesn-secret@sesn.example', `${origin}/different`, `${origin}?key=${key}`, `${origin}#${key}`]) {
    const result = await run({ env: { SESN_URL } });
    assert.equal(result.code, 1);
    assert.deepEqual(result.calls, []);
  }
});

test('there is no command to adopt or migrate existing unbound keys', async () => {
  const config = initial();
  delete config.sesnOrigin; delete config.tautulliKeyUrl; delete config.notifierProof;
  for (const args of [[], ['--sesn-origin', origin]]) {
    const result = await run({ command: 'migrate', args, config });
    assert.equal(result.code, 1);
    assert.match(result.text, /Usage:.*\[setup\|sync\|remove\]/);
    assert.equal(result.saves, 0);
    assert.deepEqual(result.config, config);
    assert.deepEqual(result.calls, []);
  }
});

test('unbound partial cleanup cannot reconstruct ownership or transmit saved credentials', async () => {
  const config = initial();
  delete config.sesnOrigin; delete config.notifierProof; delete config.sesnKey;
  const result = await run({ command: 'remove', config });
  assert.equal(result.code, 1);
  assert.match(result.text, /manually/i);
  assert.deepEqual(result.config, config);
  assert.deepEqual(result.calls, []);
});

test('fresh setup binds both keys and records a non-secret ownership fingerprint', async () => {
  const result = await run({ config: {}, command: 'setup', agent: null });
  assert.equal(result.code, 0, result.text);
  assert.equal(result.config.sesnOrigin, origin);
  assert.equal(result.config.tautulliKeyUrl, base);
  assert.deepEqual(result.config.notifierProof, initial().notifierProof);
  assert.doesNotMatch(JSON.stringify(result.config.notifierProof), /synthetic-.*-secret/);
  assert.deepEqual(result.writes.map(call => call.cmd), ['add_notifier_config', 'set_notifier_config']);
});

test('owned renamed agents can sync, with redirects disabled on both transports', async () => {
  const result = await run();
  assert.equal(result.code, 0, result.text);
  assert.equal(result.writes.length, 1);
  assert.ok(result.calls.every(call => call.options.redirect === 'error'));
  for (const call of result.calls) {
    if (call.cmd) assert.equal(call.options.headers['X-Api-Key'], localKey);
    else assert.equal(call.options.headers['x-api-key'], key);
  }
});

test('equivalent canonical origin spellings retain a valid existing binding', async () => {
  const result = await run({ env: { SESN_URL: 'https://SESN.EXAMPLE:443/' } });
  assert.equal(result.code, 0, result.text);
  assert.equal(result.writes.length, 1);
  assert.ok(result.calls.filter(call => !call.cmd).every(call => call.origin === origin));
});

const unsafeAgents = [
  ['reused ID', agent => { agent.id = 88; }],
  ['different agent type', agent => { agent.agent_id = 10; }],
  ['unrelated destination named Sesn', agent => { agent.friendly_name = 'Sesn'; agent.config.hook = 'https://other.example/hook'; }],
  ['different method', agent => { agent.config.method = 'GET'; }],
  ['same destination with another credential', agent => { agent.notify_text.on_watched.subject = JSON.stringify({ 'X-Api-Key': 'another-account' }); }],
  ['mixed credentials', agent => { agent.actions.on_play = 1; agent.notify_text.on_play.subject = JSON.stringify({ 'X-Api-Key': 'another-account' }); }],
  ['disabled trigger carrying another credential', agent => { agent.notify_text.on_play.subject = JSON.stringify({ 'X-Api-Key': 'another-account' }); }],
  ['ambiguous credential headers', agent => { agent.notify_text.on_watched.subject = JSON.stringify({ 'X-Api-Key': key, 'x-api-key': 'another-account' }); }],
  ['malformed headers', agent => { agent.notify_text.on_watched.subject = '{'; }],
  ['missing actions', agent => { delete agent.actions; }],
  ['unknown extra credential text', agent => { agent.notify_text.on_extra = { subject: JSON.stringify({ 'X-Api-Key': 'another-account' }) }; }],
  ['saved empty agent', agent => { agent.config.hook = ''; agent.actions.on_watched = 0; agent.notify_text.on_watched = { subject: '', body: '' }; }],
];
for (const [name, change] of unsafeAgents) {
  test(`${name} never authorizes update or deletion`, async () => {
    for (const command of ['setup', 'sync', 'remove']) {
      const agent = ownedAgent(); change(agent);
      const result = await run({ command, agent, reply: call => call.cmd === 'get_notifiers' ? ok([{ id: 77, agent_id: 25 }]) : undefined });
      assert.equal(result.code, 1, `${command}: ${result.text}`);
      assert.deepEqual(result.writes, []);
      if (command === 'remove') {
        assert.equal(result.config.notifierId, 77);
        assert.deepEqual(result.config.notifierProof, initial().notifierProof);
        assert.equal(result.config.sesnKey, undefined);
        assert.ok(result.calls.some(call => call.path === '/api/v1/link/revoke'));
      }
    }
  });
}

test('mismatched saved notifier provenance never authorizes a remote mutation', async () => {
  for (const changes of [{ notifierId: 88 }, { serverId: 'different-server' }, { notifierProof: undefined }, { notifierProof: { ...initial().notifierProof, credentialHash: '0'.repeat(64) } }]) {
    const result = await run({ config: { ...initial(), ...changes } });
    assert.equal(result.code, 1);
    assert.deepEqual(result.writes, []);
  }
});

test('changed Plex server identity blocks notifier changes and viewer reporting', async () => {
  for (const command of ['setup', 'sync', 'remove']) {
    const result = await run({ command, reply: call => call.cmd === 'get_server_identity' ? ok({ machine_identifier: 'replacement-server' }) : undefined });
    assert.equal(result.code, 1);
    assert.deepEqual(result.writes, []);
    assert.ok(!result.calls.some(call => call.path === '/api/v1/connections/viewers'));
  }
});

test('partial removal retains the fingerprint and can prove ownership after revocation', async () => {
  const first = await run({ command: 'remove', reply: call => {
    if (call.cmd === 'delete_notifier') throw Error('offline');
  } });
  assert.equal(first.code, 1);
  assert.equal(first.config.sesnKey, undefined);
  assert.equal(first.config.notifierProof.credentialHash, hash);
  const second = await run({ command: 'remove', config: first.config });
  assert.equal(second.code, 0, second.text);
  assert.equal(second.writes[0].cmd, 'delete_notifier');
  assert.deepEqual(second.config, { tautulliUrl: base });
  assert.ok(!second.calls.some(call => call.path === '/api/v1/link/revoke'));
});

test('successful current-API listing proves an already removed agent absent without deleting anything', async () => {
  const result = await run({ command: 'remove', agent: null, reply: call => call.cmd === 'get_notifiers' ? ok([{ id: 88, agent_id: 25 }]) : undefined });
  assert.equal(result.code, 0, result.text);
  assert.deepEqual(result.writes, []);
  assert.deepEqual(result.config, { tautulliUrl: base });
});

test('malformed listing and unavailable ownership reads preserve cleanup state', async () => {
  const result = await run({ command: 'remove', reply: call => {
    if (call.cmd === 'get_notifiers') return ok([{}]);
    if (call.cmd === 'get_notifier_config') return ok(null);
  } });
  assert.equal(result.code, 1);
  assert.equal(result.config.notifierId, 77);
  assert.equal(result.config.tautulliApiKey, localKey);
  assert.equal(result.config.sesnKey, undefined);
  assert.deepEqual(result.writes, []);
});

test('an interrupted removal cannot start a new pairing or adopt its saved empty agent', async () => {
  const config = initial(); delete config.sesnKey;
  const result = await run({ command: 'setup', config });
  assert.equal(result.code, 1);
  assert.match(result.text, /removal is incomplete/i);
  assert.deepEqual(result.writes, []);
  assert.ok(!result.calls.some(call => call.path.startsWith('/api/v1/link/')));
});
