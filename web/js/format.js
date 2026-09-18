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

/**
 * Parse CSV text into rows of strings. Handles quoted fields, escaped quotes ("") and
 * CRLF. Deliberately small: this is for pasted address lists, not general CSV.
 */
export function parseCsv(text) {
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
    else if (c === ',') { row.push(field); field = ''; }
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
