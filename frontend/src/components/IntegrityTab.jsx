import { useEffect, useState } from 'react';
import { api } from '../api/client.js';
import { EmptyState } from './cards.jsx';

const VERTICALS = [
  { id: '', label: 'All' },
  { id: 'vehicles', label: 'Vehicles' },
  { id: 'farming', label: 'Farming' },
  { id: 'food', label: 'Food' },
  { id: 'parts', label: 'Parts' },
  { id: 'building', label: 'Building' },
  { id: 'electronics', label: 'Electronics' },
];

const VERDICT = {
  mismatch: { label: 'Photo mismatch', tone: 'fraud' },
  duplicate_and_mismatch: { label: 'Duplicate + mismatch', tone: 'fraud' },
  duplicate: { label: 'Duplicate photo', tone: 'fraud' },
  shared_photo: { label: 'Shared photo', tone: 'warn' },
};

const SOURCE = {
  live_vision: { label: 'Live vision', tone: 'live' },
  vision_cache: { label: 'Vision cache', tone: 'cache' },
  human: { label: 'Human verified', tone: 'human' },
  hash_fallback: { label: 'Dataset match', tone: 'hash' },
  unverified: { label: 'Unverified', tone: 'unver' },
};

const money = (n) => `MWK ${Number(n).toLocaleString('en-US')}`;

function VisionBadge({ source }) {
  if (!source) return null;
  const s = SOURCE[source] || { label: source, tone: 'unver' };
  return <span className={`vsrc ${s.tone}`} title="How the platform knows what this photo shows">{s.label}</span>;
}

