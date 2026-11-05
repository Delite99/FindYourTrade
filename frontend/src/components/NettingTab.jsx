import { useEffect, useState } from 'react';
import { api } from '../api/client.js';
import { EmptyState } from './cards.jsx';

const BLANK_ROW = { debtor: '', creditor: '', amount: '' };

export default function NettingTab() {
  const [debts, setDebts] = useState([]);
  const [result, setResult] = useState(null);
  const [confirmed, setConfirmed] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api.nettingDemo()
      .then((d) => {
        setDebts(d.debts || []);
        setResult(d);
      })
      .catch(() => setError('Netting service unavailable.'));
  }, []);

  if (error && !result) return <EmptyState icon="Netting" title="Service unavailable" body={error} />;
  if (!result) return <div className="hint">Loading trade circle… (start backend first)</div>;

  const parties = [...new Set(debts.flatMap((d) => [d.debtor, d.creditor]).filter(Boolean))];
  const allConfirmed = parties.length > 0 && parties.every((p) => confirmed[p]);
  const saved = Number(result.original_cash_movement) - Number(result.netted_cash_movement);

  function updateRow(i, field, value) {
    setDebts((rows) => rows.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)));
  }

  function addRow() {
    setDebts((rows) => [...rows, { ...BLANK_ROW }]);
  }

  function removeRow(i) {
    setDebts((rows) => rows.filter((_, idx) => idx !== i));
  }

  async function propose() {
    // Only send complete rows; the API validates debtor/creditor/amount.
    const payload = debts
      .filter((d) => d.debtor.trim() && d.creditor.trim() && Number(d.amount) > 0)
      .map((d) => ({ debtor: d.debtor.trim(), creditor: d.creditor.trim(), amount: Number(d.amount) }));

    if (payload.length < 2) {
      setError('Add at least two debts so there is something to net.');
      return;
    }

    setBusy(true);
    setError('');
    try {
      const r = await api.nettingPropose(payload);
      // NettingResponse carries the settlement, not the input rows, so the
      // edited debts are kept alongside it for rendering.
      setResult({ ...r, debts: payload });
      setDebts(payload);
      setConfirmed({});
    } catch (e) {
      setError(`Could not propose that circle (${e?.message || 'request failed'}).`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <p className="eyebrow">Debt netting</p>
          <h2>Trade circle settlement</h2>
          <p>Circular debts cancel out so less cash has to move. Every party still confirms before anything is recorded.</p>
        </div>
      </div>

      <div className="stats" style={{ marginBottom: 12 }}>
        <div className="stat"><strong>{result.original_cash_movement}</strong><span>cash movement before</span></div>
        <div className="stat"><strong>{result.netted_cash_movement}</strong><span>cash movement after</span><span className="delta">↓ {saved} saved</span></div>
      </div>

      {error && <div className="hint" style={{ marginBottom: 12 }}>{error}</div>}

      <div className="panel">
        <div className="panel-head">
          <h3>Who owes whom</h3>
          <span className="small muted">{debts.length} debt{debts.length === 1 ? '' : 's'}</span>
        </div>
        <ul className="debt-list">
          {debts.map((d, i) => (
            <li key={i}>
              <input
                aria-label={`Debtor ${i + 1}`}
                value={d.debtor}
                onChange={(e) => updateRow(i, 'debtor', e.target.value)}
                placeholder="Debtor"
              />
              <span className="muted">→</span>
              <input
                aria-label={`Creditor ${i + 1}`}
                value={d.creditor}
                onChange={(e) => updateRow(i, 'creditor', e.target.value)}
                placeholder="Creditor"
              />
              <input
                aria-label={`Amount ${i + 1}`}
                type="number"
                min="0"
                value={d.amount}
                onChange={(e) => updateRow(i, 'amount', e.target.value)}
                placeholder="MWK"
              />
              <button type="button" className="ghost" onClick={() => removeRow(i)} aria-label={`Remove debt ${i + 1}`}>✕</button>
            </li>
          ))}
        </ul>
        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
          <button type="button" className="ghost" onClick={addRow}>+ Add debt</button>
          <button type="button" onClick={propose} disabled={busy}>
            {busy ? 'Netting…' : 'Propose netting'}
          </button>
        </div>
      </div>

      <div className="panel tinted">
        <div className="panel-head"><h3>Proposal</h3></div>
        <p style={{ marginTop: 0 }}>{result.explanation}</p>
        {result.mermaid && <pre className="mermaid">{result.mermaid}</pre>}
        {Array.isArray(result.cancellations) && result.cancellations.length > 0 && (
          <ul className="debt-list">
            {result.cancellations.map((c, i) => (
              <li key={i}>
                <span>{(c.cycle || []).join(' → ') || '—'}</span>
                <strong>MWK {c.cancelled_amount} cancelled</strong>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="panel">
        <div className="panel-head"><h3>Confirm netting</h3><span className="small muted">{Object.values(confirmed).filter(Boolean).length}/{parties.length} confirmed</span></div>
        {parties.length === 0 && <p className="muted">Add debts above to see who needs to confirm.</p>}
        {parties.map((p) => (
          <label key={p} className="confirm"><input type="checkbox" checked={!!confirmed[p]} onChange={() => setConfirmed((c) => ({ ...c, [p]: !c[p] }))} /> {p}</label>
        ))}
        {allConfirmed && <p className="ok">✅ All parties confirmed — debts net to zero with no cash moved.</p>}
      </div>
    </div>
  );
}
