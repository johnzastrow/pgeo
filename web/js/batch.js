// Small client-side batch geocoder (forward and reverse).
//
// Input: pasted text or a local delimited file (comma, pipe, tab or semicolon; the separator is
// read from the header row). The first row is expected to be a header.
//   - header with lat + lon (or latitude/longitude) columns -> reverse geocode each row
//   - header with an "address" (or "query"/"text") column    -> forward geocode that column
//   - an address split across columns ("Address Line 1" or "Street", with "City", "State" and
//     "ZIP" if present) -> forward geocode the parts together, unit tail and all-caps left as given
//   - otherwise every non-empty line is a forward query
// Rows that cannot be answered stay in the output with the reason in pgeo_error and cost no
// request: no address, a PO box, or a state this deployment does not hold (region.json "states").
// Matched rows also go to the map as GeoJSON features whose properties are the upload's columns
// plus the pgeo_* answer, and can be downloaded as a .geojson file.
// The download is the upload's own columns, unchanged and in order, with the geocoder's answer
// appended as pgeo_* columns - one output row per input row.
// Requests run a few at a time, at most `rate` started per second (see DEFAULT_RATE_PER_SEC).
// This is a demo convenience; large jobs belong in a server-side batch tool (Phase 10).
import { parseCsv, toCsv, detectDelimiter } from './format.js';

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

// Column names that carry a street address, a town, a state and a ZIP in an upload that splits the
// address across columns ("Address Line 1", "City", "State"). Compared in lower case.
const STREET_COLS = ['address', 'address line 1', 'address line1', 'address1', 'address 1', 'address_line_1',
  'street address', 'street_address', 'street', 'addr'];
const CITY_COLS = ['city', 'town', 'locality', 'municipality'];
const STATE_COLS = ['state', 'st', 'state code', 'state_code', 'region', 'province'];
const ZIP_COLS = ['zip', 'zipcode', 'zip code', 'zip_code', 'postal code', 'postal_code', 'postcode', 'postal'];

// A unit or suite tail ("Suite 100", ", Ste 4", "#12") is not part of where the building is, and it
// pulls a match toward the wrong street; the query leaves it off. The row's own cell is unchanged.
const UNIT_TAIL = /[,\s]+(?:suite|ste|unit|apt|apartment|floor|fl|room|rm|bldg|building)\b\.?\s*#?\s*[\w-]*.*$/i;
const HASH_TAIL = /\s+#\s*[\w-]+.*$/;
const PO_BOX = /^\s*(?:\d+\s+)?(?:p\.?\s*o\.?\s*box|post\s+office\s+box)\b/i;
const cleanStreet = (s) => String(s || '').trim().replace(UNIT_TAIL, '').replace(HASH_TAIL, '').trim();
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// Whole-word test, so the state "ME" is not found inside "Homestead".
const mentions = (text, v) => new RegExp(`(^|[^a-z0-9])${escapeRe(v)}($|[^a-z0-9])`, 'i').test(text);

export function planJobs(text, states = []) {
  if (text.length > MAX_BYTES) throw new Error(`Input is larger than ${MAX_BYTES / 1000} KB`);
  const delimiter = detectDelimiter(text);
  const rows = parseCsv(text, delimiter);
  if (!rows.length) throw new Error('Nothing to geocode');
  const header = rows[0].map((h) => h.trim().toLowerCase());
  const idx = (...names) => header.findIndex((h) => names.includes(h));
  const iLat = idx('lat', 'latitude', 'y');
  const iLon = idx('lon', 'lng', 'long', 'longitude', 'x');
  const iAddr = idx(...STREET_COLS);
  const iText = iAddr >= 0 ? iAddr : idx('query', 'text');
  const iCity = idx(...CITY_COLS);
  const iState = idx(...STATE_COLS);
  const iZip = idx(...ZIP_COLS);

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
    const parts = iAddr >= 0 ? [iCity, iState, iZip].filter((i) => i >= 0) : [];
    jobs = rows.slice(1).map((r) => {
      const raw = (r[iText] || '').trim();
      if (iAddr < 0) return { orig: r, input: raw };
      // A street alone is ambiguous across a whole state or two. When the upload gives a town,
      // state or ZIP in their own columns they go on the end, unless the street already says so.
      const street = cleanStreet(raw);
      const job = { orig: r, input: street };
      if (street) {
        const extra = parts.map((i) => (r[i] || '').trim()).filter((v) => v && !mentions(street, v));
        job.input = [street, ...extra].join(', ');
      }
      // Rows that cannot be answered are kept, with the reason, and cost no request.
      if (PO_BOX.test(raw)) job.skip = 'PO box';
      const st = iState >= 0 ? (r[iState] || '').trim().toUpperCase() : '';
      if (!job.skip && states.length && /^[A-Z]{2}$/.test(st) && !states.includes(st)) {
        job.skip = `outside coverage (${st})`;
      }
      return job;
    });
  } else {
    mode = 'forward';
    columns = ['input'];
    jobs = rows.map((r) => ({ orig: [r.join(', ').trim()], input: r.join(', ').trim() }));
  }
  if (jobs.length > MAX_ROWS) throw new Error(`Limit is ${MAX_ROWS} rows (got ${jobs.length})`);
  const width = Math.max(columns.length, ...jobs.map((j) => j.orig.length));
  columns = Array.from({ length: width }, (_, i) => columns[i] ?? '');
  for (const j of jobs) j.orig = Array.from({ length: width }, (_, i) => j.orig[i] ?? '');
  return { mode, jobs, columns, delimiter };
}

