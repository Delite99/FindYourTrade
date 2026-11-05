// Empty by default: requests go to the same origin, which nginx proxies to the
// backend. Set VITE_API_BASE (e.g. http://localhost:9000) to point at a backend
// running elsewhere without touching this file.
const BASE = (import.meta.env?.VITE_API_BASE || '').replace(/\/$/, '');

// Gateway statuses mean nginx could not talk to the backend (usually because the
// container is mid-restart), so the request never reached the application.
const GATEWAY = new Set([502, 503, 504]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// fetch() with a short bounded retry. Without this a 2-second backend restart
// turns into a scary "trade backend returned an error" bubble in the chat.
async function request(url, opts = {}, attempts = 3) {
  const method = (opts.method || 'GET').toUpperCase();
  const idempotent = method === 'GET' || method === 'HEAD';
  let lastErr;

  for (let i = 0; i < attempts; i++) {
    try {
      const res = await fetch(url, opts);
      if (res.ok || !GATEWAY.has(res.status)) return res;
      // 504 means the request DID reach the app and may still be processing —
      // only replay those when the call is safe to repeat.
      if (res.status === 504 && !idempotent) return res;
      lastErr = new Error(`HTTP ${res.status}`);
    } catch (e) {
      lastErr = e;                       // connection refused / DNS / offline
      if (i === attempts - 1) throw e;
    }
    if (i < attempts - 1) await sleep(300 * 2 ** i); // 300ms, 600ms
  }
  throw lastErr;
}

async function j(res) {
  if (!res.ok) {
    // Surface FastAPI's `detail` instead of a bare "HTTP 400".
    let detail = '';
    try {
      const body = await res.clone().json();
      detail = body?.detail ?? body?.error ?? '';
      if (Array.isArray(detail)) detail = detail.map((d) => d?.msg).filter(Boolean).join('; ');
    } catch { /* body was not JSON */ }
    const err = new Error(detail ? `${detail} (HTTP ${res.status})` : `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export const api = {
  health: () => request(`${BASE}/api/health`).then(j),
  parse: (text) => request(`${BASE}/api/parse`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ text }),
  }).then(j),
  transcribe: async (blob, filename='voice.wav') => {
    const fd = new FormData();
    fd.append('file', blob, filename);
    return request(`${BASE}/api/transcribe`, { method: 'POST', body: fd }).then(j);
  },
  match: (text) => request(`${BASE}/api/match`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ text }),
  }).then(j),
  listings: () => request(`${BASE}/api/listings`).then(j),
  codeword: (listing_id) => request(`${BASE}/api/showme/codeword`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ listing_id }),
  }).then(j),
  verify: async ({ listing_id, claimed_count, code_word_said, file }) => {
    const qs = new URLSearchParams({ listing_id, claimed_count: String(claimed_count), code_word_said });
    if (file) {
      const fd = new FormData();
      fd.append('file', file);
      return request(`${BASE}/api/showme/verify?${qs}`, { method: 'POST', body: fd }).then(j);
    }
    return request(`${BASE}/api/showme/verify?${qs}`, { method: 'POST' }).then(j);
  },
  chat: (messages, listing_id) => request(`${BASE}/api/chat`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ messages, listing_id }),
  }).then(j),
  fraudCheck: (messages, listing_id) => request(`${BASE}/api/fraud-check`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ messages, listing_id }),
  }).then(j),
  vehicleCompare: (listing_ids) => request(`${BASE}/api/vehicles/compare`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ listing_ids }),
  }).then(j),
  nettingDemo: () => request(`${BASE}/api/netting/demo`).then(j),
  nettingPropose: (debts) => request(`${BASE}/api/netting/propose`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ debts }),
  }).then(j),
  dashboard: () => request(`${BASE}/api/dashboard`).then(j),
  recommendations: (user_id = 'guest', opts = {}) => {
    const qs = new URLSearchParams({ user_id, limit: String(opts.limit || 8), ...(opts.include_pending ? { include_pending: 'true' } : {}), ...(opts.exclude_seen ? { exclude_seen: 'true' } : {}) });
    return request(`${BASE}/api/recommendations?${qs}`).then(j);
  },
  saveProfile: (user_id, interests, location) => request(`${BASE}/api/profile`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ user_id, interests, location }),
  }).then(j),
  event: (user_id, listing_id, event) => request(`${BASE}/api/events`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ user_id, listing_id, event }),
  }).then(j),
  similar: (listing_id) => request(`${BASE}/api/recommendations/similar/${listing_id}`).then(j),
  viewListing: (listing_id, user_id) => request(`${BASE}/api/listings/${listing_id}/view`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ user_id }),
  }).then(j),
  likeListing: (listing_id) => {
    let token = '';
    try { token = localStorage.getItem('fyt_trader_token') || ''; } catch {}
    return request(`${BASE}/api/listings/${listing_id}/like`, {
      method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {},
    }).then(j);
  },
  listingSocial: (listing_id, user_id) => {
    const qs = user_id ? `?user_id=${encodeURIComponent(user_id)}` : '';
    return request(`${BASE}/api/listings/${listing_id}/social${qs}`).then(j);
  },
  commentOnListing: (listing_id, body, parent_id) => {
    let token = '';
    try { token = localStorage.getItem('fyt_trader_token') || ''; } catch {}
    return request(`${BASE}/api/listings/${listing_id}/comments`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ body, parent_id: parent_id || null }),
    }).then(j);
  },
  modelsStatus: () => request(`${BASE}/api/models/status`).then(j),
  modelsSelftest: () => request(`${BASE}/api/models/selftest`, { method: 'POST' }).then(j),
  modelsTelemetry: (limit = 40) => request(`${BASE}/api/models/telemetry?limit=${limit}`).then(j),
  datasetStats: () => request(`${BASE}/api/dataset/stats`).then(j),
  datasetProducts: (opts = {}) => {
    const qs = new URLSearchParams();
    if (opts.vertical) qs.set('vertical', opts.vertical);
    if (opts.q) qs.set('q', opts.q);
    return request(`${BASE}/api/dataset/products?${qs}`).then(j);
  },
  integrityFlags: () => request(`${BASE}/api/integrity/flags`).then(j),
  integrityCheck: (form) => request(`${BASE}/api/integrity/check`, { method: 'POST', body: form }).then(j),
  integrityScanVision: (limit = 10, force = false) =>
    request(`${BASE}/api/integrity/scan-vision?limit=${limit}${force ? '&force=true' : ''}`, { method: 'POST' }).then(j),
  integrityFeedback: (body) => request(`${BASE}/api/integrity/feedback`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j),
};
