import { useEffect, useRef, useState } from 'react';
import { CATEGORY_PHOTO, photo } from '../assets/images.js';
import { api } from '../api/client.js';

/* Thin-line glyph set — used as the fallback when a photo is unavailable. */
const GLYPHS = {
  fertilizer: (
    <>
      <path d="M6.6 8h10.8l1.1 11.2a1.6 1.6 0 0 1-1.6 1.8H7.1a1.6 1.6 0 0 1-1.6-1.8L6.6 8Z" />
      <path d="M8.6 8V6.3A3.4 3.4 0 0 1 12 2.9a3.4 3.4 0 0 1 3.4 3.4V8" />
      <path d="M9.5 13.4h5" />
    </>
  ),
  seed: (
    <>
      <path d="M12 21v-9.6" />
      <path d="M12 12.2c0-4.1 3-6.2 7.2-6.2 0 4.1-3 6.2-7.2 6.2Z" />
      <path d="M12 15.2c0-3.1-2.6-4.7-6.2-4.7 0 3.1 2.6 4.7 6.2 4.7Z" />
    </>
  ),
  maize: (
    <>
      <path d="M12 2.8c3.1 2.3 4.7 5.6 4.7 9.3s-1.6 7-4.7 9.1c-3.1-2.1-4.7-5.4-4.7-9.1S8.9 5.1 12 2.8Z" />
      <path d="M12 5.6v13.2" />
      <path d="M9.3 8.9h5.4M9.1 12.4h5.8M9.5 15.9h5" />
    </>
  ),
  vehicle: (
    <>
      <path d="M3 15V9.4h9.6l2.6 3H21V15" />
      <path d="M3 15h1.7M7.9 15h7.9M18.6 15H21" />
      <circle cx="6.1" cy="17.2" r="1.9" />
      <circle cx="16.9" cy="17.2" r="1.9" />
    </>
  ),
  parts: (
    <>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M12 2.8v2.5M12 18.7v2.5M2.8 12h2.5M18.7 12h2.5M5.5 5.5l1.8 1.8M16.7 16.7l1.8 1.8M18.5 5.5l-1.8 1.8M7.3 16.7l-1.8 1.8" />
    </>
  ),
  equipment: (
    <>
      <path d="M3.4 17V9.2h4.3l1.5 4H3.4" />
      <path d="M11.4 13.2H21v3.9" />
      <path d="M9.2 9.2h4.4l2 4" />
      <circle cx="7.3" cy="18.2" r="2.1" />
      <circle cx="17.5" cy="18.2" r="2.1" />
    </>
  ),
  agrochem: (
    <>
      <path d="M9.6 3h4.8v4.6l3.2 6.3a3 3 0 0 1-2.7 4.4H9.1a3 3 0 0 1-2.7-4.4l3.2-6.3V3Z" />
      <path d="M7.9 13.2h8.2" />
    </>
  ),
  general: (
    <>
      <path d="M3.6 8.6 12 4.1l8.4 4.5v6.9L12 20l-8.4-4.5V8.6Z" />
      <path d="M3.6 8.6 12 13.1l8.4-4.5M12 13.1V20" />
    </>
  ),
};

export function iconFor(category) {
  return GLYPHS[category] || GLYPHS.general;
}

export function Glyph({ category, className = 'p-glyph' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="0.85" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {iconFor(category)}
    </svg>
  );
}

export function ForexMeter({ forex }) {
  if (!forex) return null;
  const cls = forex.score <= 30 ? 'fx-low' : forex.score >= 70 ? 'fx-high' : 'fx-mid';
  const label = forex.score <= 30 ? 'Low forex' : forex.score >= 70 ? 'High forex' : 'Medium forex';
  return (
    <div className={`fx ${cls}`} title="Transparent estimate, not an economic measurement">
      <div className="fx-head">
        <strong>{label} · {forex.score}/100</strong>
        <em>estimate</em>
      </div>
      <p>{forex.reason}</p>
      <div className="fx-bar"><div className="fx-fill" style={{ width: `${forex.score}%` }} /></div>
    </div>
  );
}

export function TrustPill({ score }) {
  const cls = score >= 80 ? 't-high' : score >= 55 ? 't-mid' : 't-low';
  return <span className={`trust ${cls}`}>{score} trust</span>;
}

export function VerifyChip({ status }) {
  const s = status || 'unverified';
  const label = s === 'verified' ? 'Verified' : s === 'pending' ? 'Pending' : s === 'Match' ? 'Show Me match' : s === 'Mismatch' ? 'Mismatch' : 'Unverified';
  return <span className={`verify-chip ${s}`}>{label}</span>;
}

function PauseGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <rect x="7" y="5" width="3.4" height="14" rx="1.2" />
      <rect x="13.6" y="5" width="3.4" height="14" rx="1.2" />
    </svg>
  );
}

function PlayGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M8 5.2c0-.9 1-1.4 1.7-.9l9 6.8c.6.5.6 1.3 0 1.8l-9 6.8c-.7.5-1.7 0-1.7-.9V5.2z" />
    </svg>
  );
}

function SoundOnGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M11 5 6.5 9H3v6h3.5L11 19V5z" fill="currentColor" stroke="none" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7" />
      <path d="M18.2 6a9 9 0 0 1 0 12" />
    </svg>
  );
}

function SoundOffGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M11 5 6.5 9H3v6h3.5L11 19V5z" fill="currentColor" stroke="none" />
      <path d="M16 9.5l5 5M21 9.5l-5 5" />
    </svg>
  );
}

/* A product post that plays itself while it is on screen — with sound.
   Autoplay starts muted (browsers block audible autoplay); the first tap on
   the tile unmutes, and the speaker button toggles sound any time. */
function VideoTile({ src, poster, label }) {
  const ref = useRef(null);
  const [playing, setPlaying] = useState(true);
  const [inView, setInView] = useState(false);
  const [muted, setMuted] = useState(true);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') return undefined;
    const io = new IntersectionObserver(([e]) => setInView(e.isIntersecting), { threshold: 0.3 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.muted = muted;
    if (inView && playing) el.play().catch(() => {});
    else el.pause();
  }, [inView, playing, muted]);

  function togglePlay() {
    // First interaction unmutes — browsers only allow sound after a gesture.
    if (muted) setMuted(false);
    setPlaying((p) => !p);
  }

  return (
    <div className="p-tile p-video">
      <video
        ref={ref}
        className="p-photo"
        src={src}
        poster={poster}
        muted={muted}
        loop
        playsInline
        preload="metadata"
        aria-label={label}
        onClick={togglePlay}
      />
      <span className="p-tile-shade" aria-hidden />
      <span className="p-badge video">Video</span>
      <button
        className="p-vtoggle"
        type="button"
        onClick={() => setMuted((m) => !m)}
        aria-label={muted ? 'Unmute video' : 'Mute video'}
        aria-pressed={!muted}
        title={muted ? 'Tap for sound' : 'Mute'}
      >
        {muted ? <SoundOffGlyph /> : <SoundOnGlyph />}
      </button>
      <button
        className="p-vtoggle p-vtoggle-play"
        type="button"
        onClick={togglePlay}
        aria-label={playing ? 'Pause video' : 'Play video'}
        aria-pressed={playing}
      >
        {playing ? <PauseGlyph /> : <PlayGlyph />}
      </button>
    </div>
  );
}

/* Views · likes · comments on a post. Views count once per viewer and fire
   when the card scrolls into sight; likes toggle; comments thread with
   replies one level deep. Signed-out visitors read everything and like;
   only signed-in traders can comment (their name is on it). */
export function SocialBar({ listingId, initial }) {
  const [views, setViews] = useState(initial?.views ?? 0);
  const [likes, setLikes] = useState(initial?.likes ?? 0);
  const [liked, setLiked] = useState(!!initial?.liked);
  const [comments, setComments] = useState(initial?.comments ?? []);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [replyTo, setReplyTo] = useState(null);
  const [replyText, setReplyText] = useState('');
  const [err, setErr] = useState('');
  const seenRef = useRef(false);

  useEffect(() => {
    setViews(initial?.views ?? 0);
    setLikes(initial?.likes ?? 0);
    setLiked(!!initial?.liked);
    setComments(initial?.comments ?? []);
  }, [listingId]);

  // Count the view when the card becomes visible (once per mount).
  useEffect(() => {
    const el = document.getElementById(`social-${listingId}`);
    if (!el || typeof IntersectionObserver === 'undefined') return undefined;
    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting && !seenRef.current) {
        seenRef.current = true;
        let uid = 'guest-anon';
        try { uid = localStorage.getItem('fyt_user_id') || 'guest-anon'; } catch {}
        api.viewListing(listingId, uid).then((r) => {
          if (typeof r.views === 'number') setViews(r.views);
        }).catch(() => {});
        io.disconnect();
      }
    }, { threshold: 0.4 });
    io.observe(el);
    return () => io.disconnect();
  }, [listingId]);

  async function toggleLike() {
    try {
      const r = await api.likeListing(listingId);
      if (typeof r.likes === 'number') setLikes(r.likes);
      if (typeof r.liked === 'boolean') setLiked(r.liked);
    } catch {}
  }

  async function postComment(parentId, body, clear) {
    setErr('');
    try {
      const r = await api.commentOnListing(listingId, body, parentId);
      if (r?.comment) {
        setComments((c) => [...c, r.comment]);
        clear();
      } else {
        setErr(r?.error || 'Could not post.');
      }
    } catch (e) {
      setErr(e.message || 'Sign in as a trader to comment.');
    }
  }

  const tops = comments.filter((c) => !c.parent_id);
  const replies = (pid) => comments.filter((c) => c.parent_id === pid);
  const count = comments.length;

  return (
    <div className="p-social" id={`social-${listingId}`}>
      <div className="p-social-row">
        <span className="p-stat" title="Views">👁 {views}</span>
        <button className={`p-like ${liked ? 'on' : ''}`} onClick={toggleLike}
          title={liked ? 'Unlike' : 'Like'} aria-pressed={liked}>
          {liked ? '♥' : '♡'} {likes}
        </button>
        <button className="p-comments-toggle" onClick={() => setOpen((o) => !o)}
          aria-expanded={open}>
          💬 {count} {open ? 'Hide' : 'Comments'}
        </button>
      </div>
      {open && (
        <div className="p-comments">
          {tops.length === 0 && <p className="hint">No comments yet — ask about this post.</p>}
          {tops.map((c) => (
            <div key={c.id} className="p-comment">
              <strong>{c.user_name}</strong>
              <p>{c.body}</p>
              {replies(c.id).map((r) => (
                <div key={r.id} className="p-reply">
                  <strong>{r.user_name}</strong>
                  <p>{r.body}</p>
                </div>
              ))}
              {replyTo === c.id ? (
                <div className="p-reply-box">
                  <input value={replyText} onChange={(e) => setReplyText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && replyText.trim()) postComment(c.id, replyText, () => { setReplyText(''); setReplyTo(null); }); }}
                    placeholder={`Reply to ${c.user_name}…`} aria-label="Write a reply" />
                  <button className="small" disabled={!replyText.trim()}
                    onClick={() => postComment(c.id, replyText, () => { setReplyText(''); setReplyTo(null); })}>
                    Reply
                  </button>
                  <button className="ghost small" onClick={() => { setReplyTo(null); setReplyText(''); }}>Cancel</button>
                </div>
              ) : (
                <button className="link muted-link" onClick={() => { setReplyTo(c.id); setReplyText(''); }}>Reply</button>
              )}
            </div>
          ))}
          <div className="p-comment-box">
            <input value={draft} onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && draft.trim()) postComment(null, draft, () => setDraft('')); }}
              placeholder="Write a comment… (traders only)" aria-label="Write a comment" />
            <button className="small" disabled={!draft.trim()}
              onClick={() => postComment(null, draft, () => setDraft(''))}>
              Post
            </button>
          </div>
          {err && <p className="form-err">{err}</p>}
        </div>
      )}
    </div>
  );
}