// The example offered on the form: a header row, an address column and some attribute columns of
// the kind a real list carries. Every column comes back in the download and in a map click.
export const EXAMPLE_CSV = [
  'site_id,site_name,category,status,inspected,address',
  'S-101,Portland office,office,active,2026-03-14,"389 Congress St, Portland, ME"',
  'S-102,Augusta depot,warehouse,active,2026-02-02,"210 State St, Augusta, ME"',
  'S-103,Bangor annex,office,closed,2025-11-20,"73 Harlow St, Bangor, ME"',
  'S-201,Concord field office,office,active,2026-04-08,"107 N Main St, Concord, NH"',
  'S-202,Portsmouth yard,yard,active,2026-01-27,"1 Junkins Ave, Portsmouth, NH"',
  'S-104,Caribou site,yard,pending,2026-05-19,"100 Main St, Caribou, ME"',
].join('\n') + '\n';

const DELIMITER_NAMES = { ',': 'comma', '|': 'pipe', '\t': 'tab', ';': 'semicolon' };

// Property names for the upload's columns: the header text, made unique, with a stand-in for a
// blank one. An upload column that is already called pgeo_* is kept apart from the answer's.
function propertyNames(columns) {
  const seen = new Map();
  return columns.map((c, i) => {
    let name = String(c).trim() || `column_${i + 1}`;
    if (name.startsWith('pgeo_')) name = `input_${name}`;
    const n = (seen.get(name) || 0) + 1;
    seen.set(name, n);
    return n === 1 ? name : `${name}_${n}`;
  });
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
  let lastGeojson = null;
  const save = (text, type, ext) => {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = document.createElement('a');
    a.href = url;
    const build = document.documentElement.dataset.build || 'geocoder';
    a.download = `pgeo-${build}-batch-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.${ext}`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  ui.file.addEventListener('change', async () => {
    const f = ui.file.files?.[0];
    if (!f) return;
    if (f.size > MAX_BYTES) { ui.status.textContent = `File is larger than ${MAX_BYTES / 1000} KB`; return; }
    ui.textarea.value = await f.text();
  });

  ui.cancel.addEventListener('click', () => controller?.abort());

  ui.download.addEventListener('click', () => {
    if (lastCsv) save(lastCsv, 'text/csv', 'csv');
  });
  ui.example?.addEventListener('click', () => {
    const url = URL.createObjectURL(new Blob([EXAMPLE_CSV], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'pgeo-batch-example.csv';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  ui.geojson?.addEventListener('click', () => {
    if (lastGeojson) save(lastGeojson, 'application/geo+json', 'geojson');
  });

  ui.run.addEventListener('click', async () => {
    let plan;
    try {
      plan = planJobs(ui.textarea.value, ui.states || []);
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
    if (ui.geojson) ui.geojson.disabled = true;
    ui.table.replaceChildren();
    ui.progress.max = plan.jobs.length;
    ui.progress.value = 0;

    const opts = ui.context();
    // The caller's own columns first, then the answer. The answer's names carry a pgeo_ prefix so
    // that an upload with its own "label", "lat" or "lon" column never ends up with two of them.
    const out = [[...plan.columns, 'pgeo_mode', 'pgeo_label', 'pgeo_lat', 'pgeo_lon', 'pgeo_confidence',
      'pgeo_match_type', 'pgeo_layer', 'pgeo_source', 'pgeo_gid', 'pgeo_error']];
    const features = [];
    const names = propertyNames(plan.columns);
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
    let skipped = 0;
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
          skipped += 1;
        } else if (job.skip) {
          error = job.skip;
          skipped += 1;
        } else {
          try {
            await takeSlot();
            feature = await runOne(ui.client, plan.mode, job, opts, signal);
            if (feature) ok += 1;
          } catch (err) {
            if (err.name === 'AbortError') return;
            error = err.message;
          }
        }
        const p = feature?.properties || {};
        const [lon, lat] = feature?.geometry?.coordinates || [];
        const miss = error || (feature ? '' : 'no match');
        // The map's copy of the answer: the point, with the upload's cells and the pgeo_* fields as
        // flat properties, so a click can show them all. pgeo_row is the row's place in the file.
        const props = Object.fromEntries(names.map((n, k) => [n, job.orig[k]]));
        Object.assign(props, { pgeo_row: i + 1, pgeo_mode: plan.mode, pgeo_label: p.label, pgeo_confidence: p.confidence,
          pgeo_match_type: p.match_type, pgeo_layer: p.layer, pgeo_source: p.source, pgeo_gid: p.gid });
        finished[i] = {
          geo: feature?.geometry ? { type: 'Feature', geometry: feature.geometry, properties: props } : null,
          row: [...job.orig, plan.mode, p.label, lat?.toFixed(6), lon?.toFixed(6), p.confidence,
            p.match_type, p.layer, p.source, p.gid, miss],
          shown: [job.input, p.label, p.confidence, miss],
        };
        while (finished[flushed]) {
          out.push(finished[flushed].row);
          if (finished[flushed].geo) features.push(finished[flushed].geo);
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
    ui.status.textContent = `${signal.aborted ? 'Cancelled' : 'Done'} · ${ok} of ${out.length - 1} matched`
      + `${skipped ? ` · ${skipped} not searched` : ''} · ${DELIMITER_NAMES[plan.delimiter]}-separated`;
    lastCsv = toCsv(out);
    lastGeojson = JSON.stringify({ type: 'FeatureCollection', features });
    ui.download.disabled = out.length <= 1;
    if (ui.geojson) ui.geojson.disabled = !features.length;
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
