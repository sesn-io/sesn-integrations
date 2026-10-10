import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { copyFile, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';

const source = new URL('../sesn-tautulli.mjs', import.meta.url);

const run = (script, command, env) => new Promise((resolve, reject) => {
  const child = spawn(process.execPath, [script, command], { env: { ...process.env, ...env } });
  let stdout = '', stderr = '';
  child.stdout.on('data', chunk => { stdout += chunk; });
  child.stderr.on('data', chunk => { stderr += chunk; });
  child.once('error', reject);
  child.once('close', code => resolve({ code, stdout, stderr }));
});

test('Tautulli configurator completes setup, sync and removal without exposing its local API key', { timeout: 15_000 }, async () => {
  const calls = [];
  let tracked = ['11'];
  let agent = { id: 77, agent_id: 25, config: { hook: '', method: 'POST' }, actions: { on_watched: 0 }, notify_text: { on_watched: { subject: '', body: '' } } };
  const server = createServer(async (request, response) => {
    const url = new URL(request.url, 'http://localhost');
    let body = '';
    for await (const chunk of request) body += chunk;
    calls.push({ path: url.pathname, cmd: url.searchParams.get('cmd'), method: request.method, headers: request.headers, body });
    response.setHeader('content-type', 'application/json');
    if (url.pathname === '/api/v2') {
      const cmd = url.searchParams.get('cmd');
      if (cmd === 'set_notifier_config') {
        const fields = new URLSearchParams(body);
        agent = { id: 77, agent_id: 25, config: { hook: fields.get('webhook_hook'), method: fields.get('webhook_method') }, actions: {}, notify_text: {} };
        for (const trigger of ['on_play', 'on_pause', 'on_resume', 'on_stop', 'on_watched']) {
          agent.actions[trigger] = Number(fields.get(trigger));
          agent.notify_text[trigger] = { subject: fields.get(`${trigger}_subject`), body: fields.get(`${trigger}_body`) };
        }
      }
      const data = cmd === 'get_server_identity' ? { machine_identifier: 'server-123' }
        : cmd === 'get_server_info' ? { pms_name: 'Living Room Plex' }
        : cmd === 'add_notifier_config' ? { notifier_id: 77 }
        : cmd === 'get_notifier_config' ? agent
        : cmd === 'get_notifiers' ? [{ id: 77, agent_id: 25 }]
        : cmd === 'get_users' ? [
          { user_id: 11, friendly_name: 'Owner', is_active: 1 },
          { user_id: 22, username: 'Untracked viewer', is_active: 1 },
          { user_id: 0, username: 'Local', is_active: 1 },
          { user_id: 33, username: 'Inactive', is_active: 0 },
        ] : {};
      response.end(JSON.stringify({ response: { result: 'success', data } }));
      return;
    }
    if (url.pathname === '/api/v1/link/new') {
      response.end(JSON.stringify({ device_code: 'device-1', user_code_display: 'ABCD', verification_url: 'https://sesn.io/link', expires_in: 30 }));
      return;
    }
    if (url.pathname === '/api/v1/link/poll') {
      response.end(JSON.stringify({ status: 'authorized', api_key: 'sesn-private-test-key' }));
      return;
    }
    if (url.pathname === '/api/v1/connections/viewers') {
      response.end(JSON.stringify({ tracked }));
      return;
    }
    if (url.pathname === '/api/v1/link/revoke') {
      response.end(JSON.stringify({ ok: true }));
      return;
    }
    response.statusCode = 404;
    response.end('{}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert(address && typeof address !== 'string');
  const origin = `http://127.0.0.1:${address.port}`;
  const directory = await mkdtemp(join(tmpdir(), 'sesn-tautulli-test-'));
  const script = join(directory, 'sesn-tautulli.mjs');
  await copyFile(source, script);
  const env = { SESN_URL: origin, TAUTULLI_URL: origin, TAUTULLI_API_KEY: 'tautulli-private-test-key' };
  try {
    const setup = await run(script, 'setup', env);
    assert.equal(setup.code, 0, setup.stderr);
    assert.match(setup.stdout, /Reported 2 Tautulli users/);
    assert.doesNotMatch(setup.stdout + setup.stderr, /private-test-key/);

    const config = JSON.parse(await readFile(join(directory, 'sesn-tautulli.json'), 'utf8'));
    assert.equal(config.serverId, 'server-123');
    assert.equal(config.notifierId, 77);
    assert.equal(config.sesnOrigin, origin);
    assert.equal(config.tautulliKeyUrl, origin);
    assert.equal(config.notifierProof.notifierId, '77');
    assert.equal(config.notifierProof.credentialHash.length, 64);
    assert.ok(!JSON.stringify(config.notifierProof).includes('sesn-private-test-key'));
    if (process.platform !== 'win32') assert.equal((await stat(join(directory, 'sesn-tautulli.json'))).mode & 0o777, 0o600);
    const notifier = calls.find(call => call.cmd === 'set_notifier_config');
    assert(notifier);
    assert.equal(notifier.method, 'POST');
    assert.equal(notifier.headers['x-api-key'], 'tautulli-private-test-key');
    assert(!notifier.path.includes('tautulli-private-test-key'));
    const notifierBody = new URLSearchParams(notifier.body);
    assert.equal(notifierBody.get('webhook_hook'), `${origin}/api/v1/webhooks/tautulli`);
    assert.deepEqual(JSON.parse(notifierBody.get('custom_conditions'))[0].value, ['11']);
    for (const trigger of ['on_play', 'on_pause', 'on_resume', 'on_stop', 'on_watched']) {
      assert.equal(notifierBody.get(trigger), '1');
      assert.match(notifierBody.get(`${trigger}_body`), /"server_id": "\{server_machine_id\}"/);
      assert.equal(JSON.parse(notifierBody.get(`${trigger}_subject`))['X-Api-Key'], 'sesn-private-test-key');
    }
    const viewerCall = calls.find(call => call.path === '/api/v1/connections/viewers');
    assert.deepEqual(JSON.parse(viewerCall.body).viewers, [{ id: '11', name: 'Owner' }, { id: '22', name: 'Untracked viewer' }]);

    tracked = [];
    const sync = await run(script, 'sync', env);
    assert.equal(sync.code, 0, sync.stderr);
    assert.match(sync.stdout, /sends playback for 0 tracked users/);
    const syncedNotifier = calls.filter(call => call.cmd === 'set_notifier_config').at(-1);
    assert.deepEqual(JSON.parse(new URLSearchParams(syncedNotifier.body).get('custom_conditions'))[0].value, ['-1']);

    const remove = await run(script, 'remove', env);
    assert.equal(remove.code, 0, remove.stderr);
    assert(calls.some(call => call.cmd === 'delete_notifier'));
    assert(calls.some(call => call.path === '/api/v1/link/revoke'));
  } finally {
    server.close();
    await rm(directory, { recursive: true, force: true });
  }
});

test('credential-bearing requests never reach a redirect destination on either service', { timeout: 15_000 }, async () => {
  let redirectedRequests = 0;
  const destination = createServer((_request, response) => { redirectedRequests++; response.end('{}'); });
  await new Promise(resolve => destination.listen(0, '127.0.0.1', resolve));
  const destinationOrigin = `http://127.0.0.1:${destination.address().port}`;
  const server = createServer((_request, response) => {
    response.writeHead(307, { location: `${destinationOrigin}/redirected` });
    response.end();
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const directory = await mkdtemp(join(tmpdir(), 'sesn-tautulli-redirect-'));
  const script = join(directory, 'sesn-tautulli.mjs');
  const file = join(directory, 'sesn-tautulli.json');
  await copyFile(source, script);
  const env = { SESN_URL: origin, TAUTULLI_URL: origin, TAUTULLI_API_KEY: 'tautulli-private-test-key' };
  try {
    const local = await run(script, 'setup', env);
    assert.equal(local.code, 1);
    assert.equal(redirectedRequests, 0, 'the Tautulli API key must not follow a redirect');
    await writeFile(file, JSON.stringify({ tautulliUrl: origin, sesnOrigin: origin, sesnKey: 'sesn-private-test-key' }), { mode: 0o600 });
    const remote = await run(script, 'remove', env);
    assert.equal(remote.code, 1);
    assert.equal(redirectedRequests, 0, 'the Sesn pairing key must not follow a redirect');
    assert.equal(JSON.parse(await readFile(file, 'utf8')).sesnKey, 'sesn-private-test-key');
    assert.doesNotMatch(local.stdout + local.stderr + remote.stdout + remote.stderr, /private-test-key/);
  } finally {
    server.close(); destination.close();
    await rm(directory, { recursive: true, force: true });
  }
});
