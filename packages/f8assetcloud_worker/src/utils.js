export function nowIso() {
  return new Date().toISOString();
}

export function stringOrDefault(value, fallback) {
  const text = String(value || '').trim();
  return text || fallback;
}

export function nullableString(value) {
  if (value === null || value === undefined) {
    return null;
  }
  const text = String(value).trim();
  return text ? text : null;
}

export function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function toBoolean(value) {
  if (typeof value === 'boolean') {
    return value;
  }
  const text = String(value || '').trim().toLowerCase();
  return text === '1' || text === 'true' || text === 'yes';
}

/**
 * Escape SQL LIKE wildcard characters so they are matched literally.
 * Use with an `ESCAPE '\'` clause in the SQL query.
 */
export function escapeLikePattern(value) {
  return String(value)
    .replace(/\\/g, '\\\\')
    .replace(/%/g, '\\%')
    .replace(/_/g, '\\_');
}
