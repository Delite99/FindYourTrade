"""FindYourTrade FastAPI backend (PRD section 10).

Every AI feature routes through the Meta Model API (OpenAI-compatible) via
`meta_client`: Muse Spark (reasoning + vision counting), Muse Wonder
(orchestration), Muse Voice Transcribe (speech), Muse Image (trade cards).
SAM 3.1 is segmentation on /v1/responses and is not used by the count path.
Each endpoint degrades gracefully and reports exactly which models ran in
its `trace` field.

`config` is imported first so .env is loaded before any model settings are read.
"""
from __future__ import annotations

import logging
import os
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config  # noqa: F401  (must load before meta_client)
from . import db
from . import media
from . import meta_client
from . import recommender
from .forex import score_footprint
from .matching import parse_request_text, rank_listings
from .models import (
    ParsedRequest, MatchResponse, SellerOut, ListingOut, SocialOut, CommentOut,
    ChatRequest, ChatResponse,
    ConversationalResponse, CodeWordResponse, VerifyResult, VehicleCompareResponse,
    NettingRequest, NettingResponse, RecommendationItem, RecommendationResponse,
)
from .netting import propose_netting
from . import catalog
from . import fraud_model
from . import integrity

logger = logging.getLogger("findyourtrade.api")

# ── Business rules ───────────────────────────────────────────────────────────
# A counted quantity may differ from the listing by this fraction and still be
# treated as a match (small counting error is normal in video).
COUNT_TOLERANCE_RATIO = 0.2
# Floor so tiny listings (1–2 units) still allow an off-by-one.
COUNT_TOLERANCE_MIN = 1


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        schema = db.ensure_schema()
        if not schema.get("ok"):
            print(f"Warning: Database schema error: {schema.get('error')}")
        seeded = db.seed_catalog()
        if not seeded.get("ok"):
            print(f"Warning: Database seed error: {seeded.get('error')}")
        prods = db.seed_products()
        if not prods.get("ok"):
            print(f"Warning: Dataset seed error: {prods.get('reason') or prods.get('error')}")
        # A post with no real photo and no real video is not inventory — retire
        # it before the feed is built so it never shows up as a listing.
        retired = db.sync_media_retirement()
        if retired.get("ok"):
            print(f"[FindYourTrade] retired {retired.get('retired', 0)} posts with no real media")
        print(f"[FindYourTrade] catalogue: {catalog.refresh(force=True)}")

        # Integrity pass: fingerprint every local post photo and flag duplicates
        # and photo/label mismatches before the feed is served. The startup scan
        # is local-only (hashes + stored vision/human verdicts, no network);
        # listings whose photos nobody has identified are labelled unknown, and
        # live vision upgrades them on demand through /api/integrity/check.
        vision_labels = db.fetch_vision_labels()
        brand_facts = db.fetch_brand_facts()
        print(f"[FindYourTrade] brand knowledge: {len(brand_facts)} model facts from the web")
        scanned = integrity.scan_listings(catalog.listings(), catalog.products(),
                                          vision_labels=vision_labels,
                                          brand_facts=brand_facts)
        for row in scanned:
            db.update_listing_integrity(row["id"], row["photo_hash"], row["product_id"],
                                        row["verdict"], row["flags"])
        if scanned:
            catalog.refresh(force=True)
            flagged = [r for r in scanned if r["verdict"] not in ("ok", "unknown_photo")]
            print(f"[FindYourTrade] integrity: {len(scanned)} photos scanned, {len(flagged)} flagged "
                  f"({', '.join(r['id'] for r in flagged) or 'none'})")
    except Exception as e:
        print(f"Warning: Database bootstrap error: {e}")
    print(f"[FindYourTrade] AI mode: {config.mode_label()}")
    yield


app = FastAPI(title="FindYourTrade API", version="1.1.0", lifespan=lifespan)
# Dataset photos are served from the backend so posts can reference them directly.
app.mount("/dataset", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "dataset")), name="dataset")
# Trader-uploaded photos live here (served at /uploads/<file>).
UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
app.add_middleware(
    CORSMiddleware,
    # Wildcard origins cannot be combined with credentials, so credentials are
    # only enabled once an explicit allow-list is configured.
    allow_origins=config.CORS_ORIGINS,
    allow_credentials="*" not in config.CORS_ORIGINS,
    allow_methods=["*"], allow_headers=["*"],
)

# In-memory Show Me state: listing_id -> {code_word, issued_at, result}
SHOWME: dict[str, dict] = {}


def _trace(*results: dict) -> list[dict]:
    """Compact, UI-friendly record of which models answered."""
    out = []
    for r in results:
        if not r:
            continue
        out.append({
            "model": r.get("model", ""),
            "op": r.get("op", ""),
            "ok": not r.get("is_mock", False),
            "is_mock": bool(r.get("is_mock")),
            "cached": bool(r.get("cached")),
            "latency_ms": r.get("latency_ms", 0),
        })
    return out


def to_listing_out(l: dict) -> ListingOut:
    s = l["seller"] if isinstance(l.get("seller"), dict) else catalog.seller_by_id(l["seller_id"])
    fx = l.get("forex")
    if isinstance(fx, dict):
        from .models import ForexFootprint
        fx = ForexFootprint(**fx)
    if fx is None:
        fx = score_footprint(l["origin"], l["stock_status"], s["distance_km"], l["reusable"])
    return ListingOut(
        id=l["id"], seller=SellerOut(**s), item=l["item"], category=l["category"],
        brand=l.get("brand", ""), model=l.get("model", ""), price_mwk=l.get("price_mwk", 0),
        quantity_available=l.get("quantity_available", 0), unit=l.get("unit", ""),
        origin=l["origin"], stock_status=l["stock_status"], reusable=l["reusable"],
        registration_status=l.get("registration_status", ""),
        odometer_km=l.get("odometer_km"),
        verification_status=SHOWME.get(l["id"], {}).get("result", {}).get("verdict", l["verification_status"]) if isinstance(SHOWME.get(l["id"], {}).get("result"), dict) else l["verification_status"],
        photo=l.get("photo", ""),
        video_url=l.get("video_url", ""),
        photo_hash=l.get("photo_hash", ""),
        photo_product_id=l.get("photo_product_id", ""),
        fraud_verdict=l.get("fraud_verdict", ""),
        fraud_flags=l.get("fraud_flags") or [],
        forex=fx, rank_explanation=l.get("rank_explanation", ""),
    )


# ────────────────────────────────────────────────────────────── health & models

@app.get("/api/health")
def health():
    return {
        "ok": True,
        "mode": config.mode_label(),
        "database": db.check_db_health(),
        "media": media.capabilities(),
        "config": config.public_config(),
        "telemetry": meta_client.telemetry(limit=1),
    }


@app.get("/api/models/status")
def models_status():
    """What is configured, whether it is live, and recent call telemetry."""
    tel = meta_client.telemetry(limit=40)
    return {
        **config.public_config(),
        "media": media.capabilities(),
        "telemetry": tel,
        "cache_entries": meta_client._CACHE.size(),
    }


@app.post("/api/models/selftest")
async def models_selftest():
    """Ping each model with a minimal request and report per-slot status."""
    return await meta_client.selftest()


@app.get("/api/models/telemetry")
def models_telemetry(limit: int = 40):
    return meta_client.telemetry(limit=limit)


@app.post("/api/models/reset")
def models_reset():
    meta_client.reset_telemetry()
    meta_client._CACHE.clear()
    return {"ok": True, "message": "telemetry and response cache cleared"}


# ──────────────────────────────────────────────────────────────── db (pgvector)

@app.get("/api/db/status")
def db_status():
    return {**db.check_db_health(), "catalog": catalog.stats()}


@app.post("/api/db/seed")
def db_seed():
    """Re-upsert the bootstrap catalogue, then reload it from Postgres."""
    result = db.seed_catalog(force=True)
    catalog.refresh(force=True)
    return {**result, "catalog": catalog.stats()}


@app.post("/api/db/clean-listings")
def db_clean_listings():
    """Wipe all items and listings from the database, leaving sellers and trader accounts intact."""
    result = db.clean_all_listings()
    catalog.refresh(force=True)
    return {**result, "catalog": catalog.stats()}


@app.post("/api/db/vector-search")
def db_vector_search(body: dict):
    text = body.get("text", "")
    limit = int(body.get("limit", 10))
    category = body.get("category")
    results = db.search_listings_vector(text, limit=limit, category=category)
    return {"query": text, "count": len(results), "results": results}


# ─────────────────────────────────────────── product dataset & photo integrity

@app.get("/api/dataset/stats")
def dataset_stats():
    """What the model is trained on: products per vertical, photos, fingerprints."""
    products = catalog.products()
    verticals: dict[str, int] = {}
    for p in products:
        verticals[p["vertical"]] = verticals.get(p["vertical"], 0) + 1
    feedback = db.fetch_feedback_stats()
    source_count: dict[str, int] = {}
    for l in catalog.listings():
        for f in (l.get("fraud_flags") or []):
            s = f.get("vision_source", "")
            if s:
                source_count[s] = source_count.get(s, 0) + 1
    return {
        "products": len(products),
        "verticals": verticals,
        "categories": len({p["category"] for p in products}),
        "photos": sum(1 for p in products if p["photo_file"]),
        "unique_photo_hashes": len({p["photo_hash"] for p in products if p["photo_hash"]}),
        "version": db.PRODUCTS_VERSION,
        "vision_mode": "live" if meta_client.has_meta_key() else "hash_fallback",
        "vision_model": meta_client.MODEL_SPARK,
        "integrity": {
            "checked_listings": sum(1 for l in catalog.listings() if l.get("photo_hash")),
            "flagged_listings": sum(1 for l in catalog.listings()
                                    if (l.get("fraud_verdict") or "") not in ("", "ok", "unknown_photo")),
            "verdict_sources": source_count,
        },
        "reinforcement": feedback,
    }


