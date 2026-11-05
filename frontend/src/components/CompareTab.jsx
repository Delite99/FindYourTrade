import { useState } from 'react';
import { api } from '../api/client.js';
import { ListingCard, EmptyState } from './cards.jsx';

export default function CompareTab({ compareSel, onBrowse }) {
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    if (compareSel.length < 2) return;
    setBusy(true);
    try { setResult(await api.vehicleCompare(compareSel.map((l) => l.id))); }
    catch { alert('Compare service unavailable.'); }
    finally { setBusy(false); }
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <p className="eyebrow">Side-by-side</p>
          <h2>Vehicle compare</h2>
          <p>Tick <b>Compare</b> on two or three vehicle cards, then run an honest side-by-side on price, registration, odometer and forex.</p>
        </div>
        <button onClick={run} disabled={compareSel.length < 2 || busy}>{busy ? 'Comparing…' : `Compare (${compareSel.length})`}</button>
      </div>

      {compareSel.length === 0 && (
        <EmptyState icon="Compare" title="Nothing selected" body="Pick vehicles from Discover or the Assistant first — tick Compare on their cards." action={onBrowse ? <button onClick={onBrowse}>Browse listings</button> : null} />
      )}

      <div className="listing-grid">
        {compareSel.map((l) => <ListingCard key={l.id} l={l} />)}
      </div>

      {result && (
        <div className="panel" style={{ marginTop: 12 }}>
          <div className="panel-head"><h3>Verdict</h3></div>
          <p style={{ marginTop: 0, whiteSpace: 'pre-wrap' }}>{result.comparison}</p>
          <div className="muted small">{result.ownership_reminder}</div>
        </div>
      )}
      <div className="hint" style={{ marginTop: 12 }}>Demo pair: Toyota Hilux 2016 (l10) vs Nissan Navara 2017 (l11) vs fresh import (l12).</div>
    </div>
  );
}
