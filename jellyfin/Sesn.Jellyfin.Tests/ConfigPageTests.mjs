import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const html = readFileSync(fileURLToPath(new URL('../Sesn.Jellyfin/Configuration/configPage.html', import.meta.url)), 'utf8');
const script = html.match(/<script type="text\/javascript">([\s\S]*?)<\/script>/)[1];

async function render(config) {
  const elements = new Map();
  const timers = new Map();
  let nextTimer = 0;
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      style: {}, listeners: {}, options: [], selectedIndex: 0, textContent: '',
      addEventListener(name, callback) { this.listeners[name] = callback; },
      appendChild(option) { this.options.push(option); },
    });
    return elements.get(selector);
  };
  const context = {
    document: { querySelector: element, createElement: () => ({}) },
    ApiClient: { getUsers: async () => [], getPluginConfiguration: async () => config },
    Dashboard: { showLoadingMsg() {}, hideLoadingMsg() {} },
    setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  vm.runInNewContext(script, context);
  element('#SesnConfigPage').listeners.pageshow();
  await new Promise(resolve => setImmediate(resolve));
  return { element, timers };
}

test('real config script shows bounded completion states and confirmed acceptance', async () => {
  const { element, timers } = await render({ PairingStatus: 'connected', HasCredential: true,
    PendingCompletions: 3, HeldCompletions: 2, FailedCompletions: 1, ExpiredCompletions: 4,
    LastDeliveredAtUtc: '2026-10-10T00:00:00Z', LastCompletionStatus: 'Original viewer permission changed.' });
  assert.equal(element('#CompletionCounts').textContent, '3 pending · 2 held · 1 failed · 4 expired');
  assert.match(element('#CompletionAccepted').textContent, /^Last completed watch accepted by Sesn:/);
  assert.equal(element('#CompletionWarning').textContent, 'Original viewer permission changed.');
  assert.equal([...timers.values()][0].delay, 10000);
  element('#Enabled').checked = false;
  element('#JellyfinUserId').value = 'unsaved-viewer';
  const firstTimer = [...timers.values()][0];
  timers.clear();
  const polling = firstTimer.callback();
  await polling;
  assert.equal(element('#Enabled').checked, false);
  assert.equal(element('#JellyfinUserId').value, 'unsaved-viewer');
  // Simulate a timer firing normally: it is no longer pending while its callback runs.
  const secondTimer = [...timers.values()][0];
  timers.clear();
  const inFlight = secondTimer.callback();
  element('#SesnConfigPage').listeners.pagehide();
  await inFlight;
  assert.equal(timers.size, 0);
});

test('unconfirmed and unknown queues do not claim a successful completed watch', async () => {
  const { element } = await render({ PairingStatus: 'not_connected', HeldCompletions: 1,
    LastCompletionStatus: '<unknown legacy ownership>' });
  assert.equal(element('#CompletionAccepted').textContent, 'No completed watch has been confirmed for this connection.');
  assert.equal(element('#CompletionWarning').textContent, '<unknown legacy ownership>');
  assert.match(html, /seven days/);
  assert.match(html, /500 stored records/);
});

test('privacy copy distinguishes solo selection from household identity discovery', () => {
  assert.match(html, /Single-viewer mode sends only your selected viewer’s identity and playback/);
  assert.match(html, /Household setup shares local viewer names for mapping/);
  assert.doesNotMatch(html, /Only users you choose are sent; everyone else stays on this server/);
});

test('expired household playback permissions show a retryable pause', async () => {
  const { element } = await render({ PairingStatus: 'connected', HasCredential: true, Household: true, HouseholdPlaybackPaused: true });
  assert.equal(element('#ViewerPermissionStatus').textContent, 'Playback paused: viewer permissions need refreshing. Retrying automatically.');
  assert.match(html, /within six minutes/);
});