@app.get("/api/dataset/products")
def dataset_products(vertical: str = "", category: str = "", q: str = "", limit: int = 200):
    """Browse the labelled dataset, optionally filtered."""
    needle = (q or "").lower()
    out = []
    for p in catalog.products():
        if vertical and p["vertical"] != vertical:
            continue
        if category and p["category"] != category:
            continue
        if needle and needle not in (p["title"] + " " + p["brand"] + " " + p["model"] + " " + p["label"]).lower():
            continue
        out.append(p)
    return {"count": len(out[:limit]), "total": len(out), "products": out[:limit]}


@app.get("/api/integrity/flags")
def integrity_flags():
    """Posts the fraud scan caught: verdict, evidence, and how vision knows.

    Each entry carries `vision` (what live vision saw: brand/model/confidence),
    `vision_source` (live_vision / vision_cache / human / hash_fallback /
    unverified) and `photo_hash` so the UI can confirm/dispute the verdict —
    the reinforcement signal the vision policy learns from.
    """
    labels = db.fetch_vision_labels()
    flagged = []
    for l in catalog.listings():
        verdict = l.get("fraud_verdict") or ""
        if verdict in ("", "ok", "unknown_photo"):
            continue
        seller = catalog.seller_by_id(l["seller_id"]) or {}
        product = catalog.product_by_id(l.get("photo_product_id") or "")
        vision = labels.get(l.get("photo_hash", "")) if l.get("photo_hash") else None
        flags = l.get("fraud_flags") or []
        source = (vision and vision.get("brand") and "vision_cache") or ""
        if vision and vision.get("source") == "human":
            source = "human"
        for g in flags:
            if g.get("vision_source"):
                source = g["vision_source"]
                break
        if not source and l.get("photo_product_id"):
            source = "hash_fallback"
        flagged.append({
            "listing_id": l["id"],
            "item": l["item"],
            "brand": l.get("brand", ""),
            "photo": l.get("photo", ""),
            "photo_hash": l.get("photo_hash", ""),
            "verdict": verdict,
            "flags": flags,
            "vision": ({
                "brand": vision.get("brand", ""), "model": vision.get("model", ""),
                "category": vision.get("category", ""),
                "confidence": vision.get("confidence", 0),
                "features": vision.get("features", []),
                "confirmations": vision.get("confirmations", 0),
                "disputes": vision.get("disputes", 0),
            } if vision else None),
            "vision_source": source or "unverified",
            "seller": {"id": seller.get("id", ""), "name": seller.get("name", "")},
            "photo_product": ({"id": product["id"], "title": product["title"],
                               "brand": product["brand"]} if product else None),
        })
    summary: dict[str, int] = {}
    for f in flagged:
        summary[f["verdict"]] = summary.get(f["verdict"], 0) + 1
    return {
        "count": len(flagged),
        "summary": summary,
        "checked": sum(1 for l in catalog.listings() if l.get("photo_hash")),
        "flagged": flagged,
    }


@app.post("/api/integrity/check")
async def integrity_check(item: str = Form(""), brand: str = Form(""), model: str = Form(""),
                          photo: str = Form(""), force_vision: str = Form(""),
                          file: UploadFile | None = File(default=None)):
    """Check a would-be post before it is published — LIVE VISION inside.

    Send an image upload, or `photo=/dataset/images/<file>.jpg` / an Unsplash
    id / an http(s) URL. The photo goes to Muse Spark vision, which says what
    it actually shows; duplicates are still caught by perceptual hash. The
    fresh verdict is stored, so later identical photos cost zero model calls.

    `force_vision=true` re-examines even a cached verdict (used after disputes).
    The response names `vision_source`: live_vision / vision_cache / human /
    hash_fallback / unverified — the platform never pretends to know.
    """
    data = b""
    if file is not None:
        data = await file.read()

    photo_hash = ""
    if data:
        photo_hash = integrity.dhash(data)
    elif photo:
        path = integrity.photo_path(photo)
        if path and os.path.exists(path):
            with open(path, "rb") as fh:
                data = fh.read()
            photo_hash = integrity.dhash(data)
        else:
            data = await integrity.load_image_bytes(photo)
            photo_hash = integrity.dhash(data)

    claim = {"item": item or "(unnamed)", "brand": brand, "model": model}
    result = await integrity.check_photo_live(
        data, photo_hash, claim,
        products=catalog.products(), listings=catalog.listings(),
        vision_labels=db.fetch_vision_labels(),
        force_vision=force_vision.lower() in ("1", "true", "yes"),
        brand_facts=db.fetch_brand_facts())
    if result.get("live_identification"):
        db.upsert_vision_label(photo_hash, result["live_identification"])
        result = {**result,
                  "vision_source": "live_vision",
                  "stored": True}
    return {"claim": claim, "photo_hash": photo_hash, **result}


@app.post("/api/integrity/scan-vision")
async def integrity_scan_vision(limit: int = 10, force: bool = False):
    """Run live vision over unscanned or disputed listing photos (bounded).

    Without a META_API_KEY this reports which photos need vision and returns
    them unexamined. With a key it identifies up to `limit` photos, stores the
    verdicts and re-runs the flags. This is how unknown_photo posts graduate
    to ok/mismatch — vision looks, humans confirm or correct (the RL loop).
    """
    listings = catalog.listings()
    products = catalog.products()
    labels = db.fetch_vision_labels()

    todo = []
    for l in listings:
        path = integrity.photo_path(l.get("photo", ""))
        h = integrity.dhash_file(path) if path else ""
        h = h or l.get("photo_hash", "")
        if not h:
            continue
        store = labels.get(h)
        call, reason = integrity.needs_live_call(h, store)
        if call or force:
            todo.append((l, h, reason))
    todo = todo[:max(1, limit)]

    examined = 0
    for l, h, _reason in todo:
        data = await integrity.load_image_bytes(l.get("photo", ""))
        claim = {"item": l.get("item", ""), "brand": l.get("brand", ""),
                 "model": l.get("model", "")}
        res = await integrity.check_photo_live(
            data, h, claim, products=products, listings=listings,
            vision_labels=db.fetch_vision_labels(), force_vision=force,
            exclude_listing_id=l["id"], brand_facts=db.fetch_brand_facts())
        if res.get("live_identification"):
            db.upsert_vision_label(h, res["live_identification"])
            examined += 1

    vision_labels = db.fetch_vision_labels()
    scanned = integrity.scan_listings(listings, products, vision_labels=vision_labels,
                                      brand_facts=db.fetch_brand_facts())
    for row in scanned:
        db.update_listing_integrity(row["id"], row["photo_hash"], row["product_id"],
                                    row["verdict"], row["flags"])
    catalog.refresh(force=True)
    feedback = db.fetch_feedback_stats()
    return {"examined": examined, "candidates": len(todo),
            "live": meta_client.has_meta_key(),
            "reinforcement": feedback,
            "rescan": catalog.stats()}


@app.post("/api/integrity/feedback")
async def integrity_feedback(body: dict):
    """Reinforcement signal for the vision policy: confirm or dispute.

    Body: {photo_hash, listing_id?, action: "confirm"|"dispute",
           corrected_brand?, corrected_model?}.

    A dispute with a correction becomes human ground truth and immediately
    re-runs that photo's flags, so the platform learns in one step. A dispute
    without a correction schedules vision to look again
    (next scan-vision re-examines it).
    """
    photo_hash = (body.get("photo_hash") or "").strip()
    listing_id = (body.get("listing_id") or "").strip()
    action = (body.get("action") or "").strip().lower()
    corrected_brand = (body.get("corrected_brand") or "").strip()
    corrected_model = (body.get("corrected_model") or "").strip()
    if not photo_hash:
        raise HTTPException(400, "photo_hash is required")
    if action not in ("confirm", "dispute"):
        raise HTTPException(400, "action must be 'confirm' or 'dispute'")

    result = db.record_integrity_feedback(photo_hash, listing_id, action,
                                          corrected_brand, corrected_model)
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "could not record feedback"))

    # A human correction is ground truth — re-run this listing's flags now.
    relearned = None
    if action == "dispute" and (corrected_brand or corrected_model):
        target = catalog.listing_by_id(listing_id) if listing_id else None
        if target is None and photo_hash:
            target = next((l for l in catalog.listings()
                           if l.get("photo_hash") == photo_hash), None)
        if target is not None:
            claim = {"item": target.get("item", ""), "brand": target.get("brand", ""),
                     "model": target.get("model", "")}
            out = integrity.check_photo(
                photo_hash, claim, products=catalog.products(),
                listings=catalog.listings(), exclude_listing_id=target["id"],
                vision_labels=db.fetch_vision_labels(),
                brand_facts=db.fetch_brand_facts())
            db.update_listing_integrity(target["id"], photo_hash,
                                        out.get("product_id", ""),
                                        out["verdict"], out["flags"])
            catalog.refresh(force=True)
            relearned = {"listing_id": target["id"], "verdict": out["verdict"],
                         "flags": out["flags"]}
    return {**result, "relearned": relearned}

# ───────────────────────────────────────── trader auth + verified posting

def _bearer_token(req) -> str:
    auth = (req.headers.get("authorization") or "")
    return auth[7:] if auth.lower().startswith("bearer ") else ""


def _require_trader(req):
    acct = db.account_from_token(_bearer_token(req))
    if not acct:
        raise HTTPException(401, "Please sign up or log in as a trader first.")
    return acct


@app.post("/api/trader/signup")
def trader_signup(body: dict):
    """Public signup: name + phone + PIN → trader account and seller row.
    business_category + email are stored on the seller row when given."""
    res = db.signup_trader(body.get("name", ""), body.get("phone", ""),
                           body.get("pin", ""), body.get("location", "Lilongwe"),
                           body.get("business_category", ""), body.get("email", ""))
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "signup failed"))
    catalog.refresh(force=True)
    return res