export default function IntegrityTab() {
  const [stats, setStats] = useState(null);
  const [flags, setFlags] = useState(null);
  const [products, setProducts] = useState([]);
  const [vertical, setVertical] = useState('');
  const [error, setError] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [rl, setRl] = useState(null);
  const [fbState, setFbState] = useState({});

  const reload = () => {
    Promise.all([api.datasetStats(), api.integrityFlags()])
      .then(([s, f]) => {
        setStats(s); setFlags(f);
        setRl(s.reinforcement || null);
      })
      .catch(() => setError(true));
  };

  useEffect(reload, []);

  useEffect(() => {
    api.datasetProducts({ vertical }).then((d) => setProducts(d.products || [])).catch(() => {});
  }, [vertical]);

  if (error) return <EmptyState icon="Integrity" title="Integrity service unavailable" body="Start the backend to run the photo checks." />;
  if (!stats) return <div className="hint">Fingerprinting the dataset…</div>;

  const runVisionScan = async (force) => {
    setScanning(true);
    try {
      const r = await api.integrityScanVision(10, force);
      setRl(r.reinforcement || rl);
      reload();
    } finally {
      setScanning(false);
    }
  };

  const sendFeedback = async (f, action) => {
    const hash = f.photo_hash || f.photoHash;
    const key = `${f.listing_id}:${action}`;
    setFbState((s) => ({ ...s, [key]: 'sending' }));
    try {
      let corrected_brand, corrected_model;
      if (action === 'dispute') {
        const fix = window.prompt(
          'What does this photo actually show? Leave blank if you only want to dispute.\nFormat: Brand Model (e.g. Toyota Hilux)',
          ''
        );
        if (fix === null) { setFbState((s) => ({ ...s, [key]: undefined })); return; }
        const parts = fix.trim().split(/\s+/);
        corrected_brand = parts[0] || '';
        corrected_model = parts.slice(1).join(' ');
      }
      const r = await api.integrityFeedback({
        photo_hash: hash, listing_id: f.listing_id, action,
        corrected_brand, corrected_model,
      });
      setFbState((s) => ({ ...s, [key]: r.ok ? 'done' : 'error' }));
      setRl({ events: r.events, total_reward: r.total_reward });
      reload();
    } catch {
      setFbState((s) => ({ ...s, [key]: 'error' }));
    }
  };

  const cards = [
    { value: stats.products, label: 'labelled products', sub: `${stats.photos} photos fingerprinted` },
    { value: stats.integrity.checked_listings, label: 'posts scanned', sub: `vision: ${stats.vision_mode}` },
    { value: stats.integrity.flagged_listings, label: 'posts flagged', sub: 'mismatch or duplicate' },
    {
      value: rl ? `${rl.total_reward >= 0 ? '+' : ''}${rl.total_reward}` : '—',
      label: 'vision reward',
      sub: rl ? `${rl.events} feedback events · ${rl.confirms} confirm / ${rl.disputes} dispute` : 'no feedback yet',
    },
  ];

  return (
    <div>
      <div className="page-head">
        <div>
          <p className="eyebrow">Integrity</p>
          <h2>Photos that don't match their labels</h2>
          <p>
            Every photo goes to live vision — Muse Spark looks at the actual pixels and says what
            the picture shows. One picture cannot be two products, and a Toyota Hilux cannot be
            posted as a Tesla. Confirm or dispute what vision saw; the platform learns from every vote.
          </p>
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
            <p className="eyebrow">Rules</p>
            <h2>What gets flagged</h2>
          </div>
        </div>
        <div className="rule-grid">
          <article className="rule-card">
            <h3>One photo, one product</h3>
            <p>
              Photos are compared by perceptual hash, not file bytes — so a resized, re-compressed or
              lightly cropped copy is still recognised. Reusing a picture for a different product is fraud.
            </p>
          </article>
          <article className="rule-card">
            <h3>Live vision judges the label</h3>
            <p>
              The photo goes to Muse Spark vision, which looks at the pixels and names the brand and
              model it sees. If the post claims something else, it is flagged and kept out of the feed.
              Uncertain vision warns instead of convicting; unidentified photos need a Show Me video.
            </p>
          </article>
        </div>
        <div className="vision-actions">
          <button className="btn" disabled={scanning} onClick={() => runVisionScan(false)}>
            {scanning ? 'Vision is looking…' : 'Run live vision over unscanned photos'}
          </button>
          <button className="btn ghost" disabled={scanning} onClick={() => runVisionScan(true)}>
            Re-examine disputed photos
          </button>
          <span className="hint">Model: {stats.vision_model} · mode: {stats.vision_mode}</span>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <div>
            <p className="eyebrow">Flagged</p>
            <h2>Posts the scan caught</h2>
            <p className="lead">
              {flags?.count
                ? `${flags.count} of ${flags.checked} scanned posts carry a flag. Green badges show how the platform knows what each photo shows.`
                : 'No flags on the current catalogue.'}
            </p>
          </div>
        </div>

        <div className="flag-list">
          {(flags?.flagged || []).map((f) => {
            const v = VERDICT[f.verdict] || { label: f.verdict, tone: 'warn' };
            const src = f.vision_source
              || (f.flags.find((g) => g.vision_source) || {}).vision_source;
            return (
              <article className="flag-card" key={f.listing_id}>
                {f.photo && <img className="flag-photo" src={f.photo} alt="" loading="lazy" />}
                <div className="flag-body">
                  <div className="flag-top">
                    <strong>{f.item}</strong>
                    <span className={`badge ${v.tone}`}>{v.label}</span>
                    <VisionBadge source={src} />
                  </div>
                  <p className="flag-meta">
                    {f.seller?.name} · {f.listing_id}
                    {f.photo_product && ` · photo shows ${f.photo_product.title}`}
                    {f.vision && (f.vision.brand || f.vision.model) &&
                      ` · vision saw ${[f.vision.brand, f.vision.model].filter(Boolean).join(' ')} (${Math.round((f.vision.confidence || 0) * 100)}%)`}
                  </p>
                  <ul className="flag-reasons">
                    {f.flags.map((flag, i) => <li key={i}>{flag.message}</li>)}
                  </ul>
                  <div className="rl-row">
                    <span className="hint">Vision {f.vision ? 'looked at this photo' : 'has not looked yet'} — agree?</span>
                    <button
                      className="btn mini"
                      disabled={fbState[`${f.listing_id}:confirm`] === 'sending'}
                      onClick={() => sendFeedback(f, 'confirm')}>
                      {fbState[`${f.listing_id}:confirm`] === 'done' ? 'Confirmed ✓' : 'Confirm vision'}
                    </button>
                    <button
                      className="btn mini ghost"
                      disabled={fbState[`${f.listing_id}:dispute`] === 'sending'}
                      onClick={() => sendFeedback(f, 'dispute')}>
                      {fbState[`${f.listing_id}:dispute`] === 'done' ? 'Disputed ✓' : 'Dispute / correct'}
                    </button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <div>
            <p className="eyebrow">Dataset</p>
            <h2>What the model is trained on</h2>
            <p className="lead">
              {stats.products} products across {Object.keys(stats.verticals).length} verticals, each with
              its own photo and fingerprint. Every photo in the set is unique.
            </p>
          </div>
        </div>

        <div className="chip-row">
          {VERTICALS.map((v) => (
            <button key={v.id || 'all'} className={`chip ${vertical === v.id ? 'on' : ''}`}
              onClick={() => setVertical(v.id)}>
              {v.label}
              {v.id && stats.verticals[v.id] ? ` (${stats.verticals[v.id]})` : ''}
            </button>
          ))}
        </div>

        <div className="dataset-grid">
          {products.map((p) => (
            <article className="dataset-card" key={p.id}>
              <div className="dataset-photo">
                <img src={p.photo_url} alt={p.title} loading="lazy" />
              </div>
              <div className="dataset-info">
                <strong>{p.title}</strong>
                <span>{p.brand}{p.model ? ` · ${p.model}` : ''}</span>
                <span className="dataset-price">{money(p.price_mwk)}</span>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
