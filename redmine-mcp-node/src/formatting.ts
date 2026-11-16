export type JsonObject = Record<string, unknown>;

export function isObject(value: unknown): value is JsonObject {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

export function object(value: unknown): JsonObject {
  return isObject(value) ? value : {};
}

export function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function records(value: unknown): JsonObject[] {
  return array(value).filter(isObject);
}

export function neutralizeUntrustedMarkers(text: string): string {
  return text.replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

export function wrapUntrusted(value: unknown): string {
  return `<redmine_content untrusted="true">${neutralizeUntrustedMarkers(String(value ?? ''))}</redmine_content>`;
}

export function nameText(value: unknown): string {
  return neutralizeUntrustedMarkers(String(value || ''));
}

export function flattenRef(value: unknown): string | null {
  const { id, name } = object(value);
  const label = nameText(name);
  if (id == null) return label || null;
  return label ? `${label} (#${id})` : `#${id}`;
}

export function formatAttachment(row: JsonObject): JsonObject {
  return {
    id: row.id ?? null,
    filename: wrapUntrusted(row.filename),
    filesize: row.filesize ?? null,
    author: flattenRef(row.author),
    created_on: row.created_on ?? null,
  };
}

export function paginated(raw: JsonObject, key: string, rows: JsonObject[]): JsonObject {
  return {
    total_count: Object.hasOwn(raw, 'total_count') ? raw.total_count : 0,
    offset: Object.hasOwn(raw, 'offset') ? raw.offset : 0,
    limit: Object.hasOwn(raw, 'limit') ? raw.limit : 0,
    [key]: rows,
  };
}

// Redmine compares field values as text. Match Python's rendering of JSON values,
// including booleans and lists of strings returned by multiselect custom fields.
export function pythonRepr(value: unknown): string {
  if (typeof value !== 'string') return pythonString(value);
  const quote = value.includes("'") && !value.includes('"') ? '"' : "'";
  const escaped = value.replaceAll('\\', '\\\\').replaceAll(quote, '\\' + quote)
    .replaceAll('\t', '\\t').replaceAll('\n', '\\n').replaceAll('\r', '\\r')
    .replace(/[\x00-\x1f\x7f]/g, (char) => '\\x' + char.charCodeAt(0).toString(16).padStart(2, '0'));
  return quote + escaped + quote;
}

export function pythonString(value: unknown): string {
  if (value == null) return 'None';
  if (value === true) return 'True';
  if (value === false) return 'False';
  if (Array.isArray(value)) return '[' + value.map(pythonRepr).join(', ') + ']';
  if (isObject(value)) {
    return '{' + Object.entries(value).map(([key, item]) => `${pythonRepr(key)}: ${pythonRepr(item)}`).join(', ') + '}';
  }
  return String(value);
}