@app.post("/api/trader/login")
def trader_login(body: dict):
    """Phone + PIN login → fresh token."""
    res = db.login_trader(body.get("phone", ""), body.get("pin", ""))
    if not res.get("ok"):
        raise HTTPException(401, res.get("error", "login failed"))
    return res


@app.post("/api/trader/profile")
async def trader_profile(req: Request):
    """Edit my business identity: category, email, phone, location.
    Stored on the seller row — the database is the source of truth."""
    acct = _require_trader(req)
    body = await req.json()
    res = db.update_seller_profile(
        acct["seller_id"], body.get("business_category", ""),
        body.get("email", ""), body.get("phone", ""),
        body.get("location", ""))
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "could not save"))
    catalog.refresh(force=True)
    return res


@app.get("/api/trader/me")
def trader_me(req: Request):
    """Who am I logged in as (seller row + verification state + my posts)."""
    acct = _require_trader(req)
    seller = catalog.seller_by_id(acct["seller_id"]) or {}
    mine = [l for l in catalog.listings() if l["seller_id"] == acct["seller_id"]]
    posts = [{
        "id": l["id"], "item": l["item"], "price_mwk": float(l.get("price_mwk") or 0),
        "quantity_available": l.get("quantity_available", 1),
        "photo": l.get("photo", ""), "video_url": l.get("video_url", ""),
        "verification_status": l.get("verification_status", ""),
        "fraud_verdict": l.get("fraud_verdict", ""),
        "flagged": bool(integrity.is_fraud(l)),
    } for l in sorted(mine, key=lambda x: x.get("created_at", ""), reverse=True)]
    return {"account": acct, "seller": seller,
            "my_listings": [l["id"] for l in mine],
            "my_posts": posts, "posts_count": len(mine)}


@app.post("/api/trader/photo-verify")
async def trader_photo_verify(req: Request, file: UploadFile = File(...)):
    """Step 1 of posting: upload the actual photo of YOUR goods.

    Muse decides what the picture shows — identification, pass/fail and the
    suggested details all come from the model, Python only stores and reports.
    A verified photo unlocks posting. The file is kept under /uploads.
    """
    acct = _require_trader(req)
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty photo upload.")
    photo_hash = integrity.dhash(data)
    if not photo_hash:
        raise HTTPException(400, "That file is not a readable photo. Please upload a JPG or PNG.")

    ext = (os.path.splitext(file.filename or "")[1] or ".jpg").lower()[:5]
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    fname = f"{acct['seller_id']}_{photo_hash[:12]}{ext}"
    try:
        with open(os.path.join(UPLOADS_DIR, fname), "wb") as fh:
            fh.write(data)
    except OSError as e:
        # Never let a storage problem take the whole API down — the app mount
        # can be read-only, and an unhandled OSError here used to kill uvicorn
        # (which surfaced to the browser as a 502 on every later request).
        logger.error("could not store verification photo: %s", e)
        raise HTTPException(503, "The server cannot store photos right now. "
                                 "Please try again in a moment.") from e

    claim = {"item": "(verification photo)", "brand": "", "model": ""}
    vision_out = await integrity.check_photo_live(
        data, photo_hash, claim, products=catalog.products(),
        listings=catalog.listings(), vision_labels=db.fetch_vision_labels(),
        brand_facts=db.fetch_brand_facts())
    if vision_out.get("live_identification"):
        db.upsert_vision_label(photo_hash, vision_out["live_identification"])

    vision = vision_out.get("vision") or vision_out.get("live_identification")
    verdict = (vision_out.get("verdict") or "").lower()
    # Muse decides. usable = the model says it can see sellable goods (Match /
    # usable verdict, or any non-empty identification). Python does not
    # second-guess with brand/confidence thresholds — a pizza has no brand.
    v = vision or {}
    usable = bool(v and (
        v.get("brand") or v.get("model")
        or (v.get("features") or [None])[0]
        or (v.get("notes") or "").strip()))
    identified = usable or verdict in ("match", "usable", "verified")
    label = ((v.get("features") or [""])[0]
             or f"{v.get('brand', '')} {v.get('model', '')}".strip()
             or "your goods")
    if identified:
        db.set_photo_verified(acct["seller_id"], True)
    catalog.refresh(force=True)
    details = _suggest_details(v, claim_label=label)
    return {
        "photo": f"/uploads/{fname}", "photo_hash": photo_hash,
        "identified": identified,
        "vision": vision,
        "vision_source": vision_out.get("vision_source", "unverified"),
        "suggested": details,
        "message": (f"Photo verified — this picture shows {label}. "
                    "You can now post your item." if identified else
                    "Vision could not identify a product in this photo. Please upload "
                    "a clear picture of the actual goods you are selling."),
    }


