// Run from worker/:  node --test
// Stubs global fetch (Telegram + GitHub); no network, token or Cloudflare needed.
import test from 'node:test';
import assert from 'node:assert/strict';
import worker from './index.js';
import { parseModels, pickerKeyboard, parseStartCallback } from './models.js';

const CFG = {
  default: 'qwen2.5-7b',
  models: {
    'qwen2.5-7b': { label: 'Qwen2.5-7B Q4_K_M' },
    'gemma4-12b-abl': { label: 'Gemma 4 12B QAT Abliterated' },
    'BAD ID': { label: 'never shown' },
  },
};
const ENV = {
  TG_BOT_TOKEN: 't', TG_WEBHOOK_SECRET: 's', TG_ALLOWED_CHAT_ID: '42', GH_DISPATCH_PAT: 'p',
  GH_REPO: 'o/r', GH_WORKFLOW: 'host.yml', GH_REF: 'main', WORKER_URL: 'https://w.example',
};

// Records every outbound call; `opts.running` fakes an active run, `opts.webhook` the webhook info.
function setup(opts = {}) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    const body = init.body ? JSON.parse(init.body) : undefined;
    calls.push({ url: String(url), body });
    const u = String(url);
    if (u.includes('/actions/workflows/host.yml/runs')) {
      const hit = opts.running && u.includes('status=in_progress');
      return Response.json({ total_count: hit ? 1 : 0, workflow_runs: hit ? [{ id: 9, status: 'in_progress' }] : [] });
    }
    if (u.includes('/contents/config/models.json')) return opts.noModels ? new Response('x', { status: 404 }) : Response.json(CFG);
    if (u.endsWith('/dispatches')) return new Response(null, { status: 204 });
    if (u.endsWith('/getWebhookInfo')) return Response.json({ ok: true, result: opts.webhook || {} });
    return Response.json({ ok: true, result: {} });
  };
  return calls;
}

async function send(update, headers = { 'X-Telegram-Bot-Api-Secret-Token': 's' }) {
  const pending = [];
  const res = await worker.fetch(
    new Request('https://w.example/tg', { method: 'POST', headers, body: JSON.stringify(update) }),
    ENV,
    { waitUntil: (p) => pending.push(p) },
  );
  await Promise.all(pending);
  return res;
}

const msg = (text, chat = 42) => ({ message: { text, chat: { id: chat } } });
const tap = (data, chat = 42) => ({
  callback_query: { id: 'cb1', data, message: { message_id: 7, chat: { id: chat } } },
});
const tgCalls = (calls, method) => calls.filter((c) => c.url.endsWith(`/${method}`));
const dispatches = (calls) => calls.filter((c) => c.url.endsWith('/dispatches'));

test('/start without a model shows the picker and does NOT dispatch', async () => {
  const calls = setup();
  await send(msg('/start'));
  assert.equal(dispatches(calls).length, 0);
  const [m] = tgCalls(calls, 'sendMessage');
  assert.equal(m.body.text, 'Select model.');
  const buttons = m.body.reply_markup.inline_keyboard.flat();
  assert.deepEqual(buttons.map((b) => b.callback_data), ['start:qwen2.5-7b', 'start:gemma4-12b-abl']);
  assert.equal(buttons[1].text, 'Gemma 4 12B QAT Abliterated');
});

test('tapping a button dispatches the workflow with that model and locks the picker', async () => {
  const calls = setup();
  await send(tap('start:gemma4-12b-abl'));
  assert.equal(tgCalls(calls, 'answerCallbackQuery').length, 1);
  const [d] = dispatches(calls);
  assert.deepEqual(d.body, { ref: 'main', inputs: { model: 'gemma4-12b-abl' } });
  const edits = tgCalls(calls, 'editMessageText');
  assert.equal(edits[0].body.text, 'Starting gemma4-12b-abl...');
  assert.deepEqual(edits[0].body.reply_markup, { inline_keyboard: [] }); // keyboard removed first
  assert.match(edits[1].body.text, /^Starting gemma4-12b-abl\. ETA 3-5 min/);
});