export function ListingCard({ l, onAsk, onCompare, compareSelected, social, footer }) {
  const [photoFailed, setPhotoFailed] = useState(false);
  const verified = l.verification_status === 'verified' || l.verification_status === 'Match';
  const flagged = ['mismatch', 'duplicate', 'duplicate_and_mismatch'].includes(l.fraud_verdict);
  const score = l.forex?.score;
  const category = l.category || 'general';
  const photoId = l.photo || CATEGORY_PHOTO[category];

  return (
    <article className="card">
      {(l.video || l.video_url) ? (
        <VideoTile src={l.video || l.video_url} poster={photoId ? photo(photoId, 900) : undefined} label={l.item} />
      ) : (
      <div className={`p-tile tone-${category}`}>
        {photoId && !photoFailed ? (
          <img
            className="p-photo"
            src={photo(photoId, 700)}
            alt=""
            loading="lazy"
            decoding="async"
            onError={() => setPhotoFailed(true)}
          />
        ) : (
          <Glyph category={category} />
        )}
        <span className="p-tile-shade" aria-hidden />
        {flagged ? (
          <span className="p-badge fraud">Photo flagged</span>
        ) : (
          <>
            {verified && <span className="p-badge good">Verified</span>}
            {!verified && l.verification_status === 'pending' && <span className="p-badge">Pending</span>}
          </>
        )}
        {score != null && (
          <span className="p-fxbadge">{score <= 30 ? 'Low forex' : score >= 70 ? 'High forex' : 'Mid forex'} {score}</span>
        )}
      </div>
      )}

      <div className="p-info">
        <span className="p-name">{l.item}</span>
        <span className="p-price">MWK {Number(l.price_mwk).toLocaleString()}</span>
        <p className="p-sub">
          {l.seller.name} · {l.seller.location} · {l.seller.distance_km} km
        </p>
        {l.seller.business_category && (
          <p className="p-cat">{l.seller.business_category}</p>
        )}
        {(l.seller.phone || l.seller.email) && (
          <p className="p-contact">
            {l.seller.phone && <a href={`tel:${l.seller.phone.replace(/\s/g, '')}`}>{l.seller.phone}</a>}
            {l.seller.phone && l.seller.email && ' · '}
            {l.seller.email && <a href={`mailto:${l.seller.email}`}>{l.seller.email}</a>}
          </p>
        )}
      </div>

      {(onAsk || onCompare) && (
        <div className="p-actions">
          {onAsk && <button className="link muted-link" onClick={() => onAsk(l)}>Ask</button>}
          {onCompare && (
            <label className="compare-check">
              <input type="checkbox" checked={!!compareSelected} onChange={() => onCompare(l)} /> Compare
            </label>
          )}
        </div>
      )}
      {social && <SocialBar listingId={l.id} initial={social} />}
      {footer && <div className="p-reco">{footer}</div>}
    </article>
  );
}

export function EmptyState({ icon, title, body, action }) {
  return (
    <div className="empty">
      {icon && <div className="big">{icon}</div>}
      <strong>{title}</strong>
      <div className="small">{body}</div>
      {action && <div style={{ marginTop: 18 }}>{action}</div>}
    </div>
  );
}