def _suggest_details(vision: dict, *, claim_label: str = "") -> dict:
    """Turn Muse's verdict into a ready-to-post draft: title, brand, model,
    category, quantity and a fair price from the closest catalogue neighbours.
    Everything is a suggestion — Muse decides, the trader confirms by posting.
    """
    v = vision or {}
    feats = v.get("features") or []
    title_bits = [f for f in (v.get("brand", ""), v.get("model", "")) if f]
    title = " ".join(title_bits).strip() or (feats[0] if feats else claim_label)
    brand = v.get("brand", "")
    # Fair price: median of live posts in the same category (or same brand),
    # exactly the rule describe-photo has always used.
    prices = sorted(float(l.get("price_mwk") or 0)
                    for l in catalog.listings()
                    if (l.get("category", "") == (v.get("category") or "general")
                        or (brand and l.get("brand", "").lower() == brand.lower()))
                    and float(l.get("price_mwk") or 0) > 0)
    suggested = prices[len(prices) // 2] if prices else 0
    return {
        "item": title,
        "brand": brand,
        "model": v.get("model", ""),
        "category": v.get("category", "") or "general",
        "vertical": v.get("vertical", ""),
        "quantity_available": 1,
        "unit": "unit",
        "suggested_price_mwk": round(suggested, -3) if suggested else 0,
        "price_basis": (f"median of {len(prices)} similar market posts"
                        if prices else "no comparables yet"),
    }


@app.post("/api/trader/video-verify")
async def trader_video_verify(req: Request, file: UploadFile = File(...)):
    """Step 1 (video alternative): upload a short clip of YOUR goods instead of
    a photo. Frames are sampled locally and Muse Spark watches them in order —
    the same structured identification as a photo (brand, model, category,
    confidence). A verified clip unlocks posting exactly like a verified photo.
    The clip is kept under /uploads.
    """
    acct = _require_trader(req)
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty video upload.")
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(400, "That video is over 100 MB — please upload a shorter clip.")

    ext = (os.path.splitext(file.filename or "")[1] or ".mp4").lower()[:5]
    if ext not in (".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"):
        ext = ".mp4"
    vhash = integrity.dhash(data)
    fname = f"{acct['seller_id']}_{(vhash or 'clip')[:12]}{ext}"
    try:
        with open(os.path.join(UPLOADS_DIR, fname), "wb") as fh:
            fh.write(data)
    except OSError as e:
        logger.error("could not store verification video: %s", e)
        raise HTTPException(503, "The server cannot store videos right now. "
                                 "Please try again in a moment.") from e

    sampled = media.extract_frames(data, filename=file.filename or "clip.mp4")
    frames = sampled.get("frames", [])
    vision, source = None, "unverified"
    if frames and config.is_live():
        try:
            ident = await meta_client.identify_video(
                frames, claim={"item": "(verification video)", "brand": "", "model": ""})
            trace_note = ident.get("latency_ms", 0)
            if ident.get("identification"):
                vision = ident["identification"]
                source = "live_vision"
                db.upsert_vision_label(vhash, vision)
        except meta_client.MetaError:
            pass
    if not vision and vhash:
        stored = (db.fetch_vision_labels() or {}).get(vhash)
        if stored:
            vision, source = stored, "cache"

    # Muse decides, same rule as photos: usable = the model names sellable
    # goods in any field. Python does not second-guess.
    v = vision or {}
    usable = bool(v and (
        v.get("brand") or v.get("model")
        or (v.get("features") or [None])[0]
        or (v.get("notes") or "").strip()))
    identified = usable
    label = ((v.get("features") or [""])[0]
             or f"{v.get('brand', '')} {v.get('model', '')}".strip()
             or "your goods")
    if identified:
        db.set_photo_verified(acct["seller_id"], True)
    catalog.refresh(force=True)
    details = _suggest_details(v, claim_label=label)
    return {
        "video": f"/uploads/{fname}", "video_hash": vhash,
        "identified": identified,
        "vision": vision,
        "vision_source": source,
        "frames_used": len(frames),
        "frame_note": sampled.get("note", ""),
        "suggested": details,
        "message": (f"Video verified — this clip shows {label}. "
                    "You can now post your item." if identified else
                    "Vision could not identify the goods in this video. Please upload "
                    "a clear clip of the actual goods you are selling."),
    }


@app.post("/api/trader/post")
async def trader_post(req: Request):
    """Step 2 of posting (logged-in, photo-verified traders only).

    Body: {item, brand, model, category, price_mwk, quantity_available, unit,
           origin, stock_status, photo} where photo is the /uploads path from
    photo-verify. The post runs the FULL integrity gate (live vision +
    duplicates + web knowledge) before it is stored: fraud never reaches the
    dashboard — it lands in Integrity for audit instead.
    """
    acct = _require_trader(req)
    fresh = db.account_from_token(_bearer_token(req)) or acct
    if not fresh.get("photo_verified"):
        raise HTTPException(403, "Please verify a photo of your goods first (step 1).")
    body = await req.json()
    item = (body.get("item") or "").strip()
    if len(item) < 3:
        raise HTTPException(400, "Please describe your item (at least 3 characters).")
    try:
        price = float(body.get("price_mwk") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "Price must be a number.")
    if price <= 0:
        raise HTTPException(400, "Please set a price in MWK.")

    photo = (body.get("photo") or "").strip()
    data = b""
    photo_hash = ""
    if photo.startswith("/uploads/"):
        p = os.path.join(UPLOADS_DIR, os.path.basename(photo))
        if os.path.exists(p):
            with open(p, "rb") as fh:
                data = fh.read()
            photo_hash = integrity.dhash(data)

    claim = {"item": item, "brand": body.get("brand", ""), "model": body.get("model", "")}
    verdict = await integrity.check_photo_live(
        data, photo_hash, claim, products=catalog.products(),
        listings=catalog.listings(), vision_labels=db.fetch_vision_labels(),
        brand_facts=db.fetch_brand_facts())
    if verdict.get("live_identification"):
        db.upsert_vision_label(photo_hash, verdict["live_identification"])
    flags = verdict.get("flags", [])
    is_fraud = any(f.get("severity") == "fraud" for f in flags)

    res = db.create_trader_listing(acct["seller_id"], {
        **body, "photo": photo, "photo_hash": photo_hash,
        "photo_product_id": verdict.get("product_id", ""),
        "fraud_verdict": verdict.get("verdict", ""),
        "fraud_flags": flags,
    })
    if not res.get("ok"):
        raise HTTPException(500, res.get("error", "could not save post"))
    # A fresh post with its verified photo is real inventory: sync retirement
    # and reload the catalogue now so the post is on home/dashboard instantly.
    db.sync_media_retirement()
    catalog.refresh(force=True)
    return {"ok": True, "listing_id": res["listing_id"],
            "verdict": verdict.get("verdict", ""),
            "vision": verdict.get("vision"),
            "vision_source": verdict.get("vision_source", ""),
            "flagged": is_fraud,
            "flags": flags,
            "message": ("Your post is live on the dashboard." if not is_fraud else
                        "Your post was saved but flagged by the fraud scan — it is held "
                        "out of the dashboard until it is reviewed. See the Integrity tab.")}


@app.post("/api/trader/describe-photo")
async def trader_describe_photo(req: Request, file: UploadFile = File(...)):
    """Photo-to-listing in one shot: upload any photo, vision identifies the
    product and the platform pre-fills brand/model/category, suggests a fair
    price (vertical median), and runs the fraud pre-check — before the trader
    types a single word. Auth required (traders only)."""
    _require_trader(req)
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty photo upload.")
    photo_hash = integrity.dhash(data)
    if not photo_hash:
        raise HTTPException(400, "That file is not a readable photo.")

    ident = None
    trace: list[dict] = []
    if config.is_live():
        try:
            res = await meta_client.identify_product(data)
            trace = _trace(res)
            ident = res.get("identification")
        except meta_client.MetaError as exc:
            trace = _trace({"model": meta_client.MODEL_SPARK, "op": "identify",
                            "is_mock": True, "cached": False, "latency_ms": 0,
                            "note": str(exc)})
    if not ident:
        return {"identified": False, "trace": trace,
                "message": "Vision could not identify this photo. Describe your item by hand."}

    brand, model = ident.get("brand", ""), ident.get("model", "")
    # Fair-price suggestion: median of the same vertical in the live market.
    prices = sorted(float(l.get("price_mwk") or 0)
                    for l in catalog.listings()
                    if (l.get("category", "") == (ident.get("category") or "general")
                        or l.get("brand", "").lower() == brand.lower())
                    and float(l.get("price_mwk") or 0) > 0)
    suggested = prices[len(prices) // 2] if prices else 0
    # Fraud pre-check on the identified truth vs empty claim.
    claim = {"item": f"{brand} {model}".strip(), "brand": brand, "model": model}
    pre = integrity.check_photo(photo_hash, claim, products=catalog.products(),
                                listings=catalog.listings(),
                                vision_labels=db.fetch_vision_labels(), live=ident,
                                brand_facts=db.fetch_brand_facts())
    return {"identified": True, "brand": brand, "model": model,
            "category": ident.get("category", "general"),
            "confidence": ident.get("confidence", 0),
            "features": ident.get("features", []),
            "suggested_price_mwk": round(suggested, -3) if suggested else 0,
            "price_basis": f"median of {len(prices)} similar market posts" if prices else "no comparables",
            "vision_source": pre.get("vision_source", "live_vision"),
            "precheck_verdict": pre.get("verdict", ""),
            "precheck_flags": pre.get("flags", []),
            "trace": trace}

@app.post("/api/trader/regenerate-photo")
async def trader_regenerate_photo(req: Request, body: dict):
    """After verification: generate a clean studio-style trade card of the
    trader's OWN verified goods with Muse Image (never stock, never fake)."""
    acct = _require_trader(req)
    listing_id = (body.get("listing_id") or "").strip()
    l = catalog.listing_by_id(listing_id)
    if not l or l["seller_id"] != acct["seller_id"]:
        raise HTTPException(404, "Unknown listing (or not yours).")
    photo_ref = l.get("photo", "")
    ppath = ""
    if photo_ref.startswith("/uploads/"):
        ppath = os.path.join(UPLOADS_DIR, os.path.basename(photo_ref))
    elif photo_ref.startswith("/dataset/"):
        ppath = integrity.photo_path(photo_ref)
    if not ppath or not os.path.exists(ppath):
        raise HTTPException(400, "No verified photo on this post to regenerate from.")
    with open(ppath, "rb") as fh:
        original = fh.read()
    # Vision describes the verified goods; that description (not the seller's
    # words) drives the regeneration, so the card cannot drift from reality.
    desc = ""
    if config.is_live():
        try:
            res = await meta_client.identify_product(original)
            trace = _trace(res)
            ident = res.get("identification") or {}
            seen = " ".join(x for x in (ident.get("brand"), ident.get("model")) if x)
            feats = "; ".join(ident.get("features") or [])
            desc = f"{seen} ({feats})" if seen else (ident.get("notes") or l["item"])
        except meta_client.MetaError:
            desc = l["item"]
            trace = []
    else:
        desc = l["item"]
        trace = []
    if not desc or not desc.strip():
        desc = l["item"]
    prompt = (f"Clean e-commerce studio photo of {desc}, Malawi market trade card, "
              f"bright daylight, neutral background, no people, no text overlay")
    gen = await meta_client.generate_trade_card(prompt)
    if gen.get("image") is None:
        return {"ok": False, "listing_id": listing_id, "description": desc,
                "mock": True, "trace": trace,
                "message": "Image generation needs a live model key — your verified photo stays as the post image."}
    # The API returns b64_json sized for a chat bubble, not an <img>. Persist
    # the card to /uploads and hand the frontend a URL it can actually show —
    # the same card the promo video is later built from.
    card_name = f"{l['seller_id']}_{listing_id}_card.jpg"
    card_path = os.path.join(UPLOADS_DIR, card_name)
    payload = gen.get("image") or {}
    b64 = (payload.get("data") or [{}])[0].get("b64_json", "") if isinstance(payload, dict) else ""
    saved = media.studio_card_from_b64(b64, card_path) if b64 else False
    if not saved:
        return {"ok": False, "listing_id": listing_id, "description": desc,
                "trace": trace + _trace(gen),
                "message": "Muse Image answered but the card would not decode — your verified photo stays as the post image."}
    card_url = f"/uploads/{card_name}"
    return {"ok": True, "listing_id": listing_id, "description": desc,
            "image": card_url, "model": gen.get("model"),
            "trace": trace + _trace(gen)}


async def _veto_unseen(plan_data: dict, *, listing: dict, trace: list) -> dict:
    """Retired for the MVP showcase: the trader's prompt goes through
    untouched, nothing is cut. Kept (unused) so the honesty gate can be
    switched back on later without re-deriving it.
    """
    return {"struck": [], "script": plan_data.get("script", ""),
            "shots": list(plan_data.get("shots") or [])}


@app.post("/api/trader/promo-video")
async def trader_promo_video(req: Request, body: dict):
    """Assemble a promo plan (≤10-second video) for the trader's verified listing:
    hook (Muse Wonder) + voiceover script + shot list, driven entirely by the
    trader's own prompt. MVP showcase mode: nothing is cut."""
    acct = _require_trader(req)
    listing_id = (body.get("listing_id") or "").strip()
    l = catalog.listing_by_id(listing_id)
    if not l or l["seller_id"] != acct["seller_id"]:
        raise HTTPException(404, "Unknown listing (or not yours).")
    s = catalog.seller_by_id(l["seller_id"]) or {}
    # MVP showcase: the trader's prompt goes in untouched and comes out
    # untouched — generate any prompted feature, no honesty gate, nothing cut.
    direction = (body.get("direction") or "").strip()[:300]
    plan: dict = {"hook": "", "script": "", "shots": [], "trace": [], "mock": True,
                  "direction_used": direction}
    if config.is_live():
        try:
            # Single user message, direction in the spine: this model spends
            # ~1700 reasoning tokens on multi-message briefs and dies by
            # "length" at 2000; a compact single message answers reliably.
            user_brief = (
                     f"You write 10-second market promo scripts for Malawian traders. "
                     f"Plain language, honest, no hype. Promo plan for {l['item']} "
                     f"({s.get('name', '')}, {s.get('location', '')}, "
                     f"MWK {l.get('price_mwk', 0):,.0f}). ")
            if direction:
                user_brief += (
                    f"Honour this trader direction: {direction}. "
                    f"One shot must be the trader filming themselves acting it out "
                    f"with the real goods. ")
            user_brief += ('Return ONLY JSON: {"hook": "one opening line", '
                    '"script": "25-35 word voiceover (fits a 10-second read)", '
                    '"shots": ["shot 1", "shot 2", "shot 3", "shot 4"]}')
            res = await meta_client.chat(
                config.MODEL_WONDER,
                [{"role": "user", "content": user_brief}],
                # Creative, per-trader input: never serve a cached plan from
                # another brief. Fresh Muse call every time. Retries below:
                # Wonder's reasoning intermittently eats the whole budget and
                # ends the call by "length" with empty text.
                max_tokens=8000, temperature=0.4, json_mode=True, op="promo_plan",
                cache=False)
            if not (res.get("text") or "").strip():
                res = await meta_client.chat(
                    config.MODEL_WONDER,
                    [{"role": "user", "content": user_brief}],
                    max_tokens=8000, temperature=0.2, json_mode=True,
                    op="promo_plan_retry", cache=False)
                plan["trace"] = _trace(res) + [{"op": "promo_plan_retry",
                                                "ok": bool((res.get("text") or "").strip())}]
            else:
                plan["trace"] = _trace(res)
            if res.get("data"):
                plan.update({k: res["data"].get(k, plan[k]) for k in ("hook", "script", "shots")})
                plan["mock"] = False
        except meta_client.MetaError:
            pass
    if not plan["script"]:
        plan.update({
            "hook": f"Fresh {l['item']} available in {s.get('location', 'Malawi')}!",
              "script": (f"Moni! {s.get('name', 'We')} have {l.get('quantity_available', 1)} "
                       f"{l['item']} for MWK {l.get('price_mwk', 0):,.0f}. Verified goods, fair price. "
                       "Come see them today, or chat with us right here on FindYourTrade."),
            "shots": ["Wide shot of the goods with today's code word card",
                      "Close-up of quality details", "Seller holding the goods, smiling",
                      "Price board + location sign-off"],
        })
    video_url = ""
    video_note = ""
    if plan.get("script"):
        # Real ≤10-second MP4 from the trader's verified card photo: slow zoom
        # over the actual goods plus a caption slate. ffmpeg, local, no actors.
        photo_ref = l.get("photo", "") or ""
        src = ""
        if photo_ref.startswith("/uploads/"):
            cand = os.path.join(UPLOADS_DIR, os.path.basename(photo_ref))
            if os.path.exists(cand):
                src = cand
        elif photo_ref.startswith("/dataset/"):
            cand = integrity.photo_path(photo_ref)
            if cand and os.path.exists(cand):
                src = cand
        if src:
            vname = f"{l['seller_id']}_{listing_id}_promo.mp4"
            vpath = os.path.join(UPLOADS_DIR, vname)
            caption = (f"{plan.get('hook', '')}\n{l.get('item', '')}\n"
                       f"{s.get('name', '')} - {s.get('location', '')}")
            price_line = f"MWK {l.get('price_mwk', 0):,.0f}"
            render = media.promo_video_from_image(
                src, vpath, duration_s=10, fps=24,
                caption=caption, price_line=price_line)
            video_note = render.get("note", "")
            if render.get("ok"):
                video_url = f"/uploads/{vname}"
                # Save the generated video URL back to the listing so it
                # (a) survives sync_media_retirement and (b) appears in the
                # dashboard feed without requiring another catalog refresh cycle.
                db.update_listing_video_url(listing_id, video_url)
                catalog.refresh(force=True)
            plan["trace"].append({"op": "promo_render", "ok": render.get("ok"),
                                  "note": video_note[:200]})
        else:
            video_note = "no verified photo on the post — promo plan only"
    return {"ok": True, "listing_id": listing_id, "video_url": video_url,
            "video_note": video_note, **plan}


# ──────────────────────────────── PyTorch fraud-risk scorer

@app.get("/api/integrity/model")
def integrity_model():
    """The PyTorch scorer: status, metrics, weights, version.

    The network learns from the ledger's own verdicts — every confirm/dispute
    makes the next training round smarter. Scores triage the review queue;
    they never convict (only vision, hashes and web facts do that).
    """
    saved = fraud_model.load_model()
    listings = catalog.listings()
    data = fraud_model.build_dataset(listings, catalog.sellers(),
                                     db.fetch_brand_facts())
    trainable = data is not None
    n, positives = (data[0].shape[0], int(data[1].sum().item())) if trainable else (0, 0)
    return {
        "torch": fraud_model._TORCH,
        "version": fraud_model.MODEL_VERSION,
        "trained": saved is not None,
        "features": fraud_model.FEATURE_NAMES,
        "metrics": (saved or {}).get("metrics"),
        "weights": (saved or {}).get("weights"),
        "trainable": trainable,
        "labelled_posts": n,
        "fraud_labels": positives,
    }


@app.post("/api/integrity/model/train")
def integrity_model_train():
    """Fit the PyTorch network on the current ledger verdicts."""
    res = fraud_model.train(catalog.listings(), catalog.sellers(),
                            db.fetch_brand_facts())
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "training failed"))
    return res


