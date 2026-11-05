import { useEffect, useState } from 'react';
import ChatTab from './components/ChatTab.jsx';
import CompareTab from './components/CompareTab.jsx';
import DashboardTab from './components/DashboardTab.jsx';
import DiscoverTab from './components/DiscoverTab.jsx';
import TraderTab from './components/TraderTab.jsx';
import { api } from './api/client.js';
import { CREDITS } from './assets/images.js';

const TABS = [
  { id: 'discover', label: 'Discover' },
  { id: 'chat', label: 'Assistant' },
  { id: 'compare', label: 'Compare' },
  { id: 'dashboard', label: 'Impact' },
  { id: 'trader', label: 'Trader Studio' },
];

function Wordmark() {
  return (
    <>
      <svg className="mark" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M3.5 9.5h14l-3.2-3.2" />
        <path d="M20.5 14.5h-14l3.2 3.2" />
      </svg>
      <span>FindYourTrade</span>
    </>
  );
}

export default function App() {
  const [tab, setTab] = useState(() => {
    const wanted = new URLSearchParams(window.location.search).get('tab');
    return TABS.some((t) => t.id === wanted) ? wanted : 'discover';
  });
  const [compareSel, setCompareSel] = useState([]);
  const [engine, setEngine] = useState(null);
  const [solid, setSolid] = useState(false);
  const [menu, setMenu] = useState(false);
  const [traderToken, setTraderToken] = useState(() => { try { return localStorage.getItem('fyt_trader_token') || ''; } catch { return ''; } });
  const [traderAccount, setTraderAccount] = useState(null);
  function saveTraderToken(t) {
    setTraderToken(t);
    try {
      if (t) localStorage.setItem('fyt_trader_token', t);
      else localStorage.removeItem('fyt_trader_token');
    } catch {}
    if (!t) setTraderAccount(null);
  }
  // Single source of truth for the session: on startup, a stored token is
  // validated once here — every tab (home, assistant, trader) sees the same
  // login from the first render, no need to visit Trader Studio first.
  useEffect(() => {
    if (!traderToken || traderAccount) return;
    fetch('/api/trader/me', { headers: { Authorization: `Bearer ${traderToken}` } })
      .then((x) => {
        if (!x.ok) throw new Error(`HTTP ${x.status}`);
        return x.json();
      })
      .then((r) => setTraderAccount({ name: r.account.name, seller_id: r.account.seller_id,
                                      photo_verified: r.account.photo_verified }))
      .catch(() => saveTraderToken(''));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [traderToken]);
  // The home identity follows the session directly: the logged-in trader's
  // seller id, otherwise the stored guest id. One derivation, no stale state —
  // no effect chasing another effect.
  const userId = traderAccount?.seller_id || (() => {
    try {
      const saved = localStorage.getItem('fyt_user_id');
      if (saved) return saved;
      const guest = 'guest-' + Math.random().toString(36).slice(2, 10);
      localStorage.setItem('fyt_user_id', guest);
      return guest;
    } catch { return 'guest'; }
  })();

  useEffect(() => {
    api.modelsStatus().then(setEngine).catch(() => setEngine({ live: false, offline: true }));
  }, []);

  useEffect(() => {
    const onScroll = () => setSolid(window.scrollY > 10);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    document.body.style.overflow = menu ? 'hidden' : '';
    return () => { document.body.style.overflow = ''; };
  }, [menu]);

  function go(id) {
    setTab(id);
    setMenu(false);
    const url = id === 'discover' ? window.location.pathname : `${window.location.pathname}?tab=${id}`;
    window.history.replaceState({}, '', url);
    window.scrollTo({ top: 0, behavior: 'auto' });
  }

  function toggleCompare(l) {
    setCompareSel((s) => s.find((x) => x.id === l.id) ? s.filter((x) => x.id !== l.id) : [...s, l].slice(0, 3));
  }

  const initial = ((traderAccount?.name || userId) || 'trader').trim().charAt(0).toUpperCase() || 'T';
  const live = engine?.live;

  return (
    <div className="app">
      <header className={`nav-shell solid${solid ? ' scrolled' : ''}`}>
        <div className="nav-inner">
          <button className="wordmark" onClick={() => go('discover')} aria-label="FindYourTrade home">
            <Wordmark />
          </button>

          <nav className="nav-links" aria-label="Sections">
            {TABS.map((t) => (
              <button key={t.id} className={tab === t.id ? 'active' : ''} onClick={() => go(t.id)}>
                {t.label}
                {t.id === 'compare' && compareSel.length > 0 && <span className="count">{compareSel.length}</span>}
              </button>
            ))}
          </nav>

          <div className="nav-right">
            <button className="nav-status" title={live ? 'Live Meta models connected' : 'Demo mode — add META_API_KEY to .env'}>
              <i className={live ? 'live' : ''} />
              {live ? 'Live AI' : 'Demo AI'}
            </button>
            <div className="avatar" title={traderAccount ? `Trader: ${traderAccount.name} (${traderAccount.seller_id}) — open Trader Studio` : 'Guest — open Trader Studio to sign in / register'} onClick={() => go('trader')} style={{cursor: 'pointer'}}>{initial}</div>
            <button className="burger" onClick={() => setMenu(true)} aria-label="Open menu"><span /></button>
          </div>
        </div>
      </header>

      {menu && (
        <div className="drawer">
          <div className="drawer-top">
            <button className="wordmark" onClick={() => go('discover')}><Wordmark /></button>
            <button className="drawer-close" onClick={() => setMenu(false)} aria-label="Close menu">✕</button>
          </div>
          <nav aria-label="Sections">
            {TABS.map((t) => (
              <button key={t.id} className={tab === t.id ? 'active' : ''} onClick={() => go(t.id)}>
                {t.label}
                {t.id === 'compare' && compareSel.length > 0 && <span className="count">{compareSel.length} selected</span>}
              </button>
            ))}
          </nav>
          <p className="drawer-foot">
            {live ? 'Live Meta models connected' : 'Demo mode — no model keys detected'}<br />
            {traderAccount ? `${traderAccount.name} · ${traderAccount.seller_id}` : 'Guest browsing'} · Malawi
          </p>
        </div>
      )}

      <main>
        {tab === 'discover' && (
          <DiscoverTab userId={userId} onCompare={toggleCompare} compareSel={compareSel} />
        )}

        {tab === 'chat' && (
          <ChatTab compareSel={compareSel} toggleCompare={toggleCompare} />
        )}

        {tab === 'compare' && <CompareTab compareSel={compareSel} onBrowse={() => go('chat')} />}
        {tab === 'dashboard' && <DashboardTab onAsk={() => go('chat')} />}
        {tab === 'trader' && <TraderTab token={traderToken} setToken={saveTraderToken} account={traderAccount} setAccount={setTraderAccount} />}
      </main>

      <footer className="site-foot">
        <div className="foot-grid">
          <div>
            <div className="foot-brand">FindYourTrade</div>
            <p className="foot-note">
              Forex scores are transparent estimates, not economic measurements. Show Me verification
              shows presence at recording time — not legal ownership. Payment stays normal: kwacha,
              mobile money, cash, or part goods.
            </p>
            <p className="foot-credits">
              Photography: {CREDITS.map((c, i) => (
                <span key={c.url}>
                  <a href={c.url} target="_blank" rel="noreferrer noopener">{c.name}</a>{i < CREDITS.length - 1 ? ', ' : ''}
                </span>
              ))} via Unsplash
            </p>
          </div>
          <div className="foot-links">
            {TABS.map((t) => <button key={t.id} onClick={() => go(t.id)}>{t.label}</button>)}
          </div>
        </div>
        <div className="foot-meta">
          <span>Malawi · Built for local trade</span>
          <span>{live ? 'Live models' : 'Demo data'} · {new Date().getFullYear()}</span>
        </div>
      </footer>
    </div>
  );
}
