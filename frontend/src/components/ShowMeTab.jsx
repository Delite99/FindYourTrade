import { useState } from 'react';
import { api } from '../api/client.js';
import { EmptyState } from './cards.jsx';

export default function ShowMeTab({ listing }) {
  const [code, setCode] = useState('');
  const [count, setCount] = useState(listing ? listing.quantity_available : 10);
  const [said, setSaid] = useState('');
  const [file, setFile] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState(listing ? 1 : 0);

  async function getCode() {
    if (!listing) return;
    try {
      const r = await api.codeword(listing.id);
      setCode(r.code_word);
      setStep(2);
    } catch { alert('Code word service unavailable.'); }
  }
  async function submit() {
    if (!listing) return;
    setBusy(true);
    try {
      const r = await api.verify({ listing_id: listing.id, claimed_count: Number(count), code_word_said: said, file });
      setResult(r);
      setStep(3);
    } catch (e) { alert('Verify failed: ' + e.message); }
    finally { setBusy(false); }
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <p className="eyebrow">Trust verification</p>
          <h2>Show Me verification</h2>
          <p>The seller records a 15–30s video panning across the goods, saying and showing the one-time code word. Valid for 30 minutes.</p>
        </div>
      </div>

      {!listing && (
        <>
          <EmptyState icon="Show Me" title="Select a listing first" body="Go to Results or Discover and press “Show Me” on any listing to start verification." />
          <section className="section">
            <div className="section-head">
              <div>
                <p className="eyebrow">How it works</p>
                <h2>Presence, proven in three steps</h2>
              </div>
            </div>
            <div className="rule-grid">
              <div className="rule">
                <strong>01</strong>
                <span>One-time code</span>
                <p>The buyer generates a code word that expires in 30 minutes. It cannot be reused on another listing.</p>
              </div>
              <div className="rule">
                <strong>02</strong>
                <span>Recorded proof</span>
                <p>The seller films the goods while saying and showing the code — a 15 to 30 second pan across the stock.</p>
              </div>
              <div className="rule">
                <strong>03</strong>
                <span>Verdict</span>
                <p>Frames are sampled and checked against the claim: Match, Partial match, or Mismatch — with reasons.</p>
              </div>
            </div>
          </section>
        </>
      )}

      {listing && (
        <div className="panel tinted">
          <div className="panel-head">
            <h3>{listing.item}</h3>
            <span className="stock">Listing claims {listing.quantity_available} {listing.unit}</span>
          </div>
          <div className="steps">
            <div className={`step ${step >= 1 ? 'done' : ''}`}><b>1 · Code word</b>Get a one-time code</div>
            <div className={`step ${step >= 2 ? 'done' : ''}`}><b>2 · Record</b>Seller films goods + code</div>
            <div className={`step ${step >= 3 ? 'done' : ''}`}><b>3 · Verdict</b>Match / Partial / Mismatch</div>
          </div>
          <div className="row">
            <button onClick={getCode} disabled={!listing}>Get code word</button>
            {code && <span className="codeword">{code}</span>}
          </div>
          {code && (
            <>
              <div className="row">
                <label className="small">Items visible in video&nbsp;<input type="number" value={count} onChange={(e) => setCount(e.target.value)} style={{ width: 90 }} /></label>
                <label className="small">Code word said/shown&nbsp;<input value={said} onChange={(e) => setSaid(e.target.value)} placeholder="e.g. mango-27" style={{ width: 150 }} /></label>
              </div>
              <div className="row">
                <label className="small">📎 Upload video <span className="muted">(optional — sent to SAM 3.1 + Voice Transcribe with keys)</span>&nbsp;<input type="file" accept="video/*" onChange={(e) => setFile(e.target.files[0])} /></label>
              </div>
              <button className="block" onClick={submit} disabled={!listing || busy}>{busy ? 'Verifying…' : 'Verify video'}</button>
            </>
          )}
          {result && (
            <div className={`verdict ${result.verdict.replace(' ', '-')}`}>
              <strong>{result.verdict === 'Match' ? '✅ Match' : result.verdict === 'Mismatch' ? '❌ Mismatch' : `⚠️ ${result.verdict}`}</strong>
              {result.is_mock && <span className="muted small"> · offline check — no model keys</span>}
              <ul>{result.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
              {result.frames_analyzed > 0 && (
                <div className="muted small">🔍 {result.frames_analyzed} frames sampled from your video</div>
              )}
              {result.trace?.length > 0 && (
                <div className="trace-row">
                  {result.trace.map((t, i) => (
                    <span key={i} className={`trace-chip ${t.ok ? 'live' : 'mock'}`}>
                      {t.model} · {t.op} {t.ok ? `${t.latency_ms}ms` : 'failed'}
                    </span>
                  ))}
                </div>
              )}
              {result.pipeline_notes?.length > 0 && (
                <div className="muted small" style={{ marginTop: 6 }}>{result.pipeline_notes.join(' · ')}</div>
              )}
              <div className="muted small">{result.disclaimer}</div>
            </div>
          )}
        </div>
      )}
      <div className="hint" style={{ marginTop: 32 }}>Get a code word, then enter fewer bags than claimed and the wrong code to see a Mismatch — or the exact count and code for a Match.</div>
    </div>
  );
}
