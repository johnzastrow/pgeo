// Small client-side batch geocoder (forward and reverse).
//
// Input: pasted CSV or a local .csv file.
//   - header with lat + lon (or latitude/longitude) columns -> reverse geocode each row
//   - header with an "address" (or "query"/"text") column    -> forward geocode that column
//   - otherwise every non-empty line is a forward query
// The download is the upload's own columns, unchanged and in order, with the geocoder's answer
// appended as pgeo_* columns - one output row per input row.
// Requests run one at a time, at most RATE_PER_SEC, well under the server's rate limit.
// This is a demo convenience; large jobs belong in a server-side batch tool (Phase 10).
import { parseCsv, toCsv } from './format.js';

const MAX_ROWS = 1000;
const MAX_BYTES = 250_000;
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

  // Every job carries `orig`, the row's own cells, and the plan carries `columns`, their names:
  // the output file is the upload with the geocoder's answer appended, so rows can be joined back
  // to the caller's records. A row with extra cells (unquoted commas) widens the table rather than
  // losing them.
  let mode;
  let jobs;
  let columns;
  if (iLat >= 0 && iLon >= 0) {
    mode = 'reverse';
    columns = rows[0];
    jobs = rows.slice(1).map((r) => ({ orig: r, input: `${r[iLat]},${r[iLon]}`, lat: Number(r[iLat]), lon: Number(r[iLon]) }));
  } else if (iText >= 0 && header.length === 1) {
    // A lone "address" column: unquoted addresses contain commas, so the parser splits
    // them into extra fields. Rejoin the whole line instead of taking only field 0.
    mode = 'forward';
    columns = [rows[0][0]];
    jobs = rows.slice(1).map((r) => ({ orig: [r.join(', ').trim()], input: r.join(', ').trim() }));
  } else if (iText >= 0) {
    mode = 'forward';
    columns = rows[0];
    jobs = rows.slice(1).map((r) => ({ orig: r, input: (r[iText] || '').trim() }));
  } else {
    mode = 'forward';
    columns = ['input'];
    jobs = rows.map((r) => ({ orig: [r.join(', ').trim()], input: r.join(', ').trim() }));
  }
  if (jobs.length > MAX_ROWS) throw new Error(`Limit is ${MAX_ROWS} rows (got ${jobs.length})`);
  const width = Math.max(columns.length, ...jobs.map((j) => j.orig.length));
  columns = Array.from({ length: width }, (_, i) => columns[i] ?? '');
  for (const j of jobs) j.orig = Array.from({ length: width }, (_, i) => j.orig[i] ?? '');
  return { mode, jobs, columns };
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
    const build = document.documentElement.dataset.build || 'geocoder';
    a.download = `pgeo-${build}-batch-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.csv`;
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
    // The caller's own columns first, then the answer. The answer's names carry a pgeo_ prefix so
    // that an upload with its own "label", "lat" or "lon" column never ends up with two of them.
    const out = [[...plan.columns, 'pgeo_mode', 'pgeo_label', 'pgeo_lat', 'pgeo_lon', 'pgeo_confidence',
      'pgeo_match_type', 'pgeo_layer', 'pgeo_source', 'pgeo_gid', 'pgeo_error']];
    const features = [];
    const interval = 1000 / RATE_PER_SEC;
    let ok = 0;
    for (const [i, job] of plan.jobs.entries()) {
      if (signal.aborted) break;
      const started = performance.now();
      let feature = null;
      let error = '';
      // A row with nothing to look up is kept, so the output has one row per input row, but it
      // costs no request.
      const unusable = plan.mode === 'reverse'
        ? !Number.isFinite(job.lat) || !Number.isFinite(job.lon)
        : !job.input;
      if (unusable) {
        error = plan.mode === 'reverse' ? 'invalid coordinates' : 'no address';
      } else {
        try {
          feature = await runOne(ui.client, plan.mode, job, opts, signal);
          if (feature) { ok += 1; features.push(feature); }
        } catch (err) {
          if (err.name === 'AbortError') break;
          error = err.message;
        }
      }
      const p = feature?.properties || {};
      const [lon, lat] = feature?.geometry?.coordinates || [];
      out.push([...job.orig, plan.mode, p.label, lat?.toFixed(6), lon?.toFixed(6), p.confidence,
        p.match_type, p.layer, p.source, p.gid, error || (feature ? '' : 'no match')]);
      appendRow(ui.table, [job.input, p.label, p.confidence, error || (feature ? '' : 'no match')]);
      ui.progress.value = i + 1;
      ui.status.textContent = `${i + 1} / ${plan.jobs.length} · ${ok} matched`;
      const wait = unusable ? 0 : interval - (performance.now() - started);
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

// cells: input, label, confidence, error
function appendRow(table, cells) {
  const tr = document.createElement('tr');
  for (const v of cells) {
    const td = document.createElement('td');
    td.textContent = v == null ? '' : String(v);
    tr.append(td);
  }
  if (cells[3]) tr.className = 'batch-miss';
  table.append(tr);
}
