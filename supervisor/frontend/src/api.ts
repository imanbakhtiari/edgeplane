export type Row = Record<string, any>;
let csrf = '';
export function setCSRF(value: string) { csrf = value; }
export async function api<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch('/api/v1' + path, { method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await response.text();
  let data: any = {};
  try { data = text ? JSON.parse(text) : {}; }
  catch { data = { detail: text || `Request failed with HTTP ${response.status}` }; }
  if (!response.ok) {
    const fields = Array.isArray(data.fields) ? data.fields.map((field: any) => `${(field.location || []).join('.')}: ${field.message}`).join('; ') : '';
    throw new Error(fields || (typeof data.detail === 'string' ? data.detail : data.message || `Request failed with HTTP ${response.status}`));
  }
  return data;
}
