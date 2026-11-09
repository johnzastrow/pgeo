// Small client-side batch geocoder (forward and reverse).
//
// Input: pasted CSV or a local .csv file.
//   - header with lat + lon (or latitude/longitude) columns -> reverse geocode each row
//   - header with an "address" (or "query"/"text") column    -> forward geocode that column
//   - otherwise every non-empty line is a forward query
// The download is the upload's own columns, unchanged and in order, with the geocoder's answer
// appended as pgeo_* columns - one output row per input row.
// Requests run a few at a time, at most `rate` started per second (see DEFAULT_RATE_PER_SEC).
// This is a demo convenience; large jobs belong in a server-side batch tool (Phase 10).
import { parseCsv, toCsv } from './format.js';

const MAX_ROWS = 1000;
const MAX_BYTES = 250_000;
// Requests started per second. The default fits the serving bundle's own edge limit of 10 a second
// per client; a deployment that has raised that limit (PGEO_EDGE_RATE, docs/CONFIGURATION.md) says
// so with "batch_rate" in its region.json and the page speeds up to match. A search takes about
// 70 ms, so one at a time would top out near 13 a second whatever the pacing: several run at once.
const DEFAULT_RATE_PER_SEC = 8;
const MAX_RATE_PER_SEC = 50;
const MAX_CONCURRENCY = 8;
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
 * @param {object} ui  { client, rate, textarea, file, run, cancel, download, progress, status, table, context,
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
    const rate = Math.min(MAX_RATE_PER_SEC, Math.max(1, Number(ui.rate) || DEFAULT_RATE_PER_SEC));
    const concurrency = Math.min(MAX_CONCURRENCY, Math.max(1, Math.ceil(rate / 5)));
    const interval = 1000 / rate;
    const total = plan.jobs.length;
    // Finished rows wait here, by input position, and are written out in input order: with several
    // requests in flight a later row can finish first, but the file and the table must not be
    // reordered. A cancel keeps the rows that finished in order and drops any after a gap.
    const finished = new Array(total);
    let next = 0;
    let flushed = 0;
    let done = 0;
    let ok = 0;
    let slot = performance.now();
    // One request may start per `interval` across all workers, so the rate holds however many
    // workers there are. Waiting for a slot is what the abort signal interrupts.
    const takeSlot = async () => {
      const at = Math.max(slot, performance.now());
      slot = at + interval;
      const wait = at - performance.now();
      if (wait > 0) await sleep(wait, signal);
    };
    const worker = async () => {
      while (!signal.aborted) {
        const i = next;
        next += 1;
        if (i >= total) return;
        const job = plan.jobs[i];
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
            await takeSlot();
            feature = await runOne(ui.client, plan.mode, job, opts, signal);
            if (feature) { ok += 1; features.push(feature); }
          } catch (err) {
            if (err.name === 'AbortError') return;
            error = err.message;
          }
        }
        const p = feature?.properties || {};
        const [lon, lat] = feature?.geometry?.coordinates || [];
        const miss = error || (feature ? '' : 'no match');
        finished[i] = {
          row: [...job.orig, plan.mode, p.label, lat?.toFixed(6), lon?.toFixed(6), p.confidence,
            p.match_type, p.layer, p.source, p.gid, miss],
          shown: [job.input, p.label, p.confidence, miss],
        };
        while (finished[flushed]) {
          out.push(finished[flushed].row);
          appendRow(ui.table, finished[flushed].shown);
          finished[flushed] = null;
          flushed += 1;
        }
        done += 1;
        ui.progress.value = done;
        ui.status.textContent = `${done} / ${total} · ${ok} matched`;
      }
    };
    await Promise.all(Array.from({ length: concurrency }, worker));
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
