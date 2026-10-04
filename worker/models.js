// Pure helpers for the model picker (no I/O), kept out of index.js so they can
// be unit-tested with `node --test` and bundled by wrangler as a plain import.

// Model ids are keys of config/models.json. Used for anything that reaches the
// workflow as an input: shape-checked here, existence is verified by the workflow.
export const MODEL_ID_RE = /^[a-z0-9][a-z0-9._-]{0,39}$/;

const START_CB_RE = /^start:([a-z0-9][a-z0-9._-]{0,39})$/;

// config/models.json -> [{ id, label }] in file order; ids that fail the shape
// check are dropped rather than rendered as buttons that could never work.
export function parseModels(cfg) {
  const models = cfg && typeof cfg.models === 'object' && cfg.models ? cfg.models : {};
  return Object.entries(models)
    .filter(([id]) => MODEL_ID_RE.test(id))
    .map(([id, m]) => ({ id, label: String((m && m.label) || id) }));
}

// One button per row: labels are long ("Gemma 4 12B QAT Abliterated (huihui-ai)")
// and two columns would truncate them on a phone.
export function pickerKeyboard(items) {
  return { inline_keyboard: items.map(({ id, label }) => [{ text: label, callback_data: `start:${id}` }]) };
}

// "start:<id>" -> id, or null for anything else.
export function parseStartCallback(data) {
  const m = START_CB_RE.exec(typeof data === 'string' ? data : '');
  return m ? m[1] : null;
}
