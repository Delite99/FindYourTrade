import { useRef } from 'react';
import { ListingCard } from './cards.jsx';

const initials = (name) =>
  name.split(' ').filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase();

function Arrow({ dir = 'right' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden
      style={{ transform: dir === 'left' ? 'rotate(180deg)' : 'none' }}>
      <path d="M9 5l7 7-7 7" />
    </svg>
  );
}

/*
 * One trader account and their posts.
 * Up to two posts stack; three or more become a single swipeable row, so a
 * prolific trader never pushes the rest of the feed down the page.
 */
export default function TraderBlock({ seller, items, onCompare, compareSel, reactions, react }) {
  const rail = useRef(null);
  const slider = items.length > 2;

  const nudge = (dir) => {
    const el = rail.current;
    if (!el) return;
    el.scrollBy({ left: dir * el.clientWidth * 0.72, behavior: 'smooth' });
  };

  return (
    <section className="trader">
      <header className="trader-head">
        <span className="trader-avatar" aria-hidden>{initials(seller.name)}</span>
        <div className="trader-id">
          <strong>{seller.name}</strong>
          <span>
            {seller.location} · {seller.distance_km} km · {seller.trust_score} trust
          </span>
        </div>

        <div className="trader-tools">
          <span className="trader-count">
            {items.length} {items.length === 1 ? 'post' : 'posts'}
          </span>
          {slider && (
            <div className="trader-nav">
              <button type="button" onClick={() => nudge(-1)} aria-label="Previous posts">
                <Arrow dir="left" />
              </button>
              <button type="button" onClick={() => nudge(1)} aria-label="More posts">
                <Arrow />
              </button>
            </div>
          )}
        </div>
      </header>

      <div
        className={`trader-posts${slider ? ' slider' : ''}`}
        ref={rail}
        tabIndex={slider ? 0 : undefined}
        role={slider ? 'group' : undefined}
        aria-label={slider ? `${seller.name} posts` : undefined}
      >
        {items.map(({ listing, reco_score, legitimacy_score, why, social }) => (
          <ListingCard
            key={listing.id}
            l={{ ...listing, rank_explanation: why.join(' · '), video: listing.video_url }}
            compareSelected={compareSel.find((x) => x.id === listing.id)}
            onCompare={onCompare}
            social={social}
            footer={
              <>
                <span className="p-score">
                  Match {reco_score} · trust {legitimacy_score <= 1 ? Math.round(legitimacy_score * 100) : Math.round(legitimacy_score)}
                </span>
                <button className={`link ${reactions[listing.id] === 'like' ? 'on' : ''}`} onClick={() => react(listing, 'like')}>
                  {reactions[listing.id] === 'like' ? 'Interested ✓' : 'Interested'}
                </button>
                <button className="link muted-link" onClick={() => react(listing, 'skip')}>Not for me</button>
              </>
            }
          />
        ))}
      </div>
    </section>
  );
}
