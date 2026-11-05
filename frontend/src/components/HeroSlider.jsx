import { useEffect, useRef, useState } from 'react';
import { HERO_SLIDES, photo } from '../assets/images.js';

const DURATION = 7000;

function Pause() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <rect x="7" y="5" width="3.4" height="14" rx="1.2" />
      <rect x="13.6" y="5" width="3.4" height="14" rx="1.2" />
    </svg>
  );
}

function Play() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M8 5.2c0-.9 1-1.4 1.7-.9l9 6.8c.6.5.6 1.3 0 1.8l-9 6.8c-.7.5-1.7 0-1.7-.9V5.2z" />
    </svg>
  );
}

function Chevron({ dir = 'right' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden
      style={{ transform: dir === 'left' ? 'rotate(180deg)' : 'none' }}>
      <path d="M9 5l7 7-7 7" />
    </svg>
  );
}

export default function HeroSlider({ specs = [], onStart, onBrowse }) {
  const [index, setIndex] = useState(0);
  const [shown, setShown] = useState(0);
  const [paused, setPaused] = useState(false);
  const [reduced, setReduced] = useState(false);
  const timer = useRef(null);
  const touch = useRef(null);
  const loaded = useRef(new Set());

  function onTouchStart(e) {
    touch.current = e.touches[0]?.clientX ?? null;
  }
  function onTouchEnd(e) {
    if (touch.current == null) return;
    const dx = (e.changedTouches[0]?.clientX ?? 0) - touch.current;
    touch.current = null;
    if (Math.abs(dx) < 48) return;
    go(index + (dx < 0 ? 1 : -1));
  }

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduced(mq.matches);
    const on = () => setReduced(mq.matches);
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);

  // Autoplay: paused on hover/focus, when the tab is hidden, or when the
  // visitor asked the OS for reduced motion.
  useEffect(() => {
    if (paused || reduced) return undefined;
    if (document.hidden) return undefined;
    timer.current = setTimeout(() => setIndex((i) => (i + 1) % HERO_SLIDES.length), DURATION);
    return () => clearTimeout(timer.current);
  }, [index, paused, reduced]);

  // Hold the current frame on screen until the next photo is ready, so the
  // carousel never flashes an empty grey box mid-fade.
  useEffect(() => {
    const target = HERO_SLIDES[index];
    if (loaded.current.has(target.id)) {
      setShown(index);
      return undefined;
    }
    const im = new Image();
    im.onload = () => {
      loaded.current.add(target.id);
      setShown(index);
    };
    im.src = photo(target.photo, 1600, 74);
    return () => { im.onload = null; };
  }, [index]);

  // Warm the frame after next.
  useEffect(() => {
    const next = HERO_SLIDES[(index + 1) % HERO_SLIDES.length];
    if (loaded.current.has(next.id)) return;
    const im = new Image();
    im.onload = () => loaded.current.add(next.id);
    im.src = photo(next.photo, 1600, 74);
  }, [index]);

  const go = (n) => setIndex((n + HERO_SLIDES.length) % HERO_SLIDES.length);
  // Copy, photo and credit all follow the frame that is actually on screen.
  const slide = HERO_SLIDES[shown];

  return (
    <section
      className="hero"
      aria-roledescription="carousel"
      aria-label="Why trade local"
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
      onKeyDown={(e) => {
        if (e.key === 'ArrowRight') go(index + 1);
        if (e.key === 'ArrowLeft') go(index - 1);
      }}
    >
      <div className="hero-grid">
        <div className="hero-copy" key={slide.id}>
          <p className="hero-eyebrow">{slide.kicker}</p>
          <h1>
            {slide.title[0]}
            <br />
            {slide.title[1]}
          </h1>
          <p className="hero-sub">{slide.body}</p>
          <div className="hero-actions">
            <button className="btn" onClick={onStart}>Start a search</button>
            <button className="btn ghost" onClick={onBrowse}>Browse verified listings</button>
          </div>
        </div>

        <div className="hero-stage">
          <div className="hero-media">
            {HERO_SLIDES.map((s, i) => (
              <div
                key={s.id}
                className={`hero-slide ${i === shown ? 'active' : ''}`}
                style={{
                  backgroundImage: `url(${photo(s.photo, 1600, 74)})`,
                  backgroundSize: 'cover',
                  backgroundPosition: 'center',
                }}
              >
                <img
                  src={photo(s.photo, 1600, 74)}
                  alt=""
                  loading="eager"
                  fetchpriority={i === 0 ? "high" : "auto"}
                  decoding="async"
                  onLoad={() => loaded.current.add(s.id)}
                />
              </div>
            ))}
          </div>

          <button
            className="hero-toggle"
            onClick={() => setPaused((p) => !p)}
            aria-label={paused ? 'Play slideshow' : 'Pause slideshow'}
            aria-pressed={paused}
          >
            {paused ? <Play /> : <Pause />}
          </button>

          <button className="hero-arrow prev" onClick={() => go(index - 1)} aria-label="Previous slide">
            <Chevron dir="left" />
          </button>
          <button className="hero-arrow next" onClick={() => go(index + 1)} aria-label="Next slide">
            <Chevron />
          </button>

          <div className="hero-controls">
            <div className="hero-dots" role="tablist" aria-label="Slides">
              {HERO_SLIDES.map((s, i) => (
                <button
                  key={s.id}
                  role="tab"
                  aria-selected={i === index}
                  aria-label={`Slide ${i + 1}: ${s.title.join(' ')}`}
                  className={i === index ? 'on' : ''}
                  onClick={() => go(i)}
                >
                  <span />
                </button>
              ))}
            </div>
            <p className="hero-credit">
              <a href={slide.credit.url} target="_blank" rel="noreferrer noopener">{slide.credit.name}</a> / Unsplash
            </p>
          </div>
        </div>
      </div>

      {specs.length > 0 && (
        <div className="hero-specs">
          {specs.map((s) => (
            <div key={s.label}>
              <strong>{s.value}</strong>
              <span>{s.label}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