@app.post("/api/integrity/model/score")
async def integrity_model_score(body: dict):
    """Risk score for one post from the trained network.

    Body: full or partial listing {id?, item, brand, model, category,
    price_mwk, origin, verification_status, photo?, seller_id?}. Unknown
    listings score against catalogue medians; every score names its top
    contributing signals and the model version behind it.
    """
    saved = fraud_model.load_model()
    if saved is None:
        raise HTTPException(400, "model is not trained yet — POST /api/integrity/model/train first")
    listing = dict(body)
    if body.get("id") and not body.get("item"):
        found = catalog.listing_by_id(body["id"])
        if found is None:
            raise HTTPException(404, "unknown listing")
        listing = {**found, **{k: v for k, v in body.items() if v not in (None, "")}}
    sellers = {s["id"]: s for s in catalog.sellers()}
    counts: dict[str, int] = {}
    for l in catalog.listings():
        if l.get("photo_hash"):
            counts[l["photo_hash"]] = counts.get(l["photo_hash"], 0) + 1
    if listing.get("photo") and not listing.get("photo_hash"):
        path = integrity.photo_path(listing["photo"])
        listing["photo_hash"] = integrity.dhash_file(path) if path else ""
    return {**fraud_model.score(listing, sellers, counts, saved,
                                db.fetch_brand_facts()),
            "listing_id": listing.get("id", "")}


@app.get("/api/integrity/queue")
def integrity_queue():
    """Review queue: flagged posts first (rules), then unscanned posts ordered
    by the PyTorch risk score (triage). Clean scanned posts never appear."""
    flagged = []
    for l in catalog.listings():
        if (l.get("fraud_verdict") or "") in ("", "ok", "unknown_photo"):
            continue
        flagged.append({"listing_id": l["id"], "item": l.get("item", ""),
                        "verdict": l.get("fraud_verdict", ""),
                        "kind": "rule", "risk": 1.0})
    queued = []
    saved = fraud_model.load_model()
    if saved is not None:
        sellers = {s["id"]: s for s in catalog.sellers()}
        counts: dict[str, int] = {}
        for l in catalog.listings():
            if l.get("photo_hash"):
                counts[l["photo_hash"]] = counts.get(l["photo_hash"], 0) + 1
        facts = db.fetch_brand_facts()
        for l in catalog.listings():
            if (l.get("fraud_verdict") or "") in ("", "ok"):
                continue
            if l.get("fraud_verdict"):
                continue  # already convicted — in flagged, not triage
            try:
                r = fraud_model.score(l, sellers, counts, saved, facts)
            except Exception:
                continue
            queued.append({"listing_id": l["id"], "item": l.get("item", ""),
                           "verdict": "unscanned", "kind": "triage",
                           "risk": r["risk"], "band": r["band"],
                           "top_signals": r["top_signals"]})
        queued.sort(key=lambda q: -q["risk"])
    return {"flagged": flagged, "triage": queued[:20],
            "model_version": (saved or {}).get("version"),
            "torch": fraud_model._TORCH}


# ────────────────────────────────────────────────────────────── F1: understand

def _parsed_from_model(model_fields: dict, raw_text: str) -> ParsedRequest:
    """Merge Spark's structured extraction over the deterministic parser.

    The deterministic parser's "general" is sticky when the wanted noun is
    unknown to the market: the model may not upgrade an unknown thing into a
    stocked category (that is exactly the plates → cars hallucination).
    """
    from .matching import classify_query as _classify2
    base = parse_request_text(raw_text)
    merged = base.model_dump()
    _cat, _noun = _classify2(raw_text)
    if base.category == "general" and _noun:
        model_fields = {k: v for k, v in model_fields.items() if k != "category"}
    for key in ("item_type", "quantity", "budget_mwk", "location", "payment_preference", "category"):
        value = model_fields.get(key)
        if value not in (None, "", []):
            merged[key] = value
    merged["raw_text"] = raw_text
    return ParsedRequest(**merged)


@app.post("/api/parse", response_model=ParsedRequest)
async def parse(body: dict):
    text = body.get("text", "")
    parsed = parse_request_text(text)
    if config.is_live():
        try:
            res = await meta_client.parse_trade_request(text)
            if res.get("data"):
                parsed = _parsed_from_model(res["data"], text)
        except meta_client.MetaError as exc:
            print(f"[parse] Spark unavailable, rule parser used: {exc}")
    if body.get("user_id"):
        cats = recommender.interests_from_text(text)
        prof = recommender.get_profile(body["user_id"])
        for c in cats:
            prof["interests"][c] += 2.0
    return parsed


# ─────────────────────────────────────────────────────────── F1/F2: match + rank

