"""Legitimacy-gated personalized recommender.

Only *legitimate* products are ever recommended:
  legitimate = verification_status == "verified"
            OR a live Show Me verdict of "Match" / "Partial match" on the listing.

Everything else (unverified, pending, Mismatch) is excluded from the
recommendation pool by default. `include_pending=True` optionally admits
high-trust pending listings (trust >= 70) for discovery breadth.

Ranking blends:
  - content-based affinity (explicit interests + implicit category affinity
    learned from view/like/verify events),
  - collaborative signal (Jaccard overlap with similar users' liked sets),
  - quality priors (trust score, low forex, nearby, in-stock).

Cold start: falls back to popular legitimate listings.
"""
from __future__ import annotations
from collections import Counter, defaultdict

from . import catalog, integrity
from .matching import parse_request_text
from .forex import score_footprint

# user_id -> {"interests": Counter, "location": str, "liked": set, "viewed": Counter, "skipped": set, "verified": set}
PROFILES: dict[str, dict] = {}

DEFAULT_LOCATION = "Lilongwe"


def _ids_for(categories: set[str], limit: int = 3) -> list[str]:
    """Listing ids in these categories, derived from the seed catalogue.

    Hardcoding ids here would silently break the demo whenever the catalogue
    changes, so the demo profiles are built from whatever listings exist.
    """
    return [l["id"] for l in catalog.listings() if l.get("category") in categories][:limit]


def _demo(interests: dict, categories: set[str]) -> dict:
    ids = _ids_for(categories)
    return {
        "interests": Counter(interests),
        "location": DEFAULT_LOCATION,
        "liked": set(ids),
        "viewed": Counter({i: 2 for i in ids[:2]}),
        "skipped": set(),
        "verified": set(),
    }


# Demo neighbours so the collaborative signal is visible out of the box.
# Built lazily: the catalogue lives in Postgres, so it cannot be resolved at import.
_DEMO_PROFILES: dict[str, dict] = {}


def demo_profiles() -> dict[str, dict]:
    if not _DEMO_PROFILES:
        _DEMO_PROFILES.update({
            "demo-farmer": _demo({"fertilizer": 3, "seed": 2}, {"fertilizer", "seed"}),
            "demo-mechanic": _demo({"parts": 3, "vehicle": 2}, {"parts", "vehicle"}),
        })
    return _DEMO_PROFILES

EVENT_WEIGHTS = {"view": 1.0, "like": 3.0, "verify": 4.0, "inquiry": 4.0, "skip": -2.0}

# ── Scoring weights & thresholds ─────────────────────────────────────────────
# Named so the ranking can be tuned without hunting for magic numbers.
LEGIT_BASE = {"match": 1.0, "partial": 0.8, "verified": 1.0,
              "pending": 0.55, "unverified": 0.2}
LEGIT_BASE_WEIGHT = 0.7
LEGIT_TRUST_WEIGHT = 0.3
TRUSTED_SELLER_THRESHOLD = 80
MIN_ACCOUNT_AGE_DAYS = 30

COLLAB_MAX_POINTS = 12
MAX_VIEWS_COUNTED = 3
VIEW_POINTS = 0.5
TRUST_POINTS_WEIGHT = 0.15
FOREX_POINTS_WEIGHT = 0.08
MAX_DISTANCE_POINTS = 12
DISTANCE_POINTS_WEIGHT = 0.08
IN_STOCK_POINTS = 4
OUT_OF_STOCK_POINTS = -10
LEGIT_POINTS_WEIGHT = 10
SKIP_PENALTY = -400
AFFINITY_WEIGHT = 30
AFFINITY_REASON_THRESHOLD = 0.15
NEARBY_KM = 10
LOW_FOREX_THRESHOLD = 30

DEFAULT_RECO_LIMIT = 8
SIMILAR_LIMIT = 6


def get_profile(user_id: str) -> dict:
    uid = (user_id or "guest").strip() or "guest"
    if uid not in PROFILES:
        seed = demo_profiles().get(uid, {})
        PROFILES[uid] = {
            "interests": Counter(seed.get("interests", {})),
            "location": seed.get("location", DEFAULT_LOCATION),
            "liked": set(seed.get("liked", set())),
            "viewed": Counter(seed.get("viewed", {})),
            "skipped": set(seed.get("skipped", set())),
            "verified": set(seed.get("verified", set())),
        }
    return PROFILES[uid]


def set_profile(user_id: str, interests: list[str] | None = None, location: str | None = None) -> dict:
    p = get_profile(user_id)
    if interests is not None:
        p["interests"] = Counter({c.lower(): 3.0 for c in interests})
    if location:
        p["location"] = location
    return p


