// Formatting and CSV helpers (no DOM).

function dmsPart(value, pos, neg, degWidth) {
  const hemi = value >= 0 ? pos : neg;
  const abs = Math.abs(value);
  let d = Math.floor(abs);
  let m = Math.floor((abs - d) * 60);
  let s = Math.round(((abs - d) * 60 - m) * 60);
  if (s === 60) { s = 0; m += 1; }
  if (m === 60) { m = 0; d += 1; }
  return `${String(d).padStart(degWidth, '0')}°${String(m).padStart(2, '0')}′${String(s).padStart(2, '0')}″${hemi}`;
}

/** 43.6591, -70.2568 -> ["43°39′33″N", "070°15′24″W"] */
export function dms(lat, lon) {
  return [dmsPart(lat, 'N', 'S', 2), dmsPart(lon, 'E', 'W', 3)];
}

export function fixed(n, digits = 5) {
  return Number.isFinite(n) ? n.toFixed(digits) : '';
}

export function distanceLabel(km) {
  if (!Number.isFinite(km)) return '';
  const mi = km * 0.621371;
  return mi < 0.1 ? `${Math.round(mi * 5280)} ft` : `${mi.toFixed(mi < 10 ? 2 : 1)} mi`;
}

// --- CSV --------------------------------------------------------------------------------

/** Field separators the batch tool accepts, in the order a tie is settled: comma first. */
export const DELIMITERS = [',', '|', '\t', ';'];

/**
 * Guess the separator from the first non-empty line (the header row): the candidate that
 * appears most often outside double quotes. No candidate present -> comma.
 */
export function detectDelimiter(text) {
  const line = text.split(/\r\n|\n|\r/).find((l) => l.trim() !== '') || '';
  const counts = new Map(DELIMITERS.map((d) => [d, 0]));
  let quoted = false;
  for (const c of line) {
    if (c === '"') quoted = !quoted;
    else if (!quoted && counts.has(c)) counts.set(c, counts.get(c) + 1);
  }
  let best = ',';
  for (const d of DELIMITERS) if (counts.get(d) > counts.get(best)) best = d;
  return best;
}

/**
 * Parse delimited text into rows of strings. Handles quoted fields, escaped quotes ("") and
 * CRLF. The separator is one character (default comma; see detectDelimiter). Deliberately
 * small: this is for pasted address lists, not general CSV.
 */
export function parseCsv(text, delimiter = ',') {
  const rows = [];
  let row = [];
  let field = '';
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i += 1; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === delimiter) { row.push(field); field = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i += 1;
      row.push(field); rows.push(row); row = []; field = '';
    } else field += c;
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((v) => v.trim() !== ''));
}

/**
 * Quote one CSV cell. Cells that a spreadsheet would treat as a formula (leading = + - @,
 * tab or CR) are prefixed with an apostrophe to prevent CSV/formula injection.
 */
export function csvCell(value) {
  let v = value == null ? '' : String(value);
  if (/^[=+\-@\t\r]/.test(v) && !/^-?\d+(\.\d+)?$/.test(v)) v = `'${v}`;
  return /[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

export function toCsv(rows) {
  return `${rows.map((r) => r.map(csvCell).join(',')).join('\r\n')}\r\n`;
}