@app.post("/api/match", response_model=MatchResponse)
async def match(body: dict):
    text = body.get("text", "")
    parsed = parse_request_text(text)
    trace: list[dict] = []
    note = ""

    if config.is_live():
        try:
            res = await meta_client.parse_trade_request(text)
            trace += _trace(res)
            if res.get("data"):
                parsed = _parsed_from_model(res["data"], text)
                note = f"Muse Spark parsed your request ({res.get('latency_ms', 0)} ms)."
        except meta_client.MetaError as exc:
            note = f"Rule parser used (Spark unavailable: {exc})."

    ranked = rank_listings(parsed)

    # Live ranking note via Muse Spark (Meta Model API). The
    # deterministic `top` above stays the source of truth; this note is the
    # model's one-line second opinion. Judge-style prompts make this reasoning
    # model deliberate without bound, so the prompt endorses-then-justifies
    # instead of ranking from scratch.
    if ranked and config.is_live():
        try:
            first = ranked[0]
            res = await meta_client.spark_rank(
                "TASK: You are a shopping assistant's ranking judge. A buyer needs "
                f"{parsed.quantity} x {parsed.item_type} near {parsed.location}. Our system "
                f"already ranked {first['id']} ({first['item']} by {first['seller']['name']}, "
                f"forex {first['forex'].score}/100, {first['verification_status']}) as the top "
                "pick. In ONE sentence, agree or correct this pick, starting with the listing id "
                "you endorse — for example 'l01 — best pick because ...'. Reply with that sentence only.",
                max_tokens=1024,
                expect_id=first["id"],
            )
            trace += _trace(res)
            if res.get("text"):
                note = (note + " " if note else "") + "Muse Spark: " + res["text"][:280]
        except meta_client.MetaError as exc:
            note = (note + f" (Spark ranker unavailable: {exc})").strip()

    if not note:
        note = f"Ranked {len(ranked)} sellers · {config.mode_label()} · scores are estimates."

    return MatchResponse(parsed=parsed, results=[to_listing_out(l) for l in ranked],
                         note=note, trace=trace)


@app.get("/api/listings")
def listings(include_quarantined: bool = False):
    """Every post, minus the quarantined ones by default.

    Repeated postings (same photo, different product) and wrong-brand records
    never reach the dashboard — they live on in /api/integrity/flags for audit.
    Pass include_quarantined=true for the full set.
    """
    out = []
    quarantined = 0
    for l in catalog.listings():
        if not include_quarantined and integrity.is_fraud(l):
            quarantined += 1
            continue
        seller = catalog.seller_by_id(l["seller_id"])
        if seller:
            out.append(to_listing_out({**l, "seller": seller}))
    return {"listings": out, "quarantined": quarantined,
            "source": catalog.stats()["source"]}


@app.get("/api/listings/{listing_id}")
def listing_detail(listing_id: str):
    l = catalog.listing_by_id(listing_id)
    if not l:
        raise HTTPException(404, "unknown listing")
    return to_listing_out({**l, "seller": catalog.seller_by_id(l["seller_id"])})


# ─────────────────────────────────────────────────────────────── voice input

@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...)):
    """Muse Voice Transcribe on an uploaded voice note."""
    audio = await file.read()
    res = await meta_client.transcribe_audio(audio, file.filename or "voice.webm")
    return {**res, "trace": _trace(res)}


# ───────────────────────────────────────────────────── F4: Show Me verification

@app.post("/api/showme/codeword", response_model=CodeWordResponse)
def issue_codeword(body: dict):
    listing_id = body.get("listing_id", "")
    if not catalog.listing_by_id(listing_id):
        raise HTTPException(404, "unknown listing")
    cw = meta_client.new_code_word()
    SHOWME[listing_id] = {"code_word": cw, "issued_at": time.time(), "result": None}
    return CodeWordResponse(listing_id=listing_id, code_word=cw)


@app.post("/api/showme/verify")
async def verify_showme(listing_id: str = "", claimed_count: int = 0, code_word_said: str = "",
                        file: UploadFile | None = File(default=None)):
    """Full Show Me pipeline (PRD §8):

    upload → OpenCV frame sampling → Muse Spark vision counts → ffmpeg audio → Muse Voice
    Transcribe → Muse Spark compares against the listing → Match/Partial/Mismatch.

    Without media (or without model keys) the check falls back to the
    self-reported count and code word, and the response says so.
    """
    l = catalog.listing_by_id(listing_id)
    if not l:
        raise HTTPException(404, "unknown listing")
    state = SHOWME.get(listing_id, {})
    expected = state.get("code_word", "")
    listing_qty = int(l.get("quantity_available", 0))

    video_bytes = await file.read() if file is not None else b""
    filename = (file.filename if file else "showme.mp4") or "showme.mp4"

    trace: list[dict] = []
    transcript = ""
    counted: int | None = None
    frames_used = 0
    pipeline_notes: list[str] = []

    # 1. Vision + audio extraction. Frame sampling always runs (it is local and
    #    free); the models are only called when credentials are present.
    if video_bytes:
        frames = media.extract_frames(video_bytes, filename)
        frames_used = frames["count"]
        if frames["count"]:
            if config.is_live():
                try:
                    res = await meta_client.count_items(frames["frames"], l["item"])
                    trace += _trace(res)
                    counted = res.get("count")
                    if res.get("notes"):
                        pipeline_notes.append(f"Vision count: {res['notes']}")
                except meta_client.MetaError as exc:
                    pipeline_notes.append(f"Vision counting unavailable: {exc}")
            else:
                pipeline_notes.append(
                    f"{frames['count']} frames sampled locally; vision counting skipped in mock mode.")
        else:
            pipeline_notes.append(f"No frames sampled ({frames.get('note') or 'unknown'}).")

        if config.is_live():
            audio = media.extract_audio(video_bytes, filename)
            if audio["ok"]:
                try:
                    res = await meta_client.transcribe_audio(audio["audio"], "audio.wav")
                    trace += _trace(res)
                    transcript = res.get("text", "")
                    if res.get("error"):
                        pipeline_notes.append(
                            f"Transcription failed: {res['error'].get('error', 'unknown')}")
                except meta_client.MetaError as exc:
                    pipeline_notes.append(f"Transcription unavailable: {exc}")
            else:
                # Muse Voice Transcribe only accepts mono 16-bit PCM WAV, so
                # sending the raw video container would just be rejected.
                pipeline_notes.append(
                    f"Audio track not extracted ({audio.get('note') or 'ffmpeg missing'}); "
                    "transcription skipped.")

    # Demo path: seller self-reports what the video shows.
    if counted is None:
        counted = claimed_count
        pipeline_notes.append("Item count self-reported (no vision model run).")
    if not transcript:
        transcript = code_word_said

    code_ok = bool(expected) and (
        code_word_said.strip().lower() == expected.lower()
        or expected.lower() in transcript.lower()
    )
    if not expected:
        code_ok = True

    # 2. Spark issues the verdict; deterministic rules are the fallback.
    verdict: str | None = None
    reasons: list[str] = []
    if config.is_live():
        try:
            res = await meta_client.verify_showme(l, expected, transcript, counted, frames_used=frames_used)
            trace += _trace(res)
            if res.get("data"):
                verdict = res["data"]["verdict"]
                reasons = res["data"].get("reasons") or []
        except meta_client.MetaError as exc:
            pipeline_notes.append(f"Spark verdict unavailable: {exc}")

    if not verdict:
        if code_ok and counted == listing_qty:
            verdict = "Match"
        elif code_ok or (counted and abs(counted - listing_qty)
                         <= max(COUNT_TOLERANCE_MIN, listing_qty * COUNT_TOLERANCE_RATIO)):
            verdict = "Partial match"
        else:
            verdict = "Mismatch"
        if counted == listing_qty and listing_qty > 0:
            reasons.append(f"Count matches listing ({listing_qty} {l.get('unit', '')}).")
        elif counted and listing_qty:
            reasons.append(f"Listing claims {listing_qty}, video shows {counted}.")
        if expected:
            reasons.append(f"Code word '{expected}' {'heard' if code_ok else 'NOT heard'} in video.")
        else:
            reasons.append("No code word was issued for this check.")

    is_mock = not config.is_live()
    result = VerifyResult(
        listing_id=listing_id, verdict=verdict, reasons=reasons[:4],
        counted_items=counted or None, code_word_heard=code_ok,
        transcript=transcript, is_mock=is_mock,
        frames_analyzed=frames_used, trace=trace,
    )
    SHOWME.setdefault(listing_id, {})["result"] = result.model_dump()

    out = result.model_dump()
    out["expected_code_word"] = expected
    out["listing_quantity"] = listing_qty
    out["pipeline_notes"] = pipeline_notes
    out["disclaimer"] = ("Shows goods + seller were present at recording time. "
                         "Not proof of legal ownership.")
    return JSONResponse(out)



# ───────────────────────── grounded-assistant guardrails (no hallucination)

# Things the assistant must never help sell, whatever the wording.
REFUSAL_PATTERNS = [
    "gun", "rifle", "pistol", "bullet", "ammo", "ammunition", "bomb",
    "explosive", "grenade", "missile", "weapon", "machete weapon",
    "drug", "cocaine", "heroin", "meth", "cannabis for smoking", "chamba",
    "poison", "kill", "murder", "steal", "stolen",
]

# Chichewa/English greetings and help — the only small talk allowed.
GREETINGS = {"hello", "hi", "hey", "moni", "how are you", "who are you",
             "what can you do", "good morning", "good afternoon",
             "good evening", "muli bwanji", "zikomo", "help", "thanks",
             "thank you", "yeah", "ok", "okay"}


def _refusal_reason(q_lower: str) -> str | None:
    for pat in REFUSAL_PATTERNS:
        if pat in q_lower:
            return pat
    return None


def _extract_item_ids(text: str) -> tuple[list[str], str]:
    """Split Spark's trailing [ITEMS: l01, l02] off its reply.

    Returns (ids, clean_text). Ids are validated against the catalogue by the
    caller; anything unknown is dropped, never shown.
    """
    m = re.search(r"\[ITEMS\s*:(.*?)\]", text or "", re.IGNORECASE | re.DOTALL)
    if not m:
        return [], (text or "").strip()
    ids = re.findall(r"\b([a-z]\d{2,3})\b", m.group(1).lower())
    clean = (text[:m.start()] + text[m.end():]).strip()
    return ids, clean


