/* exoself landing: derive in the browser, prove the build against the vectors.
 *
 * The page has no dependencies and no network calls after load. The wasm module
 * has no imports; it is the same exoself-core crate the CLI uses.
 */

const $ = (sel) => document.querySelector(sel);
const enc = new TextEncoder();
const dec = new TextDecoder();

let wasm = null;
let current = { seed: null, cert: null, panes: {}, pane: 'prompt' };

/** One request in, one JSON response out. The module may grow its memory, so
 *  every view is re-read from the current buffer. */
function call(req) {
  const bytes = enc.encode(JSON.stringify(req));
  const ptr = wasm.exoself_wasm_alloc(bytes.length);
  new Uint8Array(wasm.memory.buffer, ptr, bytes.length).set(bytes);
  const res = wasm.exoself_wasm_request(ptr, bytes.length);
  const len = new DataView(wasm.memory.buffer).getUint32(res, true);
  const text = dec.decode(new Uint8Array(wasm.memory.buffer, res + 4, len));
  wasm.exoself_wasm_free(res, 4 + len);
  wasm.exoself_wasm_free(ptr, bytes.length);
  return JSON.parse(text);
}

function toHex(bytes) {
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function fromHex(hex) {
  const clean = hex.trim().toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(clean)) return null;
  const out = new Uint8Array(32);
  for (let i = 0; i < 32; i++) out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  return out;
}

function nowIso() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
}

async function boot() {
  try {
    const { instance } = await WebAssembly.instantiateStreaming(fetch('exoself_wasm.wasm'), {});
    wasm = instance.exports;
  } catch (e) {
    // Some static servers get the MIME type wrong; fall back to bytes.
    const buf = await (await fetch('exoself_wasm.wasm')).arrayBuffer();
    const { instance } = await WebAssembly.instantiate(buf, {});
    wasm = instance.exports;
  }

  const reg = call({ op: 'registry' });
  if (reg.ok) {
    $('#registry').textContent = `${reg.id} · ${reg.sha256.slice(0, 12)}…`;
  }

  wire();
  runConformance();

  const seed = seedFromHash();
  if (seed) {
    $('#seed').value = toHex(seed);
    derive(seed, { fromHash: true });
  }
}

async function runConformance() {
  const el = $('#conformance');
  try {
    const vectors = await (await fetch('vectors.json')).text();
    const r = call({ op: 'conformance', vectors, primitives: true });
    if (r.ok) {
      el.textContent = `${r.summary}, in this browser`;
      el.classList.add('good');
    } else {
      el.textContent = `${r.summary}. ${r.failures.slice(0, 2).join(' / ')}`;
      el.classList.add('bad');
    }
  } catch (e) {
    el.textContent = 'could not load spec/vectors.json';
    el.classList.add('bad');
  }
}

function seedFromHash() {
  const m = location.hash.match(/seed=([0-9a-fA-F]{64})/);
  return m ? fromHex(m[1]) : null;
}

function draw() {
  const seed = new Uint8Array(32);
  crypto.getRandomValues(seed);
  derive(seed);
}

function derive(seed, opts = {}) {
  const hex = toHex(seed);
  const desire = $('#desire').checked;
  const phenotype = call({ op: 'phenotype', seed: hex });
  if (!phenotype.ok) {
    $('#hint').textContent = phenotype.error;
    return;
  }
  const prompt = call({ op: 'prompt', seed: hex, desire });
  const soul = call({ op: 'soul', seed: hex, desire });
  const born = call({ op: 'born', seed: hex, born_at: nowIso() });

  current.seed = hex;
  current.cert = born.ok ? born.certificate : null;
  current.panes = {
    prompt: prompt.payload,
    soul: soul.payload,
    json: JSON.stringify(phenotype.phenotype, null, 2),
  };

  const p = phenotype.phenotype;
  $('#handle').textContent = p.handle;
  $('#identity').textContent = `${p.id} · ${p.fixation ? 'taken with ' + p.fixation : 'no fixation'}`;
  $('#seedline').textContent = hex;
  $('#agent').hidden = false;
  renderPanes();

  $('#cert-note').textContent =
    'These traits were derived from the seed above. The certificate is drawn in your browser ' +
    'from a seed you can see, which makes it self-asserted (level 0). It is not a credential.';

  if (!opts.fromHash) {
    history.replaceState(null, '', `#seed=${hex}`);
  }
}

function renderPanes() {
  for (const name of ['prompt', 'soul', 'json']) {
    $(`#pane-${name}`).textContent = current.panes[name] || '';
    $(`#pane-${name}`).hidden = name !== current.pane;
  }
  for (const btn of document.querySelectorAll('[role="tab"]')) {
    btn.setAttribute('aria-selected', String(btn.dataset.pane === current.pane));
  }
}

function copy(text, note = 'Copied.') {
  const done = () => ($('#cert-note').textContent = note);
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
}

function fallbackCopy(text, done) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); done(); } catch (e) { /* ignore */ }
  document.body.removeChild(ta);
}

function download() {
  if (!current.cert) return;
  const blob = new Blob([JSON.stringify(current.cert, null, 2) + '\n'], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `exoself-${current.cert.id}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function wire() {
  $('#draw').addEventListener('click', draw);

  $('#derive-form').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const seed = fromHex($('#seed').value);
    if (!seed) {
      $('#hint').textContent = 'A seed is 64 hex characters. Paste one or draw a new agent.';
      return;
    }
    derive(seed);
  });

  for (const btn of document.querySelectorAll('[role="tab"]')) {
    btn.addEventListener('click', () => {
      current.pane = btn.dataset.pane;
      renderPanes();
    });
  }

  $('#desire').addEventListener('change', () => {
    if (current.seed) derive(fromHex(current.seed), { fromHash: true });
  });

  $('#copy').addEventListener('click', () => copy(current.panes[current.pane] || ''));
  $('#download').addEventListener('click', download);
  $('#share').addEventListener('click', () => {
    if (!current.seed) return;
    copy(`${location.origin}${location.pathname}#seed=${current.seed}`,
      'Link copied. Anyone can derive the same agent from it.');
  });
}

boot();