test('tap while a run is active: no dispatch, says so', async () => {
  const calls = setup({ running: true });
  await send(tap('start:qwen2.5-7b'));
  assert.equal(dispatches(calls).length, 0);
  assert.equal(tgCalls(calls, 'editMessageText').at(-1).body.text, 'Already running.');
});

test('/start while a run is active: no picker, no dispatch', async () => {
  const calls = setup({ running: true });
  await send(msg('/start'));
  assert.equal(dispatches(calls).length, 0);
  assert.equal(tgCalls(calls, 'sendMessage')[0].body.text, 'Already running.');
});

test('/start <id> still starts directly (shortcut)', async () => {
  const calls = setup();
  await send(msg('/start padauk'));
  assert.deepEqual(dispatches(calls)[0].body, { ref: 'main', inputs: { model: 'padauk' } });
});

test('/start with an invalid id is rejected before any GitHub call', async () => {
  const calls = setup();
  await send(msg('/start BAD;ID'));
  assert.equal(dispatches(calls).length, 0);
  assert.match(tgCalls(calls, 'sendMessage')[0].body.text, /^Invalid model id/);
});

test('picker falls back to a hint when models.json cannot be read', async () => {
  const calls = setup({ noModels: true });
  await send(msg('/start'));
  assert.equal(dispatches(calls).length, 0);
  assert.match(tgCalls(calls, 'sendMessage')[0].body.text, /Model list unavailable.*Use \/start <model>/);
});

test('strangers get nothing: no Telegram or GitHub calls', async () => {
  const calls = setup();
  await send(msg('/start', 999));
  await send(tap('start:qwen2.5-7b', 999));
  assert.equal(calls.length, 0);
});

test('wrong webhook secret is rejected', async () => {
  setup();
  const res = await send(msg('/start'), { 'X-Telegram-Bot-Api-Secret-Token': 'nope' });
  assert.equal(res.status, 403);
});

test('garbage callback data is acknowledged but starts nothing', async () => {
  const calls = setup();
  await send(tap('start:../../etc'));
  assert.equal(tgCalls(calls, 'answerCallbackQuery').length, 1);
  assert.equal(dispatches(calls).length, 0);
});

test('heal re-sets a webhook that lacks callback_query', async () => {
  const calls = setup({ webhook: { url: 'https://w.example/tg', allowed_updates: ['message'] } });
  const res = await worker.fetch(
    new Request('https://w.example/heal', { method: 'POST', headers: { 'X-Telegram-Bot-Api-Secret-Token': 's' } }),
    ENV, { waitUntil() {} },
  );
  assert.equal((await res.json()).reason, 'webhook set');
  assert.deepEqual(tgCalls(calls, 'setWebhook')[0].body.allowed_updates, ['message', 'callback_query']);
});

test('heal leaves an up-to-date webhook alone', async () => {
  const calls = setup({ webhook: { url: 'https://w.example/tg', allowed_updates: ['message', 'callback_query'] } });
  const res = await worker.fetch(
    new Request('https://w.example/heal', { method: 'POST', headers: { 'X-Telegram-Bot-Api-Secret-Token': 's' } }),
    ENV, { waitUntil() {} },
  );
  assert.equal((await res.json()).reason, 'webhook ok');
  assert.equal(tgCalls(calls, 'setWebhook').length, 0);
});

test('models helpers: parse, keyboard, callback parsing', () => {
  const items = parseModels(CFG);
  assert.deepEqual(items.map((i) => i.id), ['qwen2.5-7b', 'gemma4-12b-abl']);
  assert.equal(pickerKeyboard(items).inline_keyboard.length, 2);
  assert.equal(parseStartCallback('start:padauk'), 'padauk');
  assert.equal(parseStartCallback('stop:padauk'), null);
  assert.equal(parseStartCallback(undefined), null);
  assert.deepEqual(parseModels(null), []);
});