def _catalogue_brief(ranked: list[dict], limit: int = 5) -> str:
    return "\n".join(
        f"- {r['id']}: {r['item']} | MWK {r['price_mwk']:,.0f} | {r['seller']['name']}, "
        f"{r['seller']['distance_km']:.0f}km | trust {r['seller']['trust_score']} | "
        f"forex {r['forex'].score}/100 | {r['verification_status']} | "
        f"qty {r.get('quantity_available', 0)} {r.get('unit', '')}"
        for r in ranked[:limit])


ASSISTANT_SYSTEM = (
    "You are FindYourTrade's trade assistant for Malawi. Rules you never break: "
    "1) ONLY recommend listings from the REAL SEARCH RESULTS below — never invent a product, "
    "price, seller or listing id. If the results are empty, say plainly that nobody has posted "
    "that product and suggest the closest real alternative. "
    "2) Only answer questions about buying and selling legal goods (farm inputs, food, vehicles, "
    "parts, building materials, electronics). For anything else — weapons, drugs, violence, or "
    "off-topic chat — refuse briefly and steer back to the market. "
    "3) Never quote demo or example data as if it were a live listing. "
    "4) Mention verification status and the forex footprint when they matter. "
    "5) Keep replies to 3 short sentences for voice.")

# ─────────────────────────────────────────────────── conversational assistant

@app.post("/api/chat", response_model=ConversationalResponse)
async def conversational_chat(req: ChatRequest):
    """One direct line to Muse Spark: the model sees the whole conversation
    plus the live trader posts and just talks — greetings, questions, insights.
    Python only fetches the posts (the market shelf) and parses the model's
    reply for listing cards. No greeting lists, no refusal patterns, no
    separate parse/fraud/detail branches: Spark does all of that itself."""
    if not req.messages:
        return ConversationalResponse(
            answer="Moni! How can I help you today? Ask for any goods or trade needs in Malawi.",
            spoken="Moni! How can I help you today? Ask for any goods or trade needs in Malawi.",
        )
    trace: list[dict] = []

    # The live shelf: every non-fraud post, same pool the home dashboard shows.
    shelf = [(l, catalog.seller_by_id(l["seller_id"]))
             for l in catalog.listings() if not integrity.is_fraud(l)]
    shelf_lines = "\n".join(
        f"- {l['id']}: {l['item']} | MWK {float(l.get('price_mwk') or 0):,.0f} | "
        f"{(s or {}).get('name', '')} in {(s or {}).get('location', '')} | "
        f"qty {l.get('quantity_available', 0)} {l.get('unit', '')} | "
        f"status {l.get('verification_status', '')}"
        for l, s in shelf[:60])
    if not shelf_lines:
        shelf_lines = "(the market shelf is empty right now — no trader has posted yet)"

    history = [{"role": m.role, "content": m.content} for m in req.messages[-12:]]
    system = (
        "You are FindYourTrade's trade assistant for Malawi, talking directly "
        "to the buyer. Be natural: greet, joke lightly, explain, give insights — "
        "whatever the conversation calls for. When they want goods, recommend "
        "ONLY from the LIVE TRADER POSTS below — never invent a product, price, "
        "seller or listing id. If nothing matches, say plainly nobody has posted "
        "it. For weapons, drugs, violence or theft, refuse briefly and steer "
        "back to the market. Keep voice-length replies short.\n\n"
        f"LIVE TRADER POSTS (this is the whole market right now):\n{shelf_lines}\n\n"
        "End your reply with a line of the form [ITEMS: l01, l02] naming the "
        "ids you recommended, or [ITEMS:] when you recommend nothing.")

    answer = ""
    if config.is_live():
        try:
            res = await meta_client.chat(
                config.MODEL_SPARK,
                [{"role": "system", "content": system}, *history],
                # Spark reasons hard before answering; 260 truncates to empty.
                max_tokens=2000, temperature=0.4, op="assistant_direct",
                cache=False)
            trace += _trace(res)
            answer = (res.get("text") or "").strip()
            wants_retry = not answer
        except meta_client.MetaError:
            wants_retry = True
        if wants_retry and config.is_live():
            try:
                res = await meta_client.chat(
                    config.MODEL_SPARK,
                    [{"role": "system", "content": system}, *history],
                    max_tokens=8000, temperature=0.3, op="assistant_direct_retry",
                    cache=False)
                trace += _trace(res)
                answer = (res.get("text") or "").strip()
            except meta_client.MetaError:
                pass

    # Pull [ITEMS: ...] off the reply; the visible answer never shows it.
    picked, clean = _extract_item_ids(answer)
    show: list[dict] = []
    for pid in picked:
        l = catalog.listing_by_id(pid)
        if not l or integrity.is_fraud(l):
            continue
        s = catalog.seller_by_id(l["seller_id"]) or {}
        fx = score_footprint(l.get("origin", ""), l.get("stock_status", ""),
                             s.get("distance_km", 0), l.get("reusable", False))
        show.append({**l, "seller": s, "forex": fx,
                     "rank_explanation": "named by the assistant"})
    results_out, parsed = [], None
    if show:
        try:
            parsed = parse_request_text(req.messages[-1].content)
        except Exception:
            parsed = None
        results_out = [to_listing_out(r) for r in show]
    if not answer:
        clean = ("Moni! I couldn't reach the trade brain just now — give me a "
                 "few seconds and ask again.")
    spoken = clean.replace("MWK", "Kwacha").replace("km", "kilometers")
    return ConversationalResponse(
        answer=clean, spoken=spoken, results=results_out,
        parsed=parsed, listing_id=(show[0]["id"] if show else None),
        trace=trace, show_results=bool(show))


# ────────────────────────────────────────────────────────── F5: fraud check

@app.post("/api/fraud-check", response_model=ChatResponse)
async def fraud_check(req: ChatRequest):
    """Conversational fraud check over listing + verification + trade history."""
    ctx_lines: list[str] = []
    if req.listing_id and catalog.listing_by_id(req.listing_id):
        l = catalog.listing_by_id(req.listing_id)
        s = catalog.seller_by_id(l["seller_id"])
        v = SHOWME.get(req.listing_id, {}).get("result")
        ctx_lines.append(f"Listing {l['id']}: {l['item']} x{l['quantity_available']} @ MWK "
                         f"{l['price_mwk']:,}, origin={l['origin']}, stock={l['stock_status']}, "
                         f"verification={l['verification_status']}.")
        ctx_lines.append(f"Seller {s['name']} ({s['location']}, {s['distance_km']} km): "
                         f"trust {s['trust_score']}/100, account age {s['account_age_days']} days.")
        if isinstance(v, dict):
            ctx_lines.append(f"Show Me verdict: {v.get('verdict')} — {'; '.join(v.get('reasons', []))}.")

    question = req.messages[-1].content if req.messages else ""
    flags: list[str] = []
    reasoning: list[str] = []

    l = catalog.listing_by_id(req.listing_id or "") or {}
    s = (catalog.seller_by_id(l.get("seller_id", "")) or {}) if l else {}
    if l:
        if s.get("account_age_days", 999) < 30 and l.get("quantity_available", 0) > 200:
            flags.append("Brand-new account with a very large offer — classic advance-fee pattern.")
        v = SHOWME.get(l["id"], {}).get("result")
        if l.get("verification_status") == "unverified" or (
                isinstance(v, dict) and v.get("verdict") == "Mismatch"):
            flags.append("Verification failed or missing — insist on a Show Me video with today's code word.")
        if l.get("origin") == "imported" and l.get("stock_status") == "new_import":
            reasoning.append("New-import stock carries high forex dependence and longer lead times.")
        if isinstance(v, dict) and v.get("counted_items") is not None and v.get("counted_items") != l.get("quantity_available"):
            flags.append(f"Count mismatch: listing claims {l.get('quantity_available')}, "
                         f"video shows {v.get('counted_items')}.")
        reasoning.append(f"Trust {s.get('trust_score')}/100 from completed confirmed trades, "
                         f"verification history and account age ({s.get('account_age_days')} days).")

    trace: list[dict] = []
    answer = ""
    if config.is_live():
        try:
            res = await meta_client.fraud_assess(ctx_lines, question)
            trace += _trace(res)
            if res.get("data"):
                answer = res["data"]["answer"]
                flags = list(dict.fromkeys(flags + res["data"].get("risk_flags", [])))
                reasoning = reasoning + res["data"].get("reasoning", [])
        except meta_client.MetaError as exc:
            reasoning.append(f"Spark unavailable ({exc}); rule-based check used.")

    if not answer:
        if flags:
            answer = "I would be careful here. " + " ".join(flags)
        elif reasoning:
            answer = ("This listing looks reasonable: " + " ".join(reasoning)
                      + " Still, meet in person for high-value trades and confirm vehicle ownership "
                        "with the official authority.")
        else:
            answer = ("Ask me about a specific listing (for example 'Is l05 legit?') and I'll check it "
                      "against its verification record.")

    return ChatResponse(answer=answer, reasoning=reasoning or ctx_lines,
                        risk_flags=flags, trace=trace)


# ──────────────────────────────────────────────────────── F6: compare vehicles

