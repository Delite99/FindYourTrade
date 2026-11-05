import { useEffect, useState } from 'react';
import { api } from '../api/client.js';
import { EmptyState } from './cards.jsx';

const RULES = [
  { n: '40', label: 'Origin', body: 'Produced or already in-country scores highest. A fresh import costs the nation forex before it costs you anything.' },
  { n: '30', label: 'Supply chain', body: 'Local assembly, transport and resale keep more of each kwacha circulating at home.' },
  { n: '15', label: 'Inputs', body: 'Goods made from Malawian inputs — seed, feed, labour — leak less foreign exchange per unit.' },
  { n: '15', label: 'Distance', body: 'Shorter haulage means less imported fuel and fewer border costs baked into the price.' },
];

export default function DashboardTab({ onAsk }) {
  const [d, setD] = useState(null);
  const [error, setError] = useState(false);
  useEffect(() => { api.dashboard().then(setD).catch(() => setError(true)); }, []);
  if (error) return <EmptyState icon="Impact" title="Dashboard unavailable" body="Start the backend to see community impact stats." />;
  if (!d) return <div className="hint">Loading community impact…</div>;

  const cards = [
    { value: d.verified_sellers, label: 'verified sellers', sub: 'building trust' },
    { value: d.verified_listings, label: 'verified listings', sub: `${d.total_listings} total` },
    { value: d.local_listings, label: 'in-country listings', sub: 'no fresh import needed' },
    { value: `~${d.imports_avoided_estimate}`, label: 'imports avoided (est.)', sub: 'forex saved' },
  ];

  return (
    <div>
      <div className="page-head">
        <div>
          <p className="eyebrow">Community</p>
          <h2>Impact dashboard</h2>
          <p>How much trade stays local — and how much forex the community keeps.</p>
        </div>
      </div>

      <div className="stats">
        {cards.map((c) => (
          <div className="stat" key={c.label}>
            <strong>{c.value}</strong>
            <span>{c.label}</span>
            <span className="delta">{c.sub}</span>
          </div>
        ))}
      </div>

      <section className="section">
        <div className="section-head">
          <div>
            <p className="eyebrow">Method</p>
            <h2>How the Forex Footprint works</h2>
            <p className="lead">
              A transparent 40 / 30 / 15 / 15 weighting. It is an estimate, never an economic
              measurement — and every listing shows the arithmetic behind its score.
            </p>
          </div>
        </div>
        <div className="rule-grid">
          {RULES.map((r) => (
            <div className="rule" key={r.label}>
              <strong>{r.n}</strong>
              <span>{r.label}</span>
              <p>{r.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="band">
        <div className="band-inner">
          <h2>Every verified listing keeps value in Malawi.</h2>
          <p>
            Sellers who verify with Show Me videos rise to the top of Discover and search. Buyers see
            the goods before any money moves.
          </p>
          {onAsk && <button className="btn btn-light" onClick={onAsk}>Start a search</button>}
        </div>
      </section>
    </div>
  );
}