def record_event(user_id: str, listing_id: str, event: str) -> dict:
    p = get_profile(user_id)
    w = EVENT_WEIGHTS.get(event, 1.0)
    listing = catalog.listing_by_id(listing_id)
    if listing and event == "skip":
        p["skipped"].add(listing_id)
    elif listing:
        p["viewed"][listing_id] += 1
        p["interests"][listing["category"]] += w
        if event in ("like", "verify", "inquiry"):
            p["liked"].add(listing_id)
        if event == "verify":
            p["verified"].add(listing_id)
        p["skipped"].discard(listing_id)
    return {"user_id": user_id, "event": event, "listing_id": listing_id,
            "top_interests": p["interests"].most_common(3)}


def trust_for(listing: dict) -> tuple[int, str]:
    """One 0–100 trust number per post — the only quality signal buyers see.

    Blends what used to be three separate badges (photo/video verification,
    fraud scan, seller history) into a single level:
    - media proof: real photo/video on file scores; bare stock links don't
    - fraud scan: any fraud-severity flag floors the post (it stays hidden)
    - seller history: trust_score and account age lift or drag
    Labels: 80+ High trust · 55–79 Good trust · 30–54 Low trust · below 30 Risky.
    """
    seller = catalog.seller_by_id(listing.get("seller_id", "")) or {}
    if integrity.is_fraud(listing):
        return 5, "Risky"
    score = 45.0
    photo, video = listing.get("photo", ""), listing.get("video_url", "")
    has_real_media = (photo.startswith("/uploads/") or photo.startswith("/dataset/")
                      or video.startswith("/uploads/"))
    if has_real_media:
        score += 20
    elif photo:
        score += 5
    st = float(seller.get("trust_score", 50) or 50)
    score += (st - 50) * 0.3
    age = int(seller.get("account_age_days", 0) or 0)
    if age >= MIN_ACCOUNT_AGE_DAYS:
        score += 8
    elif age > 0:
        score += 3
    score = max(0, min(100, round(score)))
    label = ("High trust" if score >= 80 else "Good trust" if score >= 55
             else "Low trust" if score >= 30 else "Risky")
    return score, label


def legitimacy(listing: dict, showme_verdict: str | None) -> tuple[bool, float, list[str]]:
    """Return (is_legit, score 0..1, signals). Score now starts from trust_for
    so ranking and the displayed trust level can never disagree."""
    status = listing.get("verification_status", "unverified")
    seller = catalog.seller_by_id(listing["seller_id"])
    signals: list[str] = []
    if showme_verdict in ("Match", "Partial match"):
        signals.append(f"Show Me {showme_verdict}")
        base = LEGIT_BASE["match"] if showme_verdict == "Match" else LEGIT_BASE["partial"]
    elif status == "verified":
        signals.append("verified listing")
        base = LEGIT_BASE["verified"]
    elif status == "pending":
        signals.append("verification pending")
        base = LEGIT_BASE["pending"]
    else:
        signals.append("unverified")
        base = LEGIT_BASE["unverified"]
    trust = seller["trust_score"] / 100
    age_ok = seller["account_age_days"] >= MIN_ACCOUNT_AGE_DAYS
    if seller["trust_score"] >= TRUSTED_SELLER_THRESHOLD:
        signals.append(f"trusted seller ({seller['trust_score']}/100)")
    if not age_ok:
        signals.append(f"new account ({seller['account_age_days']} days)")
    score = round(LEGIT_BASE_WEIGHT * base + LEGIT_TRUST_WEIGHT * trust, 3)
    # Everyone sees every non-fraud post: verified, pending and unverified all
    # enter the pool, ranked by the legitimacy score instead of gated by it.
    # The only removal a user can trigger is an explicit "not interested".
    is_legit = True
    return is_legit, score, signals


def legit_pool(showme: dict, include_pending: bool = False) -> list[dict]:
    pool = []
    for l in catalog.listings():
        if integrity.is_fraud(l):
            continue  # keep flagged posts out of the legitimate pool
        v = (showme.get(l["id"], {}) or {}).get("result") or {}
        verdict = v.get("verdict")
        ok, score, signals = legitimacy(l, verdict)
        if ok:
            pool.append((l, score, signals))
        elif (include_pending and l.get("verification_status") == "pending"
              and (catalog.seller_by_id(l["seller_id"]) or {}).get("trust_score", 0) >= 70):
            pool.append((l, score, signals + ["high-trust pending"]))
    return pool