@app.post("/api/vehicles/compare", response_model=VehicleCompareResponse)
async def vehicle_compare(body: dict):
    ids: list[str] = body.get("listing_ids", [])[:3]
    items = []
    for i in ids:
        l = catalog.listing_by_id(i)
        seller = catalog.seller_by_id(l["seller_id"]) if l else None
        if l and seller:
            items.append(to_listing_out({**l, "seller": seller}))
    if len(items) < 2:
        raise HTTPException(400, "provide at least two vehicle listing ids")

    bullets = [
        f"{it.item}: MWK {it.price_mwk:,.0f}, {it.registration_status or 'registration unknown'}, "
        f"odo {it.odometer_km or '?'} km, forex {it.forex.score}/100 ({it.forex.reason})"
        for it in items
    ]

    trace: list[dict] = []
    comp = ""
    if config.is_live():
        try:
            res = await meta_client.compare_vehicles(bullets)
            trace += _trace(res)
            comp = res.get("text", "")
        except meta_client.MetaError:
            comp = ""

    if not comp:
        best = min(items, key=lambda x: (x.forex.score, x.price_mwk))
        comp = ("Quick compare: " + " | ".join(bullets)
                + f" Lowest-forex pick: {best.item} ({best.forex.score}/100). Registered in-country "
                  f"stock avoids a fresh import.")

    return VehicleCompareResponse(items=items, comparison=comp, trace=trace)


# ─────────────────────────────────────────────────────────── F7: debt netting

@app.get("/api/netting/demo")
def netting_demo():
    """Net the debt ledger stored in Postgres (not a hardcoded ring)."""
    ledger = catalog.debts()
    res = propose_netting(ledger)
    return {"debts": ledger, **res,
            "mermaid": "graph LR\n" + "\n".join(
                f"  {d['debtor']}-->|{d['amount']:g}|{d['creditor']}" for d in ledger)}


@app.get("/api/netting/debts")
def list_debts():
    """Every recorded obligation in the ledger."""
    ledger = catalog.debts()
    return {"count": len(ledger), "total": round(sum(d["amount"] for d in ledger), 2),
            "debts": ledger, "source": catalog.stats()["source"]}


@app.post("/api/netting/debts")
def add_debt(body: dict):
    """Record one obligation: {"debtor": "...", "creditor": "...", "amount": 100}."""
    debtor = (body.get("debtor") or "").strip()
    creditor = (body.get("creditor") or "").strip()
    try:
        amount = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "amount must be a number")
    if not debtor or not creditor:
        raise HTTPException(400, "debtor and creditor are required")
    if debtor == creditor:
        raise HTTPException(400, "debtor and creditor must differ")
    if amount <= 0:
        raise HTTPException(400, "amount must be greater than zero")

    result = db.record_debt(debtor, creditor, amount)
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "could not record debt"))
    catalog.refresh(force=True)
    return {**result, "catalog": catalog.stats()}


@app.post("/api/netting/propose", response_model=NettingResponse)
async def netting_propose(req: NettingRequest):
    debts = [d.model_dump() for d in req.debts]
    res = propose_netting(debts)
    expl = res["explanation"]
    trace: list[dict] = []

    if config.is_live():
        try:
            r = await meta_client.explain_netting(debts, expl)
            trace += _trace(r)
            if r.get("text"):
                expl = r["text"]
        except meta_client.MetaError:
            pass

    return NettingResponse(
        original_cash_movement=res["original_cash_movement"],
        netted_cash_movement=res["netted_cash_movement"],
        cancellations=res["cancellations"], explanation=expl, trace=trace,
        mermaid="graph LR\n" + "\n".join(f"  {d.debtor}-->|{d.amount:g}|{d.creditor}" for d in req.debts),
    )


# ──────────────────────────────────────────────────────────────── P2: dashboard

@app.get("/api/dashboard")
def dashboard():
    """Dashboard counts cover clean posts only; quarantined posts are reported
    separately so the numbers never mix repeated or misbranded records in."""
    clean = [l for l in catalog.listings() if not integrity.is_fraud(l)]
    quarantined = [l for l in catalog.listings() if integrity.is_fraud(l)]
    by_verdict: dict[str, int] = {}
    for l in quarantined:
        by_verdict[l.get("fraud_verdict", "unknown")] = by_verdict.get(l.get("fraud_verdict", "unknown"), 0) + 1
    verified = sum(1 for l in clean if l["verification_status"] == "verified")
    local = sum(1 for l in clean if l["origin"] == "local")
    return {"verified_sellers": len({l['seller_id'] for l in clean if l['verification_status'] == 'verified'}),
            "verified_listings": verified, "local_listings": local,
            "imports_avoided_estimate": local * 3,
            "total_listings": len(clean),
            "total_sellers": len(catalog.sellers()),
            "quarantined": len(quarantined),
            "quarantined_by_verdict": by_verdict,
            "source": catalog.stats()["source"]}


# ──────────────────────────────────────────────────────────────── recommender

def _reco_item(entry: dict, legitimacy_score: float, social: dict | None = None) -> RecommendationItem:
    l, s, fx = entry["listing"], entry["seller"], entry["forex"]
    merged = {**l, "seller": s, "forex": fx, "rank_explanation": "; ".join(entry["reasons"])}
    soc = None
    if social is not None:
        soc = SocialOut(views=social.get("views", 0), likes=social.get("likes", 0),
                        liked=social.get("liked", False),
                        comments=[CommentOut(**c) for c in social.get("comments", [])])
    return RecommendationItem(listing=to_listing_out(merged), reco_score=entry["score"],
                              why=entry["reasons"], legitimacy_score=legitimacy_score,
                              social=soc)


def _legit_score_for(listing_id: str) -> float:
    entry = (SHOWME.get(listing_id, {}) or {}).get("result")
    verdict = entry.get("verdict") if isinstance(entry, dict) else None
    _, score, _ = recommender.legitimacy(catalog.listing_by_id(listing_id), verdict)
    return score


@app.get("/api/recommendations", response_model=RecommendationResponse)
async def get_recommendations(user_id: str = "guest", limit: int = 8,
                              include_pending: bool = False, exclude_seen: bool = False):
    """Personalized discovery feed — legitimate (verified / Show Me matched) products only."""
    entries, fallback = recommender.recommend(user_id, SHOWME, limit=limit,
                                              include_pending=include_pending,
                                              exclude_seen=exclude_seen)
    prof = recommender.get_profile(user_id)
    social = db.fetch_social([e["listing"]["id"] for e in entries], viewer_id=user_id)
    items = [_reco_item(e, _legit_score_for(e["listing"]["id"]),
                        social.get(e["listing"]["id"])) for e in entries]
    top = [c for c, _ in prof["interests"].most_common(3)]
    note = ("Popular legitimate listings (tell me your interests for a personal feed)."
            if fallback else
            f"Personalized from your interests: {', '.join(top) or 'general browsing'}. Legitimate products only.")
    return RecommendationResponse(user_id=user_id, interests=top, recommendations=items,
                                  used_fallback=fallback, note=note)


@app.post("/api/profile")
def save_profile(body: dict):
    p = recommender.set_profile(body.get("user_id", "guest"), body.get("interests"), body.get("location"))
    return {"user_id": body.get("user_id", "guest"), "interests": dict(p["interests"]),
            "location": p["location"], "liked": sorted(p["liked"])}


@app.get("/api/profile/{user_id}")
def read_profile(user_id: str):
    p = recommender.get_profile(user_id)
    return {"user_id": user_id, "interests": dict(p["interests"]), "location": p["location"],
            "liked": sorted(p["liked"]), "viewed": dict(p["viewed"]), "skipped": sorted(p["skipped"])}


@app.post("/api/events")
def log_event(body: dict):
    if not catalog.listing_by_id(body.get("listing_id") or ""):
        raise HTTPException(404, "unknown listing")
    if body.get("event") not in ("view", "like", "verify", "inquiry", "skip"):
        raise HTTPException(400, "unknown event")
    if body.get("event") == "view":
        SHOWME.setdefault(body["listing_id"], {})
        db.record_view(body["listing_id"], body.get("user_id", "guest"))
    return recommender.record_event(body.get("user_id", "guest"), body["listing_id"], body["event"])


@app.post("/api/listings/{listing_id}/view")
def listing_view(listing_id: str, body: dict):
    """Count a view. Called when a post scrolls into sight on home."""
    if not catalog.listing_by_id(listing_id):
        raise HTTPException(404, "unknown listing")
    return db.record_view(listing_id, (body or {}).get("user_id", "guest"))


@app.post("/api/listings/{listing_id}/like")
def listing_like(listing_id: str, req: Request):
    """Like/unlike toggle. Browsing guests like as their guest id; the
    identity chip (not a form) decides who they are — no login required."""
    if not catalog.listing_by_id(listing_id):
        raise HTTPException(404, "unknown listing")
    acct = db.account_from_token(_bearer_token(req))
    user_id = acct["seller_id"] if acct else "guest-anon"
    return db.toggle_like(listing_id, user_id)


@app.get("/api/listings/{listing_id}/social")
def listing_social(listing_id: str, user_id: str = ""):
    """Views, likes and the comment tree for one post."""
    if not catalog.listing_by_id(listing_id):
        raise HTTPException(404, "unknown listing")
    return db.fetch_social([listing_id], viewer_id=user_id).get(listing_id)


@app.post("/api/listings/{listing_id}/comments")
async def listing_comment(listing_id: str, req: Request):
    """Post a comment or a reply (parent_id set). Traders only — comments
    carry the trader's name, so anonymous noise stays out."""
    if not catalog.listing_by_id(listing_id):
        raise HTTPException(404, "unknown listing")
    acct = _require_trader(req)
    body = await req.json()
    return db.add_comment(listing_id, acct["seller_id"], acct.get("name", ""),
                          (body or {}).get("body", ""),
                          (body or {}).get("parent_id"))


@app.get("/api/recommendations/similar/{listing_id}")
def similar(listing_id: str, limit: int = 6):
    if not catalog.listing_by_id(listing_id):
        raise HTTPException(404, "unknown listing")
    out = [_reco_item(e, _legit_score_for(e["listing"]["id"]))
           for e in recommender.similar_legit(listing_id, SHOWME, limit=limit)]
    return {"listing_id": listing_id, "similar": out}
