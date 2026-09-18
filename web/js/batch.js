// Small client-side batch geocoder (forward and reverse).
//
// Input: pasted CSV or a local .csv file.
//   - header with lat + lon (or latitude/longitude) columns -> reverse geocode each row
//   - header with an "address" (or "query"/"text") column    -> forward geocode that column
//   - otherwise every non-empty line is a forward query
// Requests run one at a time, at most RATE_PER_SEC, well under the server's rate limit.
// This is a demo convenience; large jobs belong in a server-side batch tool (Phase 10).
import { parseCsv, toCsv } from './format.js';

const MAX_ROWS = 250;
const MAX_BYTES = 100_000;
const RATE_PER_SEC = 4;
const MAX_RETRIES = 3;

const sleep = (ms, signal) => new Promise((resolve, reject) => {
  const t = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => { clearTimeout(t); reject(new DOMException('aborted', 'AbortError')); }, { once: true });
});

function planJobs(text) {
  if (text.length > MAX_BYTES) throw new Error(`Input is larger than ${MAX_BYTES / 1000} KB`);
  const rows = parseCsv(text);
  if (!rows.length) throw new Error('Nothing to geocode');
  const header = rows[0].map((h) => h.trim().toLowerCase());
  const idx = (...names) => header.findIndex((h) => names.includes(h));
  const iLat = idx('lat', 'latitude', 'y');
  const iLon = idx('lon', 'lng', 'long', 'longitude', 'x');
  const iText = idx('address', 'query', 'text');

  let mode;
  let jobs;
  if (iLat >= 0 && iLon >= 0) {
    mode = 'reverse';
    jobs = rows.slice(1).map((r) => ({ input: `${r[iLat]},${r[iLon]}`, lat: Number(r[iLat]), lon: Number(r[iLon]) }));
  } else if (iText >= 0 && header.length === 1) {
    // A lone "address" column: unquoted addresses contain commas, so the parser splits
    // them into extra fields. Rejoin the whole line instead of taking only field 0.
    mode = 'forward';
    jobs = rows.slice(1).map((r) => ({ input: r.join(', ').trim() }));
  } else if (iText >= 0) {
    mode = 'forward';
    jobs = rows.slice(1).map((r) => ({ input: (r[iText] || '').trim() }));
  } else {
    mode = 'forward';
    jobs = rows.map((r) => ({ input: r.join(', ').trim() }));
  }
  jobs = jobs.filter((j) => j.input);
  if (jobs.length > MAX_ROWS) throw new Error(`Limit is ${MAX_ROWS} rows (got ${jobs.length})`);
  return { mode, jobs };
}

async function runOne(client, mode, job, opts, signal) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      const fc = mode === 'reverse'
        ? await client.reverse(job.lat, job.lon, { size: 1, layers: opts.layers }, signal)
        : await client.search(job.input, { size: 1, ...opts }, signal);
      return fc.features?.[0] || null;
    } catch (err) {
      if (err.status === 429 && attempt < MAX_RETRIES) {
        await sleep(1500 * (attempt + 1), signal);
        continue;
      }
      throw err;
    }
  }
}

/**
 * Wire the batch panel.
 * @param {object} ui  { client, textarea, file, run, cancel, download, progress, status, table, context,
 *                      onStart, onResults }
 */
export function setupBatch(ui) {
  let controller = null;
  let lastCsv = null;

  ui.file.addEventListener('change', async () => {
    const f = ui.file.files?.[0];
    if (!f) return;
    if (f.size > MAX_BYTES) { ui.status.textContent = `File is larger than ${MAX_BYTES / 1000} KB`; return; }
    ui.textarea.value = await f.text();
  });

  ui.cancel.addEventListener('click', () => controller?.abort());

  ui.download.addEventListener('click', () => {
    if (!lastCsv) return;
    const url = URL.createObjectURL(new Blob([lastCsv], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `pelias-maine-batch-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  ui.run.addEventListener('click', async () => {
    let plan;
    try {
      plan = planJobs(ui.textarea.value);
    } catch (err) {
      ui.status.textContent = err.message;
      return;
    }
    controller = new AbortController();
    const { signal } = controller;
    ui.onStart?.();
    ui.run.disabled = true;
    ui.cancel.disabled = false;
    ui.download.disabled = true;
    ui.table.replaceChildren();
    ui.progress.max = plan.jobs.length;
    ui.progress.value = 0;

    const opts = ui.context();
    const out = [['input', 'mode', 'label', 'lat', 'lon', 'confidence', 'match_type', 'layer', 'source', 'gid', 'error']];
    const features = [];
    const interval = 1000 / RATE_PER_SEC;
    let ok = 0;
    for (const [i, job] of plan.jobs.entries()) {
      if (signal.aborted) break;
      const started = performance.now();
      let feature = null;
      let error = '';
      try {
        feature = await runOne(ui.client, plan.mode, job, opts, signal);
        if (feature) { ok += 1; features.push(feature); }
      } catch (err) {
        if (err.name === 'AbortError') break;
        error = err.message;
      }
      const p = feature?.properties || {};
      const [lon, lat] = feature?.geometry?.coordinates || [];
      out.push([job.input, plan.mode, p.label, lat?.toFixed(6), lon?.toFixed(6), p.confidence,
        p.match_type, p.layer, p.source, p.gid, error || (feature ? '' : 'no match')]);
      appendRow(ui.table, out[out.length - 1]);
      ui.progress.value = i + 1;
      ui.status.textContent = `${i + 1} / ${plan.jobs.length} · ${ok} matched`;
      const wait = interval - (performance.now() - started);
      if (wait > 0) await sleep(wait, signal).catch(() => {});
    }
    ui.status.textContent = `${signal.aborted ? 'Cancelled' : 'Done'} · ${ok} of ${out.length - 1} matched`;
    lastCsv = toCsv(out);
    ui.download.disabled = out.length <= 1;
    ui.run.disabled = false;
    ui.cancel.disabled = true;
    ui.onResults(features);
  });
}

function appendRow(table, cells) {
  const tr = document.createElement('tr');
  // input, label, confidence, error
  for (const v of [cells[0], cells[2], cells[5], cells[10]]) {
    const td = document.createElement('td');
    td.textContent = v == null ? '' : String(v);
    tr.append(td);
  }
  if (cells[10]) tr.className = 'batch-miss';
  table.append(tr);
}