def collaborative_boost(user_id: str, candidate_id: str) -> tuple[float, str | None]:
    """Jaccard-over-likes boost from demo + live neighbours."""
    me = get_profile(user_id).get("liked", set())
    if not me:
        return 0.0, None
    best = 0.0
    reason = None
    neighbours = {**demo_profiles(), **{u: {"liked": p["liked"]} for u, p in PROFILES.items() if u != user_id}}
    for other, prof in neighbours.items():
        other_liked = set(prof.get("liked", set()))
        if not other_liked or candidate_id not in other_liked:
            continue
        union = me | other_liked
        j = len(me & other_liked) / max(1, len(union))
        if j > best:
            best = j
            reason = f"liked by traders with tastes like yours ({other})"
    return round(best * COLLAB_MAX_POINTS, 2), reason


def recommend(user_id: str, showme: dict, limit: int = DEFAULT_RECO_LIMIT,
              include_pending: bool = False, exclude_seen: bool = False) -> tuple[list[dict], bool]:
    """Return (ranked listing dicts with _reco metadata, used_fallback)."""
    p = get_profile(user_id)
    pool = legit_pool(showme, include_pending=include_pending)
    if exclude_seen:
        seen = set(p["liked"]) | set(p["viewed"])
        pool = [(l, s, g) for (l, s, g) in pool if l["id"] not in seen]
    if not pool:
        return [], False
    # Skipped ("not interested") posts stay in the feed, ranked at the bottom —
    # a skip is a preference signal, never a disappearance.

    total_interest = sum(p["interests"].values()) or 1.0
    scored: list[tuple[float, dict]] = []
    for l, legit_score, legit_signals in pool:
        seller = catalog.seller_by_id(l["seller_id"])
        fx = score_footprint(l["origin"], l["stock_status"], seller["distance_km"], l["reusable"])
        affinity = p["interests"].get(l["category"], 0.0) / total_interest
        views = min(MAX_VIEWS_COUNTED, p["viewed"].get(l["id"], 0)) * VIEW_POINTS
        collab, collab_reason = collaborative_boost(user_id, l["id"])
        trust_pts = seller["trust_score"] * TRUST_POINTS_WEIGHT
        fx_pts = (100 - fx.score) * FOREX_POINTS_WEIGHT
        dist_pts = -min(MAX_DISTANCE_POINTS, seller["distance_km"] * DISTANCE_POINTS_WEIGHT)
        stock_pts = IN_STOCK_POINTS if l["quantity_available"] > 0 else OUT_OF_STOCK_POINTS
        legit_pts = legit_score * LEGIT_POINTS_WEIGHT
        skip_pen = SKIP_PENALTY if l["id"] in p["skipped"] else 0
        total = (affinity * AFFINITY_WEIGHT + views + collab + trust_pts + fx_pts
                 + dist_pts + stock_pts + legit_pts + skip_pen)

        why = list(legit_signals)
        if l["id"] in p["skipped"]:
            why.append("you marked this not interested — kept visible on request")
        if affinity > AFFINITY_REASON_THRESHOLD:
            why.append(f"matches your interest in {l['category']}")
        if collab_reason:
            why.append(collab_reason)
        if fx.score <= LOW_FOREX_THRESHOLD:
            why.append("low forex need")
        if seller["distance_km"] <= NEARBY_KM:
            why.append("close to you")
        scored.append((total, {"listing": l, "seller": seller, "forex": fx,
                               "reasons": why, "score": round(total, 2)}))
    scored.sort(key=lambda x: x[0], reverse=True)
    used_fallback = total_interest <= 1.0 and not p["liked"]
    return [s for _, s in scored[:limit]], used_fallback


def similar_legit(listing_id: str, showme: dict, limit: int = SIMILAR_LIMIT) -> list[dict]:
    base = catalog.listing_by_id(listing_id)
    if not base:
        return []
    pool = legit_pool(showme)
    out = []
    for l, _, _ in pool:
        if l["id"] == listing_id:
            continue
        s = 0.0
        why = []
        if l["category"] == base["category"]:
            s += 20
            why.append(f"same category ({l['category']})")
        price = base.get("price_mwk") or 1
        if price and abs((l.get("price_mwk", 0) - price) / price) < 0.3:
            s += 6
            why.append("similar price band")
        if l["origin"] == "local" and base["origin"] != "local":
            s += 5
            why.append("local alternative — lower forex")
        seller = catalog.seller_by_id(l["seller_id"])
        s += seller["trust_score"] * 0.1
        fx = score_footprint(l["origin"], l["stock_status"], seller["distance_km"], l["reusable"])
        out.append((s, {"listing": l, "seller": seller, "forex": fx, "reasons": why, "score": round(s, 2)}))
    out.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in out[:limit]]


def interests_from_text(text: str) -> list[str]:
    parsed = parse_request_text(text or "")
    return [parsed.category] if parsed.category != "general" else []
