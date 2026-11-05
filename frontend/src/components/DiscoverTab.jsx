import { useEffect, useState } from 'react';
import { api } from '../api/client.js';
import { EmptyState } from './cards.jsx';
import TraderBlock from './TraderBlock.jsx';

export default function DiscoverTab({ userId, onCompare, compareSel }) {
  const [feed, setFeed] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [catFilter, setCatFilter] = useState('');
  const [reactions, setReactions] = useState({});

  async function load(uid, opts = {}) {
    setLoading(true); setError('');
    try {
      const r = await api.recommendations(uid || userId, { limit: 30, ...opts });
      setFeed(r);
    } catch (e) { setError('Recommendation service unavailable. Start the backend first.'); }
    finally { setLoading(false); }
  }

  useEffect(() => {
    load().catch(() => {});
  }, []);

  async function react(l, event) {
    try {
      await api.event(userId, l.id, event);
      setReactions((r) => ({ ...r, [l.id]: event }));
      if (event === 'like' || event === 'skip') load();
    } catch {}
  }

  const recs = feed?.recommendations || [];

  // The feed is a set of traders, not a flat product list: each trader shows
  // however many posts they have (1, 2, or a grid of up to 6).
  const groups = [];
  const bySeller = new Map();
  for (const item of recs) {
    const sid = item.listing.seller.id;
    if (!bySeller.has(sid)) {
      const g = { seller: item.listing.seller, items: [] };
      bySeller.set(sid, g);
      groups.push(g);
    }
    bySeller.get(sid).items.push(item);
  }

  return (
    <div className="wrap">
      {/* ---------- LISTINGS FIRST: search, then the live posts ---------- */}
      <section className="section" id="feed">
        <div className="search-row">
          <input value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') setQuery(e.target.value); }}
            placeholder="Search posts — e.g. fertilizer, Hilux, maize seed…"
            aria-label="Search posts" />
          <select value={catFilter} onChange={(e) => setCatFilter(e.target.value)}
            aria-label="Filter by business category" className="cat-filter">
            <option value="">All businesses</option>
            <option value="vehicles">Vehicles</option>
            <option value="agriculture">Agriculture</option>
            <option value="restaurant">Restaurant</option>
          </select>
          <button className="ghost small" onClick={() => load()} disabled={loading}>
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>

        {error && !feed && (
          <EmptyState icon="Offline" title="Service unavailable" body={error} action={<button onClick={() => load()}>Retry</button>} />
        )}

        {loading && !feed && <div className="hint">Loading recommendations…</div>}

        {feed && recs.length === 0 && (
          <EmptyState icon="Empty" title="No matches yet" body="No trader has posted this yet — be the first from Trader Studio." />
        )}

        <div className="trader-feed">
          {groups.filter(({ seller, items }) => {
            if (catFilter && (seller.business_category || '') !== catFilter) return false;
            const q = query.trim().toLowerCase();
            if (!q) return true;
            return items.some(({ listing }) =>
              `${listing.item} ${listing.seller?.name || ''} ${listing.seller?.location || ''} ${listing.seller?.business_category || ''}`.toLowerCase().includes(q));
          }).map(({ seller, items }) => (
            <TraderBlock
              key={seller.id}
              seller={seller}
              items={items}
              onCompare={onCompare}
              compareSel={compareSel}
              reactions={reactions}
              react={react}
            />
          ))}
        </div>

        {feed?.note && <p className="feed-note">{feed.note}</p>}
      </section>
    </div>
  );
}
