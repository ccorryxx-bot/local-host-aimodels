// local-host-aimodels: Telegram -> GitHub Actions trigger (Cloudflare Worker)
// Secrets: TG_BOT_TOKEN, TG_WEBHOOK_SECRET, TG_ALLOWED_CHAT_ID, GH_DISPATCH_PAT
// Vars:    GH_REPO, GH_WORKFLOW, GH_REF, WORKER_URL

import { MODEL_ID_RE, parseModels, pickerKeyboard, parseStartCallback } from './models.js';

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json' } });

function safeEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function tg(env, method, body = {}) {
  const res = await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/${method}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

const say = (env, chatId, text) =>
  tg(env, 'sendMessage', { chat_id: chatId, text, disable_web_page_preview: true });

const answerCb = (env, id) => tg(env, 'answerCallbackQuery', { callback_query_id: id });

// Replace a message's text and drop its inline keyboard (empty markup removes it).
const editText = (env, chatId, messageId, text) =>
  tg(env, 'editMessageText', {
    chat_id: chatId,
    message_id: messageId,
    text,
    reply_markup: { inline_keyboard: [] },
  });

function gh(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${env.GH_REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GH_DISPATCH_PAT}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'local-host-aimodels-worker',
      ...(init.headers || {}),
    },
  });
}

async function activeRun(env) {
  for (const status of ['in_progress', 'queued']) {
    const res = await gh(env, `/actions/workflows/${env.GH_WORKFLOW}/runs?status=${status}&per_page=1`);
    if (!res.ok) throw new Error(`GitHub API ${res.status}`);
    const data = await res.json();
    if (data.total_count > 0) return data.workflow_runs[0];
  }
  return null;
}

const ALLOWED_UPDATES = ['message', 'callback_query'];

async function heal(env) {
  const run = await activeRun(env);
  if (run) return { healed: false, reason: 'run active' };
  const want = `${env.WORKER_URL}/tg`;
  const info = await tg(env, 'getWebhookInfo');
  // Compare allowed_updates too: a webhook set by an older version lacks
  // callback_query, so the model picker's buttons would silently never arrive.
  const have = info.ok ? info.result.allowed_updates || [] : [];
  const upToDate = info.ok && info.result.url === want && ALLOWED_UPDATES.every((u) => have.includes(u));
  if (upToDate) return { healed: false, reason: 'webhook ok' };
  const res = await tg(env, 'setWebhook', {
    url: want,
    secret_token: env.TG_WEBHOOK_SECRET,
    allowed_updates: ALLOWED_UPDATES,
    max_connections: 1,
  });
  return { healed: !!res.ok, reason: res.ok ? 'webhook set' : res.description };
}

// The model list is read from config/models.json on every /start, so the picker
// can never drift from what the workflow will actually accept.
async function loadModels(env) {
  const res = await gh(env, `/contents/config/models.json?ref=${encodeURIComponent(env.GH_REF)}`, {
    headers: { Accept: 'application/vnd.github.raw+json' },
  });
  if (!res.ok) throw new Error(`models.json: GitHub ${res.status}`);
  return parseModels(await res.json());
}

// Dispatch the host workflow. Resolves to the text to show the owner; never throws.
async function startRun(env, model) {
  try {
    if (await activeRun(env)) return 'Already running.';
    const res = await gh(env, `/actions/workflows/${env.GH_WORKFLOW}/dispatches`, {
      method: 'POST',
      body: JSON.stringify({ ref: env.GH_REF, inputs: { model } }),
    });
    if (res.status !== 204) return `Dispatch failed: GitHub ${res.status}.`;
    return `Starting ${model}. ETA 3-5 min; a model's first run also downloads it (+3-6 min).`;
  } catch (e) {
    return `Error: ${e.message}`;
  }
}

async function showPicker(env, chatId) {
  if (await activeRun(env)) return say(env, chatId, 'Already running.');
  let items;
  try {
    items = await loadModels(env);
  } catch (e) {
    return say(env, chatId, `Model list unavailable (${e.message}). Use /start <model>.`);
  }
  if (!items.length) return say(env, chatId, 'No models configured.');
  return tg(env, 'sendMessage', { chat_id: chatId, text: 'Select model.', reply_markup: pickerKeyboard(items) });
}

async function handleCommand(env, chatId, text) {
  const [head, arg] = text.trim().split(/\s+/);
  const cmd = head.split('@')[0].toLowerCase();
  try {
    if (cmd === '/start') {
      const model = (arg || '').toLowerCase();
      if (!model) return showPicker(env, chatId); // no model named: ask, never start
      if (!MODEL_ID_RE.test(model)) return say(env, chatId, 'Invalid model id. Usage: /start [model]');
      return say(env, chatId, await startRun(env, model)); // explicit id stays a shortcut
    }
    if (cmd === '/status') {
      const run = await activeRun(env);
      if (!run) return say(env, chatId, 'Offline.');
      return say(env, chatId, `${run.status === 'queued' ? 'Queued' : 'Running'} since ${run.created_at}.`);
    }
    if (cmd === '/stop') {
      const run = await activeRun(env);
      if (!run) return say(env, chatId, 'Offline. Nothing to stop.');
      const res = await gh(env, `/actions/runs/${run.id}/cancel`, { method: 'POST' });
      return say(env, chatId, res.status === 202 ? 'Stopping.' : `Cancel failed: GitHub ${res.status}.`);
    }
    return say(env, chatId, 'Commands: /start [model] /stop /status');
  } catch (e) {
    return say(env, chatId, `Error: ${e.message}`);
  }
}

async function handleCallback(env, cq) {
  const model = parseStartCallback(cq.data);
  await answerCb(env, cq.id); // stop the button spinner whatever happens next
  if (!model) return;
  const { chat, message_id } = cq.message;
  // Lock the picker first (keyboard removed) so a second tap can't dispatch again.
  await editText(env, chat.id, message_id, `Starting ${model}...`);
  await editText(env, chat.id, message_id, await startRun(env, model));
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token');

    if (request.method === 'POST' && url.pathname === '/tg') {
      if (!safeEqual(secret, env.TG_WEBHOOK_SECRET)) return new Response('forbidden', { status: 403 });
      let update;
      try { update = await request.json(); } catch { return json({ ok: true }); }
      const cq = update.callback_query;
      if (cq) {
        const chat = cq.message && cq.message.chat;
        if (!chat || String(chat.id) !== String(env.TG_ALLOWED_CHAT_ID)) return json({ ok: true });
        ctx.waitUntil(handleCallback(env, cq));
        return json({ ok: true });
      }
      const msg = update.message;
      if (!msg || !msg.text) return json({ ok: true });
      if (String(msg.chat.id) !== String(env.TG_ALLOWED_CHAT_ID)) return json({ ok: true });
      ctx.waitUntil(handleCommand(env, msg.chat.id, msg.text));
      return json({ ok: true });
    }

    if (request.method === 'POST' && url.pathname === '/heal') {
      if (!safeEqual(secret, env.TG_WEBHOOK_SECRET)) return new Response('forbidden', { status: 403 });
      try { return json(await heal(env)); } catch (e) { return json({ error: e.message }, 500); }
    }

    if (request.method === 'GET' && url.pathname === '/') return new Response('ok');
    return new Response('not found', { status: 404 });
  },

  async scheduled(_event, env, ctx) {
    ctx.waitUntil(heal(env).catch(() => {}));
  },
};
