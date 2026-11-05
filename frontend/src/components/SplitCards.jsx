import { photo } from '../assets/images.js';

/*
 * Editorial split block: a large left-hand statement with a pill call to
 * action, and a stack of soft rounded cards on the right — the pattern used
 * across Meta's About pages, kept here in one reusable component.
 */
export default function SplitCards({ eyebrow, title, body, cta, onCta, items = [] }) {
  return (
    <section className="split">
      <div className="split-copy">
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h2>{title}</h2>
        {body && <p className="lead">{body}</p>}
        {cta && (
          <button className="btn btn-blue" onClick={onCta}>{cta}</button>
        )}
      </div>

      <div className="split-cards">
        {items.map((it) => (
          <button key={it.title} className="soft-card" onClick={it.onClick} type="button">
            {it.photo && (
              <img
                src={photo(it.photo, 260, 72)}
                alt=""
                loading="lazy"
                decoding="async"
                onError={(e) => { e.currentTarget.style.visibility = 'hidden'; }}
              />
            )}
            <span className="soft-card-text">
              <strong>{it.title}</strong>
              <span className="soft-card-body">{it.body}</span>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
