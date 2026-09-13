const DEV = import.meta.env.DEV;

export const API = DEV ? 'http://127.0.0.1:8000' : '';

export function wsUrl() {
  const host = DEV ? '127.0.0.1:8000' : window.location.host;
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${host}/ws`;
}

export async function api(path, options) {
  const res = await fetch(`${API}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) throw new Error(`${path} failed (${res.status})`);
  return res.json();
}
