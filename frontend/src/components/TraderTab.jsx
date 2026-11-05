import { useEffect, useState } from 'react';
import { api } from '../api/client.js';
import { EmptyState } from './cards.jsx';
import { photo } from '../assets/images.js';

// ── Trader Studio: signup → verify real photo → studio card + promo → save & post ──
// Browsing stays public. Posting requires a trader account whose goods photo
// was identified by live vision. Muse Image regenerates studio cards and
// Muse Wonder directs the promo video — always from verified goods.

const authFetch = (token) => ({ Authorization: `Bearer ${token}` });

export async function traderPost(path, token, body, isForm) {
  const res = await fetch(`/api/trader/${path}`, {
    method: 'POST',
    headers: isForm ? authFetch(token) : { ...authFetch(token), 'Content-Type': 'application/json' },
    body: isForm ? body : JSON.stringify(body || {}),
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch { /* keep status */ }
    throw new Error(msg);
  }
  return res.json();
}

export default function TraderTab({ token, setToken, account, setAccount }) {
  // Session is restored once in App — this tab just consumes it.

  const [mode, setMode] = useState('login');
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [pin, setPin] = useState('');
  const [loc, setLoc] = useState('Lilongwe');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [dupPhone, setDupPhone] = useState(false);

  // posting state
  const [photoFile, setPhotoFile] = useState(null);
  const [videoFile, setVideoFile] = useState(null);
  const [verifyOut, setVerifyOut] = useState(null);
  const [item, setItem] = useState('');
  const [brand, setBrand] = useState('');
  const [model, setModel] = useState('');
  const [price, setPrice] = useState('');
  const [qty, setQty] = useState('1');
  const [postOut, setPostOut] = useState(null);

  // studio & media state
  const [studio, setStudio] = useState(null);
  const [promo, setPromo] = useState(null);
  const [promoDirection, setPromoDirection] = useState('');
  const [useStudioPhoto, setUseStudioPhoto] = useState(true);

  async function doAuth() {
    setBusy(true); setErr(''); setDupPhone(false);
    try {
      const r = await traderPost(mode, '', { name, phone, pin, location: loc });
      setToken(r.token); setAccount({ name: r.name, seller_id: r.seller_id, photo_verified: r.photo_verified });
    } catch (e) {
      setErr(e.message);
      if (/already registered/i.test(e.message || '')) setDupPhone(true);
    }
    finally { setBusy(false); }
  }

  function goLogin() {
    setMode('login'); setErr(''); setDupPhone(false);
  }

  async function doLogout() {
    setToken(''); setAccount(null);
    setPhotoFile(null); setVideoFile(null); setVerifyOut(null); setPostOut(null); setStudio(null); setPromo(null);
    setMode('login'); setPin(''); setErr(''); setDupPhone(false);
  }

  async function doRefresh() {
    try {
      const r = await fetch('/api/trader/me', { headers: authFetch(token) }).then((x) => {
        if (!x.ok) throw new Error(`HTTP ${x.status}`);
        return x.json();
      });
      setAccount({ name: r.account.name, seller_id: r.account.seller_id, photo_verified: r.account.photo_verified });
    } catch { doLogout(); }
  }

  async function doVerify(e, kind) {
    const f = e.target.files?.[0];
    if (!f) return;
    const isVideo = kind === 'video' || (f.type || '').startsWith('video');
    if (isVideo) { setVideoFile(f); setPhotoFile(null); } else { setPhotoFile(f); setVideoFile(null); }
    setVerifyOut(null); setStudio(null); setPromo(null); setBusy(true); setErr('');
    try {
      // Single call: Muse verifies AND returns the suggested form fill.
      // One path for photos and videos — no extra describe step.
      const fd = new FormData();
      fd.append('file', f);
      const r = await traderPost(isVideo ? 'video-verify' : 'photo-verify', token, fd, true);
      setVerifyOut(r);
      // Auto-fill the whole form from Muse's verdict; the trader just confirms.
      const s = r.suggested || {};
      if (s.item) setItem((cur) => cur || s.item);
      if (s.brand) setBrand((cur) => cur || s.brand);
      if (s.model) setModel((cur) => cur || s.model);
      if (s.suggested_price_mwk) setPrice((cur) => cur || String(s.suggested_price_mwk));
      if (s.quantity_available) setQty((cur) => cur || String(s.quantity_available));
      await doRefresh();
    } catch (ex) { setErr(ex.message); }
    finally { setBusy(false); }
  }

  const activePhoto = (useStudioPhoto && studio?.image) ? studio.image : (verifyOut?.photo || '');
  // A verified clip is postable media too: it becomes the post's video_url
  // (kept alongside the photo), so it plays on home and in My Posts.
  const activeVideo = verifyOut?.video || '';

  async function doPost() {
    setBusy(true); setErr('');
    try {
      const r = await traderPost('post', token, {
        listing_id: postOut?.listing_id || undefined,
        item: item || `${brand} ${model}`.trim(),
        brand, model,
        price_mwk: Number(price),
        quantity_available: Number(qty) || 1,
        photo: activePhoto,
        video_url: activeVideo || promo?.video_url || '',
      });
      setPostOut({ ...r, message: 'Posted successfully ✓' });
    } catch (ex) { setErr(ex.message); }
    finally { setBusy(false); }
  }

  async function doRegen() {
    if (!verifyOut?.photo && !studio?.image) return;
    setBusy(true); setErr('');
    try {
      const r = await traderPost('regenerate-photo', token, {
        listing_id: postOut?.listing_id || undefined,
        photo: verifyOut?.photo || studio?.image || '',
        item: item || `${brand} ${model}`.trim() || undefined,
      });
      setStudio(r);
      if (r.image) setUseStudioPhoto(true);
      if (postOut?.listing_id) {
        setPostOut((p) => p ? { ...p, message: 'Studio photo generated and updated on the dashboard!' } : p);
      }
    } catch (ex) { setErr(ex.message); }
    finally { setBusy(false); }
  }

  async function doPromo() {
    if (!activePhoto) return;
    setBusy(true); setErr('');
    try {
      const r = await traderPost('promo-video', token, {
        listing_id: postOut?.listing_id || undefined,
        direction: promoDirection,
        photo: activePhoto,
        item: item || `${brand} ${model}`.trim() || undefined,
        price_mwk: Number(price) || undefined,
        quantity_available: Number(qty) || 1,
      });
      setPromo(r);
      if (postOut?.listing_id && r.video_url) {
        setPostOut((p) => p ? { ...p, message: 'Promo video rendered and saved to your live post!' } : p);
      }
    } catch (ex) { setErr(ex.message); }
    finally { setBusy(false); }
  }

  if (!token) {
    return (
      <div>
        <div className="page-head">
          <div>
            <p className="eyebrow">Trader Studio</p>
            <h2>Sell on FindYourTrade</h2>
            <p>Browsing is open to everyone. Posting needs a trader account — sign up with your
              business name, phone and a PIN. Then verify with a real photo of your goods.</p>
          </div>
        </div>
        <div className="auth-card">
          <div className="chip-row">
            <button className={`chip ${mode === 'login' ? 'on' : ''}`} onClick={() => setMode('login')}>Log in</button>
            <button className={`chip ${mode === 'signup' ? 'on' : ''}`} onClick={() => setMode('signup')}>Sign up</button>
          </div>
          {mode === 'signup' && (
            <label>Business name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Chikondi Agro" /></label>
          )}
          <label>Phone<input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+265 991 ..." /></label>
          <label>PIN (4+ digits)<input type="password" value={pin} onChange={(e) => setPin(e.target.value)} placeholder="••••" /></label>
          {mode === 'signup' && (
            <label>Location<input value={loc} onChange={(e) => setLoc(e.target.value)} placeholder="Lilongwe" /></label>
          )}
          {err && <p className="form-err">{err}</p>}
          {dupPhone && (
            <p className="form-err">That number already has an account. <button className="linklike" onClick={goLogin}>Log in with it instead →</button></p>
          )}
          <button className="btn" disabled={busy} onClick={doAuth}>{busy ? 'Please wait…' : mode === 'login' ? 'Log in' : 'Create trader account'}</button>
          <p className="hint">Demo? Any phone + 4-digit PIN works — e.g. +265991000001 / 1234.</p>
        </div>
      </div>
    );
  }

  const verified = !!account?.photo_verified;
  const [myPosts, setMyPosts] = useState([]);
  const [viewPost, setViewPost] = useState(null);

  useEffect(() => {
    if (!token) { setMyPosts([]); return; }
    fetch('/api/trader/me', { headers: authFetch(token) })
      .then((x) => (x.ok ? x.json() : null))
      .then((r) => { if (r?.my_posts) setMyPosts(r.my_posts); })
      .catch(() => {});
  }, [token, postOut]);

  return (
    <div>
      <div className="page-head">
        <div>
          <p className="eyebrow">Trader Studio</p>
          <h2>Moni, {account?.name || 'trader'}!</h2>
          <p>Seller <strong>{account?.seller_id}</strong> · {verified ? 'photo verified ✓ — ready to post & generate promo' : 'verify a photo of your goods to unlock posting'}</p>
        </div>
        <button className="btn ghost" onClick={doLogout}>Log out</button>
      </div>

      {/* Step 1: Real photo or video upload and identification */}
      <section className="section">
        <div className="section-head">
          <div>
            <p className="eyebrow">Step 1</p>
            <h2>Verify with a real photo or video</h2>
            <p className="lead">Upload an actual photo or a short clip of the goods you sell. Muse watches photos and videos alike — no stock pictures, no screenshots.</p>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <label className="file-btn">Choose photo<input type="file" accept="image/*" hidden onChange={(e) => doVerify(e, 'photo')} /></label>
          <label className="file-btn">Choose video<input type="file" accept="video/*" hidden onChange={(e) => doVerify(e, 'video')} /></label>
        </div>
        {busy && !verifyOut && <p className="hint">Muse is inspecting your {videoFile ? 'video' : 'photo'}…</p>}
        {err && <p className="form-err">{err}</p>}

        {verifyOut && (
          <article className={`flag-card ${verifyOut.identified ? '' : 'warn'}`}>
            {verifyOut.photo && <img className="flag-photo" src={photo(verifyOut.photo)} alt="your goods" />}
            {verifyOut.video && (
              <video className="flag-photo" src={verifyOut.video} controls playsInline preload="metadata" />
            )}
            <div className="flag-body">
              <div className="flag-top">
                <strong>{verifyOut.identified ? (verifyOut.video ? 'Video verified ✓' : 'Photo verified ✓') : 'Could not verify'}</strong>
                <span className={`vsrc ${verifyOut.vision_source === 'live_vision' ? 'live' : 'cache'}`}>{verifyOut.vision_source}</span>
              </div>
              <p className="flag-meta">{verifyOut.message}</p>
              {verifyOut.video && verifyOut.frames_used > 0 && (
                <p className="flag-meta">Muse watched {verifyOut.frames_used} frames{verifyOut.frame_note ? ` · ${verifyOut.frame_note}` : ''}.</p>
              )}
              {verifyOut.identified && verifyOut.suggested && (
                <p className="flag-meta">
                  Auto-filled: <strong>{verifyOut.suggested.item}</strong>
                  {verifyOut.suggested.suggested_price_mwk > 0 && ` — fair price ≈ MWK ${Number(verifyOut.suggested.suggested_price_mwk).toLocaleString()} (${verifyOut.suggested.price_basis})`}
                </p>
              )}
              {verifyOut.photo && verifyOut.identified && (
                <div style={{ marginTop: 10 }}>
                  <button className="btn ghost small" disabled={busy} onClick={doRegen}>
                    {studio?.image ? 'Regenerate studio card' : 'Generate clean studio photo'}
                  </button>
                </div>
              )}
            </div>
          </article>
        )}
      </section>

      {/* Step 2: photo posts get item details + optional studio media;
          video posts get a lean describe-and-post form instead. */}
      {verified && verifyOut?.video ? (
        <section className="section">
          <div className="section-head">
            <div>
              <p className="eyebrow">Step 2</p>
              <h2>Describe & post your video</h2>
              <p className="lead">Muse already watched your clip. Give it a title and price, then post — no studio photo needed.</p>
            </div>
          </div>

          <div className="post-grid">
            <label>Item*<input value={item} onChange={(e) => setItem(e.target.value)} placeholder="e.g. Toyota Hilux 2016 Double Cab" /></label>
            <label>Brand<input value={brand} onChange={(e) => setBrand(e.target.value)} placeholder="e.g. Toyota" /></label>
            <label>Model<input value={model} onChange={(e) => setModel(e.target.value)} placeholder="e.g. Hilux" /></label>
            <label>Price (MWK)*<input type="number" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="e.g. 35000000" /></label>
            <label>Quantity<input type="number" value={qty} onChange={(e) => setQty(e.target.value)} /></label>
          </div>

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginTop: 16 }}>
            <button
              className="btn"
              disabled={busy || !verified || !activeVideo || !price}
              onClick={doPost}
              style={{ minWidth: 200 }}
            >
              {busy ? 'Posting…' : (postOut?.listing_id ? 'Save changes to post' : 'Post video to the market')}
            </button>
          </div>

          {postOut && (
            <article className="flag-card" style={{ marginTop: 20 }}>
              <div className="flag-body">
                <div className="flag-top">
                  <strong>{postOut.listing_id} — {postOut.flagged ? 'held for review' : 'posted successfully ✓'}</strong>
                  <span className={`badge ${postOut.flagged ? 'fraud' : 'good'}`}>
                    {postOut.flagged ? 'review' : 'posted'}
                  </span>
                </div>
                <p className="flag-meta">{postOut.message}</p>
                {(postOut.flags || []).map((f, i) => <p className="flag-meta" key={i}>• {f.message}</p>)}
              </div>
            </article>
          )}
        </section>
      ) : null}
      {verified && verifyOut?.photo && (
        <section className="section">
          <div className="section-head">
            <div>
              <p className="eyebrow">Step 2</p>
              <h2>Item details & Studio media</h2>
              <p className="lead">When your photo is generated, you can post immediately or generate a promo video first and hit save to post successfully.</p>
            </div>
          </div>

          <div className="post-grid">
            <label>Item*<input value={item} onChange={(e) => setItem(e.target.value)} placeholder="e.g. Toyota Hilux 2016 Double Cab" /></label>
            <label>Brand<input value={brand} onChange={(e) => setBrand(e.target.value)} placeholder="e.g. Toyota" /></label>
            <label>Model<input value={model} onChange={(e) => setModel(e.target.value)} placeholder="e.g. Hilux" /></label>
            <label>Price (MWK)*<input type="number" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="e.g. 35000000" /></label>
            <label>Quantity<input type="number" value={qty} onChange={(e) => setQty(e.target.value)} /></label>
          </div>

          {/* Studio card preview if generated */}
          {studio?.image && (
            <article className="flag-card" style={{ marginBottom: 16 }}>
              <img className="flag-photo" src={studio.image} alt="generated studio trade card" />
              <div className="flag-body">
                <div className="flag-top">
                  <strong>Studio trade card generated ✓</strong>
                  <span className="vsrc live">Muse Image</span>
                </div>
                <p className="flag-meta">{studio.description || 'Clean e-commerce studio photo'}</p>
                <div className="chip-row" style={{ marginTop: 8 }}>
                  <button
                    className={`chip ${useStudioPhoto ? 'on' : ''}`}
                    onClick={() => setUseStudioPhoto(true)}
                  >
                    Use Generated Studio Photo
                  </button>
                  <button
                    className={`chip ${!useStudioPhoto ? 'on' : ''}`}
                    onClick={() => setUseStudioPhoto(false)}
                  >
                    Use Original Photo
                  </button>
                </div>
              </div>
            </article>
          )}

          {/* Promo video section */}
          <div style={{ marginTop: 16, marginBottom: 20 }}>
            <label style={{ display: 'block', marginBottom: 8, fontWeight: 500 }}>
              Promo creative direction (optional):
              <input
                value={promoDirection}
                onChange={(e) => setPromoDirection(e.target.value)}
                placeholder='e.g. show seller smiling, test the tractor engine, focus on quality seed'
                maxLength={300}
                style={{ width: '100%', marginTop: 6 }}
              />
            </label>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
              <button className="btn ghost" disabled={busy} onClick={doPromo}>
                {promo?.video_url ? 'Regenerate promo video' : 'Generate promo video'}
              </button>
              {!studio?.image && (
                <button className="btn ghost" disabled={busy} onClick={doRegen}>
                  Generate studio photo
                </button>
              )}
            </div>
          </div>

          {/* Promo video player if generated */}
          {promo && (
            <article className="rule-card" style={{ marginBottom: 20 }}>
              <h3>{promo.hook}</h3>
              {promo.video_url && (
                <div style={{ marginTop: 12 }}>
                  <video className="studio-img" src={promo.video_url} controls playsInline preload="metadata">
                    Your browser cannot play this video.
                  </video>
                  <p className="hint" style={{ marginTop: 8 }}>
                    ✓ Promo video ready. Hit <strong>Save & Post</strong> below to publish it live to the dashboard!
                  </p>
                </div>
              )}
              {promo.video_note && !promo.video_url && <p className="hint">{promo.video_note}</p>}
              <p style={{ marginTop: 10 }}><strong>Voiceover:</strong> {promo.script}</p>
              <ul className="flag-reasons">{(promo.shots || []).map((s, i) => <li key={i}>{s}</li>)}</ul>
            </article>
          )}

          {/* Action buttons: Post or Save */}
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginTop: 16 }}>
            <button
              className="btn"
              disabled={busy || !verified || !activePhoto}
              onClick={doPost}
              style={{ minWidth: 200 }}
            >
              {busy ? 'Saving & Publishing…' : (promo?.video_url ? 'Save & Post successfully' : (postOut?.listing_id ? 'Save changes to post' : 'Post to the market'))}
            </button>
            {!promo?.video_url && (
              <button
                className="btn ghost"
                disabled={busy || !verified || !activePhoto}
                onClick={doPromo}
              >
                Generate promo video first
              </button>
            )}
          </div>

          {/* Status feedback */}
          {postOut && (
            <article className="flag-card" style={{ marginTop: 20 }}>
              <div className="flag-body">
                <div className="flag-top">
                  <strong>{postOut.listing_id} — {postOut.flagged ? 'held for review' : 'posted successfully ✓'}</strong>
                  <span className={`badge ${postOut.flagged ? 'fraud' : 'good'}`}>
                    {postOut.flagged ? 'review' : 'posted'}
                  </span>
                </div>
                <p className="flag-meta">{postOut.message}</p>
                {(postOut.flags || []).map((f, i) => <p className="flag-meta" key={i}>• {f.message}</p>)}
              </div>
            </article>
          )}
        </section>
      )}

      {/* My Posts: every photo/video post under this login, clickable to view */}
      {token && (
        <section className="section">
          <div className="section-head">
            <div>
              <p className="eyebrow">Your shelf</p>
              <h2>My Posts {myPosts.length > 0 && `(${myPosts.length})`}</h2>
              <p className="lead">Everything you posted — photos and videos. Click any post to view it.</p>
            </div>
          </div>
          {myPosts.length === 0 ? (
            <p className="hint">Nothing posted yet — verify a photo or video above and post it to the market.</p>
          ) : (
            <div className="listing-grid">
              {myPosts.map((p) => (
                <article key={p.id} className="card" onClick={() => setViewPost(p)}
                  style={{ cursor: 'pointer' }} title="Click to view">
                  {p.video_url ? (
                    <div className="p-tile p-video">
                      <video className="p-photo" src={p.video_url} poster={p.photo ? photo(p.photo) : undefined}
                        muted playsInline preload="metadata" />
                      <span className="p-tile-shade" aria-hidden />
                      <span className="p-badge video">Video</span>
                    </div>
                  ) : p.photo ? (
                    <div className="p-tile">
                      <img className="p-photo" src={photo(p.photo)} alt="" loading="lazy" decoding="async" />
                      <span className="p-tile-shade" aria-hidden />
                    </div>
                  ) : null}
                  <div className="p-info">
                    <span className="p-name">{p.item}</span>
                    <span className="p-price">MWK {Number(p.price_mwk).toLocaleString()}</span>
                    <p className="p-sub">{p.flagged ? 'Held for review' : p.verification_status || 'posted'}</p>
                  </div>
                </article>
              ))}
            </div>
          )}
          {viewPost && (
            <article className="flag-card" style={{ marginTop: 16 }}>
              {viewPost.video_url ? (
                <video className="flag-photo" style={{ width: '100%', height: 'auto', maxWidth: 480 }}
                  src={viewPost.video_url} poster={viewPost.photo ? photo(viewPost.photo) : undefined}
                  controls playsInline preload="metadata" />
              ) : viewPost.photo ? (
                <img className="flag-photo" style={{ width: '100%', height: 'auto', maxWidth: 480 }}
                  src={photo(viewPost.photo)} alt={viewPost.item} />
              ) : null}
              <div className="flag-body">
                <div className="flag-top">
                  <strong>{viewPost.item} ({viewPost.id})</strong>
                  <button className="ghost small" onClick={() => setViewPost(null)}>Close</button>
                </div>
                <p className="flag-meta">
                  MWK {Number(viewPost.price_mwk).toLocaleString()} · {viewPost.quantity_available} available · {viewPost.verification_status || 'posted'}
                  {viewPost.flagged ? ' · held for review' : ''}
                </p>
              </div>
            </article>
          )}
        </section>
      )}
    </div>
  );
}

export function TraderGate({ onGo }) {
  return <EmptyState icon="Trader" title="Traders sign up to post" body="Browsing is free for everyone. To sell, create a trader account and verify a photo of your goods." action={<button onClick={onGo}>Open Trader Studio</button>} />;
}
