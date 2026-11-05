import { useEffect, useState } from 'react';
import { api } from '../api/client.js';

const SLOT_LABEL = {
  spark: 'Reasoning, chat & vision counting',
  wonder: 'Multi-party orchestration',
  sam: 'Segmentation (/v1/responses)',
  transcribe: 'Speech to text',
  // The client can call Muse Image, but no endpoint uses it yet — trade cards
  // are rendered from CSS icons, so the slot is labelled as unwired.
  image: 'Image generation (not wired)',
};

export default function EngineTab() {
  const [status, setStatus] = useState(null);
  const [test, setTest] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function load() {
    try { setStatus(await api.modelsStatus()); }
    catch { setError('Backend unreachable — start uvicorn to see engine status.'); }
  }

  useEffect(() => { load(); }, []);

  async function runTest() {
    setBusy(true); setError('');
    try { setTest(await api.modelsSelftest()); }
    catch { setError('Self-test could not run.'); }
    finally { setBusy(false); }
  }

  const live = status?.live;
  const tel = status?.telemetry || {};
  const media = status?.media || {};

  return (
    <div>
      <div className="page-head">
        <div>
          <p className="eyebrow">AI engine</p>
          <h2>Meta model integration</h2>
          <p>Every feature calls the Meta Model API through one hardened client — retries, caching, a spend cap, and per-call telemetry.</p>
        </div>
        <button className="ghost small" onClick={load}>Refresh</button>
      </div>

      <div className={`engine-banner ${live ? 'live' : 'mock'}`}>
        <div>
          <strong>{live ? '● Live models connected' : '○ Mock mode'}</strong>
          <span>
            {live
              ? 'Real Meta Model API responses. Verify below that each slot answered.'
              : 'No META_API_KEY detected. Every response is labelled is_mock so nothing pretends to be real.'}
          </span>
        </div>
        <button className={live ? '' : 'ghost'} onClick={runTest} disabled={busy}>
          {busy ? 'Testing…' : 'Run self-test'}
        </button>
      </div>

      {error && <div className="hint">{error}</div>}

      {status && (
        <div className="panel">
          <div className="panel-head"><h3>Configured slots</h3><span className="small muted">{status.api_base}</span></div>
          <div className="model-grid">
            {Object.entries(status.models || {}).map(([slot, model]) => {
              const res = test?.results?.find((r) => r.slot === slot);
              const state = !res ? 'idle' : res.ok ? 'ok' : 'fail';
              return (
                <div className={`model-card ${state}`} key={slot}>
                  <div className="model-top">
                    <strong>{slot}</strong>
                    <span className={`dot ${state}`} />
                  </div>
                  <div className="model-id">{model}</div>
                  <div className="small muted">{SLOT_LABEL[slot] || 'model slot'}</div>
                  {res && (
                    <div className="model-result">
                      {res.ok ? `✓ ${res.latency_ms} ms` : `✕ ${res.status || ''} ${res.reason || ''}`.slice(0, 90)}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          {test?.hint && <div className="hint" style={{ marginTop: 10 }}>{test.hint}</div>}
        </div>
      )}

      <div className="stats" style={{ marginTop: 12 }}>
        <div className="stat"><strong>{tel.live_calls ?? 0}</strong><span>live model calls</span></div>
        <div className="stat"><strong>{tel.cached_hits ?? 0}</strong><span>cache hits</span></div>
        <div className="stat"><strong>{tel.avg_latency_ms ?? 0}<span className="small muted"> ms</span></strong><span>avg latency</span></div>
        <div className="stat"><strong>{tel.calls_used ?? 0}<span className="small muted">/{tel.spend_cap_calls ?? 0}</span></strong><span>spend cap used</span></div>
      </div>

      <div className="panel" style={{ marginTop: 12 }}>
        <div className="panel-head"><h3>Pipeline readiness</h3></div>
        <ul className="debt-list">
          <li><span>OpenCV frame sampling</span><strong>{media.opencv ? 'ready' : 'missing'}</strong></li>
          <li><span>ffmpeg audio extraction</span><strong>{media.ffmpeg ? 'ready' : 'install ffmpeg'}</strong></li>
          <li><span>Response cache</span><strong>{status?.cache_entries ?? 0} entries</strong></li>
        </ul>
      </div>

      {tel.recent?.length > 0 && (
        <div className="panel" style={{ marginTop: 12 }}>
          <div className="panel-head"><h3>Recent calls</h3></div>
          <ul className="debt-list">
            {tel.recent.slice(0, 8).map((r, i) => (
              <li key={i}>
                <span>{r.model}</span>
                <span className="muted small">{r.op}</span>
                <strong className={r.ok ? 'ok' : 'warn'}>
                  {r.cached ? 'cached' : r.ok ? `${r.latency_ms} ms` : 'failed'}
                </strong>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="hint" style={{ marginTop: 12 }}>
        To go live: put <b>META_API_KEY</b> in <b>.env</b> at the project root, restart the backend, then press
        “Run self-test”. A slot that fails with 404/400 usually means the model id differs from your account
        catalog — override it with the matching <b>MODEL_*</b> variable.
      </div>
    </div>
  );
}
