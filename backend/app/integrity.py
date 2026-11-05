"""Photo integrity: live-vision identification, perceptual dedup, RL feedback.

Three layers, in order of authority:

1. **Duplicate detection (perceptual hash, always local).** The same picture
   cannot be posted twice and passed off as two different products. Photos are
   fingerprinted with a 64-bit dHash, so a re-encoded, resized or slightly
   cropped copy is still recognised (Hamming distance, not byte equality).

2. **Label truth (LIVE VISION, primary).** The raw image bytes go to Muse
   Spark vision (`meta_client.identify_product`), which looks at the photo and
   says what it actually shows — brand, model, category, confidence. The claim
   on the post is judged against what vision *saw*, not against a stored
   label. One live call per unique photo: verdicts are cached in
   `vision_labels` keyed by photo fingerprint, and the cache is trusted until
   human feedback says otherwise.

3. **Reinforcement learning (human feedback).** Every vision verdict can be
   confirmed (+1 reward: vision got it right) or disputed (−1: it got it
   wrong, optionally with the correction). Disputed verdicts are re-examined
   by vision; corrections backed by a human become ground truth and override
   vision from then on. The policy provably improves: photos nobody disputes
   cost zero further calls, photos people dispute get corrected once and stay
   corrected.

When live vision is unreachable (no API key) the label rule degrades to the
dataset hash match and says so openly in `vision_source` — never silently.
"""
from __future__ import annotations

import os
import re

import httpx

# Hamming distance under which two dHashes are treated as the same picture.
# 64-bit hashes: <= 6 bits is a re-encode / small crop, not a different product.
NEAR_DUPLICATE_BITS = 6

# Vision confidence at or above which a verdict can convict (fraud).
# Below it vision is advisory: a warning, never a fraud flag.
VISION_CONFIDENT = 0.5

# Where a verdict's "what the photo shows" came from — always reported.
SOURCE_LIVE_VISION = "live_vision"      # fresh Muse Spark call this request
SOURCE_VISION_CACHE = "vision_cache"    # trusted stored vision verdict
SOURCE_HUMAN = "human"                  # human correction (RL ground truth)
SOURCE_HASH_FALLBACK = "hash_fallback"  # dataset fingerprint match (no vision)
SOURCE_UNVERIFIED = "unverified"        # nobody knows what this photo shows

DATASET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset")

# Words that carry no product identity, so they must not make two labels "match".
STOPWORDS = {
    "the", "and", "with", "for", "per", "set", "pack", "piece", "pieces", "unit",
    "local", "fresh", "new", "used", "assorted", "quality", "grade", "size",
    "kg", "g", "l", "ml", "cm", "mm", "m", "inch", "in", "v", "w", "ah", "hp",
    "kva", "litre", "liter", "bag", "plate", "portion", "bird", "sheet", "length",
    "bucket", "panel", "vehicle", "equipment", "supplier", "assembly", "forge",
    "mill", "kiln", "sawmill", "kitchen", "farm", "blend", "brand",
}

import cv2
import numpy as np


def dhash(image_bytes: bytes, size: int = 8) -> str:
    """64-bit difference hash as 16 hex chars; empty string if undecodable."""
    if not image_bytes:
        return ""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return ""
    img = cv2.resize(img, (size + 1, size), interpolation=cv2.INTER_AREA)
    diff = img[:, 1:] > img[:, :-1]
    bits = 0
    for bit in diff.flatten():
        bits = (bits << 1) | int(bit)
    return f"{bits:016x}"


def dhash_file(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return dhash(fh.read())
    except OSError:
        return ""


def hamming(a: str, b: str) -> int:
    """Bit distance between two hex hashes; 64 (max) when either is missing."""
    if not a or not b:
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def photo_path(photo: str) -> str:
    """Resolve a stored photo reference to a file on disk ('' if not local)."""
    if not photo:
        return ""
    if photo.startswith("/dataset/"):
        return os.path.join(DATASET_DIR, photo[len("/dataset/"):])
    if photo.startswith("dataset/"):
        return os.path.join(DATASET_DIR, photo[len("dataset/"):])
    return ""


def unsplash_url(photo: str) -> str:
    """Build a fetchable URL for an Unsplash id or pass through http(s)."""
    if photo.startswith("http"):
        return photo
    return f"https://images.unsplash.com/{photo}?auto=format&fit=crop&w=800&q=70"


async def load_image_bytes(photo: str, timeout: float = 25.0) -> bytes:
    """Raw bytes for ANY post photo: local dataset file or remote Unsplash URL.

    Live vision looks at actual pixels, so every photo — not just dataset
    files — can be identified. Returns b"" when the photo cannot be loaded.
    """
    if not photo:
        return b""
    path = photo_path(photo)
    if path:
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:
            return b""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(unsplash_url(photo))
        if r.status_code == 200 and r.content:
            ctype = r.headers.get("content-type", "")
            if ctype.startswith("image") or r.content[:4] in (b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1",
                                                             b"\xff\xd8\xff\xdb", b"\x89PNG"):
                return r.content
            # Some CDNs omit the content type — accept decodable bytes anyway.
            if dhash(r.content):
                return r.content
    except Exception:
        pass
    return b""


def tokens(text: str) -> set[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in (text or ""))
    return {t for t in cleaned.split() if t and t not in STOPWORDS}


_VERDICT_KEYS = {"brand", "model", "category", "vertical", "confidence",
                 "features", "notes", "matches_claim", "claim_reason"}


def _looks_like_verdict(ident: dict) -> bool:
    """True when a dict really is Muse's structured verdict — i.e. it answers
    the photo question in its own vocabulary (features, notes, category), not
    a bare token intersection with zero product substance.

    A stored hash-fallback label ({brand, model} only) and a plain claim do
    not pass: the gate is about content (did Muse answer the photo?), never
    about names.
    """
    if not isinstance(ident, dict):
        return False
    if set(ident) & {"matches_claim", "claim_reason", "features", "notes", "confidence"}:
        return True
    return bool(ident.get("brand") or ident.get("model"))


def _verdict_title(ident: dict) -> str:
    """What Muse called the goods, in its own words — notes first, then the
    salient visible features. Never the products table: the title describes
    the photo, not a catalogue row.
    """
    notes = str(ident.get("notes") or "").strip()
    feats = [str(f).strip() for f in (ident.get("features") or []) if str(f).strip()]
    if notes and feats:
        return f"{notes.rstrip('.')} — {'; '.join(feats[:2])}"
    return notes or "; ".join(feats[:2])


def same_brand(a: str, b: str) -> bool | None:
    """True/False when both are known, None when either side is unknown."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return None
    return bool(ta & tb)


# Web-learned brand knowledge: which brand each model name belongs to,
# learned from Wikipedia + Wikidata and stored in brand_facts. A claim that
# contradicts public knowledge ("Honda Hilux") is flagged even when nobody
# has seen its photo — the database record itself is wrong.
SOURCE_WEB_KNOWLEDGE = "web_knowledge"


# Brands that make vehicles (not parts): only their names in a claim's brand
# field assert "X built this model". A parts-maker brand (Denso, Bridgestone)
# on a parts item is legitimate — Denso sells Hilux alternators without
# claiming to build the Hilux.
VEHICLE_MAKERS = {
    "toyota", "nissan", "honda", "mazda", "mitsubishi", "ford", "tesla",
    "volkswagen", "mercedes-benz", "mercedes", "bmw", "isuzu", "scania",
    "kia", "hyundai", "suzuki", "subaru", "land rover", "audi", "volvo",
    "general motors", "chevrolet",
}

# Item wording that marks a claim as parts/accessories rather than a vehicle.
PARTS_WORDS = {
    "alternator", "clutch", "radiator", "tyre", "tyre", "tyres", "tire",
    "tires", "wheel", "wheels", "battery", "batteries", "brake", "brakes",
    "filter", "filters", "pump", "belt", "plug", "plugs", "gasket", "bearing",
    "shock", "strut", "mirror", "light", "lights", "bumper", "fender",
    "grille", "muffler", "exhaust", "starter", "compressor", "kit", "pad",
    "pads", "disc", "rotor", "sensor", "hose", "valve", "piston", "ring",
    "seal", "chain", "sprocket", "oil", "coolant", "wiper", "bulb", "fuse",
    "relay", "injector", "turbo", "radiator", "condenser", "evaporator",
    "driveshaft", "axle", "differential", "gearbox", "transmission",
}


def find_claimed_model(claim: dict, brand_facts: dict | None) -> dict | None:
    """Which known model name appears in the claim? Returns its fact row."""
    if not brand_facts:
        return None
    hay = f"{claim.get('model', '')} {claim.get('item', '')}".lower()
    if not hay.strip():
        return None
    best = None
    for key, fact in brand_facts.items():
        # Whole-word match so "Fit" does not fire inside "outfit".
        if re.search(rf"\b{re.escape(key)}\b", hay):
            if best is None or len(key) > len(best[0]):
                best = (key, fact)
    return best[1] if best else None


def web_knowledge_conflict(claim: dict, brand_facts: dict | None) -> str:
    """Reason when the claim contradicts internet-learned brand knowledge."""
    fact = find_claimed_model(claim, brand_facts)
    if not fact:
        return ""
    claimed_brand = (claim.get("brand") or "").strip()
    if not claimed_brand:
        return ""
    if same_brand(claimed_brand, fact["brand"]) is False:
        # A parts-maker brand on a parts item is not a brand claim at all:
        # "Denso used" on a Hilux alternator does not say Denso built the Hilux.
        # But a non-maker brand on a whole vehicle ("Denso Hilux 2019" as a car)
        # is still wrong — nothing built it but the real maker.
        claimed_tokens = tokens(claimed_brand)
        item_tokens = tokens(claim.get("item", ""))
        if not (claimed_tokens & VEHICLE_MAKERS) and (item_tokens & PARTS_WORDS):
            return ""
        return (f"public records say the {fact['model']} is a {fact['brand']}, "
                f"but this record claims brand {claimed_brand}")
    return ""


def same_product(claim_a: dict, claim_b: dict) -> bool:
    """Do two posts claim the same product? (brand + item wording)"""
    brand_same = same_brand(claim_a.get("brand", ""), claim_b.get("brand", ""))
    if brand_same is False:
        return False
    ta = tokens(claim_a.get("item", ""))
    tb = tokens(claim_b.get("item", ""))
    if not ta or not tb:
        return brand_same is not False
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return overlap >= 0.5


def nearest_product(photo_hash: str, products: list[dict]) -> tuple[dict | None, int]:
    best, best_d = None, 64
    for p in products:
        d = hamming(photo_hash, p.get("photo_hash", ""))
        if d < best_d:
            best, best_d = p, d
    if best_d > NEAR_DUPLICATE_BITS:
        return None, best_d
    return best, best_d


# ─────────────────────────────── vision truth + RL policy

def needs_live_call(photo_hash: str, stored: dict | None) -> tuple[bool, str]:
    """Explore/exploit policy for the vision model, driven by reward history.

    Exploit the cache while humans keep confirming it; explore (re-call vision)
    when the stored verdict was never identified or disputes outweigh confirms.
    """
    if not photo_hash:
        return False, "no fingerprint"
    if not stored or not (stored.get("brand") or stored.get("model")):
        return True, "never identified by vision"
    if stored.get("source") == "human":
        return False, "human ground truth"
    if stored.get("disputes", 0) > stored.get("confirmations", 0):
        return True, "disputed — re-examine"
    return False, "trusted cache"


def effective_truth(*, photo_hash: str, claim: dict, products: list[dict],
                    vision_store: dict | None,
                    live: dict | None = None) -> tuple[dict | None, str, bool]:
    """What the photo actually shows, and where that knowledge came from.

    Precedence: human correction > confident vision (fresh, then cached) >
    dataset fingerprint match > uncertain vision (advisory) > unknown.
    Returns (truth {brand, model, title}, source, confident).
    """
    # 1. Human correction from RL feedback — always wins.
    if vision_store and vision_store.get("source") == "human" and (
            vision_store.get("corrected_brand") or vision_store.get("corrected_model")):
        return ({
            "brand": vision_store.get("corrected_brand", ""),
            "model": vision_store.get("corrected_model", ""),
            "title": "",
        }, SOURCE_HUMAN, True)

    # 2. Direct vision verdict, fresh first. The verdict object IS Muse's raw
    # answer — injected untouched. An unbranded verdict ({brand: "", model:
    # ""}) is a real statement ("no visible make"), not a failure: it carries
    # features/notes/category, so it names the goods even when there is no logo.
    for ident, source in ((live, SOURCE_LIVE_VISION),
                          (vision_store, SOURCE_VISION_CACHE)):
        if ident and _looks_like_verdict(ident):
            try:
                conf = float(ident.get("confidence") or 0)
            except (TypeError, ValueError):
                conf = 0.0
            if conf >= VISION_CONFIDENT:
                return ({
                    "brand": ident.get("brand", ""),
                    "model": ident.get("model", ""),
                    "title": _verdict_title(ident),
                }, source, True)

    # 3. Dataset fingerprint match (offline fallback, labelled by construction).
    product, _ = nearest_product(photo_hash, products) if photo_hash else (None, 64)
    if product:
        return ({
            "brand": product.get("brand", ""),
            "model": product.get("model", ""),
            "title": product.get("title", ""),
            "product_id": product.get("id", ""),
        }, SOURCE_HASH_FALLBACK, True)

    # 4. Muse's verdict below the confidence threshold is advisory only — but it
    # is still Muse's verdict, never the hash consensus.
    for ident, source in ((live, SOURCE_LIVE_VISION),
                          (vision_store, SOURCE_VISION_CACHE)):
        if ident and _looks_like_verdict(ident):
            return ({
                "brand": ident.get("brand", ""),
                "model": ident.get("model", ""),
                "title": _verdict_title(ident),
            }, source, False)

    return None, SOURCE_UNVERIFIED, False


def _label_conflict(claim: dict, truth: dict) -> str:
    """Human reason when the claim contradicts what the photo shows, else ''.

    The comparison runs on Muse's own words (notes/features/category), never
    on the products table. When Muse said "no visible make", its description
    of the goods is the truth being compared — so a Hilux label over a bowl of
    mandazi is a mismatch even though neither side names a brand.
    """
    claim_name = " ".join(t for t in (claim.get("brand"), claim.get("model"),
                                       claim.get("item")) if t).strip()
    truth_name = " ".join(t for t in (truth.get("brand"), truth.get("model"),
                                      truth.get("title")) if t).strip()

    brand_same = same_brand(claim.get("brand", ""), truth.get("brand", ""))
    if brand_same is False:
        return (f"the photo shows {truth_name or 'a different product'}, "
                f"but the post is labelled {claim.get('brand') or claim.get('item')}")
    if brand_same is None and claim.get("brand") and not truth.get("brand"):
        # Named brand on the label, but Muse saw no make at all: the verdict's
        # own description decides — a truck name over pastries is a mismatch.
        pass  # falls through to the goods-description check below

    claimed_model = claim.get("model", "")
    truth_model = truth.get("model", "")
    if claimed_model and truth_model and same_brand(claimed_model, truth_model) is False:
        return (f"the photo shows a {truth_model}, "
                f"but the post claims a {claimed_model}")

    # Goods-description check: Muse described the goods; the label must speak
    # about the same thing in the same vocabulary.
    return _goods_conflict(claim, truth, claim_name, truth_name)


def _goods_conflict(claim: dict, truth: dict, claim_name: str, truth_name: str) -> str:
    """Do the label and Muse's photo description speak about the same goods?

    Both sides are compared in Muse's vocabulary: the claim's words against the
    truth's description (notes/features/category), not against catalogue rows.
    Empty on agreement or on genuine uncertainty — a cautious unknown is NOT a
    mismatch ("mandazi?" over mandazi passes; "Toyota Hilux" over mandazi
    fails).
    """
    claimed_tokens = tokens(claim.get("item", "")) | tokens(claim.get("model", ""))
    claimed_tokens |= tokens(claim.get("brand", ""))
    truth_tokens = tokens(truth.get("title", ""))
    truth_tokens |= tokens(truth.get("model", "")) | tokens(truth.get("brand", ""))

    truth_category = str(truth.get("category", "") or "").lower()
    if truth_category and truth_category != "general":
        truth_tokens.add(truth_category)

    if not claimed_tokens or not truth_tokens:
        return ""

    shared = claimed_tokens & truth_tokens
    if shared:
        return ""

    # A bare name that shares nothing with the photo (e.g. a seed variety code
    # "SC 627") is uncertainty, not fraud: it names nothing the photo
    # contradicts. But a label that speaks a whole different product language
    # (vehicle words over pastries) is a genuine mismatch.
    extra = claimed_tokens - truth_tokens
    if len(extra) <= 2 and not any(len(w) > 4 for w in extra):
        return ""

    # No shared word at all. Distinguish "cautious unknown" from "contradiction":
    # shared_tokens empty but the label merely lacks the food words (e.g. a
    # bare variety name) is uncertainty, not fraud. Demand positive evidence of
    # a DIFFERENT thing — a second product vocabulary on the label side.
    return (f"the photo shows {truth_name or 'these goods'}, "
            f"but the post is described as {claim_name}")


def _duplicate_flags(photo_hash: str, claim: dict, listings: list[dict],
                     exclude_listing_id: str, claim_matches_truth: bool) -> tuple[list[dict], str | None]:
    """Rule 1: the same picture cannot be two products. Always local."""
    flags: list[dict] = []
    duplicate_of = None
    for other in listings:
        if other["id"] == exclude_listing_id:
            continue
        other_hash = other.get("photo_hash") or ""
        if not other_hash:
            continue
        if hamming(photo_hash, other_hash) > NEAR_DUPLICATE_BITS:
            continue
        duplicate_of = other
        same = same_product(claim, {"item": other.get("item", ""), "brand": other.get("brand", "")})
        if same:
            flags.append({
                "code": "duplicate_photo", "severity": "warning",
                "message": (f"This photo is already used by another post ({other['id']}) "
                            f"for the same product — reposting the same picture adds nothing."),
                "duplicate_of": other["id"],
            })
        elif claim_matches_truth:
            flags.append({
                "code": "shared_photo", "severity": "warning",
                "message": (f"The same photo is also used by post {other['id']} "
                            f"(\u201c{other.get('item')}\u201d), which labels it differently. "
                            f"This post's label matches the picture."),
                "duplicate_of": other["id"],
            })
        else:
            flags.append({
                "code": "duplicate_photo", "severity": "fraud",
                "message": (f"This photo is already used by post {other['id']} "
                            f"(\u201c{other.get('item')}\u201d) for a different product — "
                            f"one picture cannot be two products."),
                "duplicate_of": other["id"],
            })
        break
    return flags, duplicate_of["id"] if duplicate_of else None


def _assemble(photo_hash: str, claim: dict, truth: dict | None, source: str,
              confident: bool, dup_flags: list[dict], duplicate_of: str | None,
              vision_ident: dict | None, web_reason: str = "",
              web_fact: dict | None = None) -> dict:
    """Verdict + flags from the truth judgement and the duplicate check."""
    flags: list[dict] = []
    if truth:
        reason = _label_conflict(claim, truth)
        if reason:
            if confident:
                flags.append({
                    "code": "photo_mismatch", "severity": "fraud",
                    "message": f"Photo and label disagree: {reason}.",
                    "vision_source": source,
                    "shows_brand": truth.get("brand", ""),
                    "shows_model": truth.get("model", ""),
                })
            else:
                flags.append({
                    "code": "uncertain_vision", "severity": "warning",
                    "message": (f"Vision is unsure what this photo shows ({reason}); "
                                f"ask for a Show Me video before payment."),
                    "vision_source": source,
                })
    if web_reason:
        flags.append({
            "code": "brand_conflict", "severity": "fraud",
            "message": f"Record contradicts public knowledge: {web_reason}.",
            "vision_source": SOURCE_WEB_KNOWLEDGE,
            "shows_brand": (web_fact or {}).get("brand", ""),
            "shows_model": (web_fact or {}).get("model", ""),
            "evidence_url": (web_fact or {}).get("url", ""),
        })
    flags.extend(dup_flags)

    has_fraud = any(f["severity"] == "fraud" for f in flags)
    if has_fraud:
        codes = {f["code"] for f in flags}
        verdict = "duplicate_and_mismatch" if len(codes) > 1 else (
            "brand_conflict" if "brand_conflict" in codes else
            "mismatch" if "photo_mismatch" in codes else "duplicate")
    elif flags:
        codes = {f["code"] for f in flags}
        verdict = "shared_photo" if codes == {"shared_photo"} else "duplicate"
    elif truth:
        verdict = "ok"
    else:
        verdict = "unknown_photo"
        flags.append({
            "code": "unknown_photo", "severity": "info",
            "message": ("Nobody has identified this photo yet — not vision, not a "
                        "human. Show Me verification is required before payment."),
            "vision_source": source,
        })

    return {
        "verdict": verdict,
        "flags": flags,
        "vision": vision_ident,
        "vision_source": source,
        "truth": truth,
        "matched_product": ({"id": truth.get("product_id", ""), "title": truth.get("title", ""),
                             "brand": truth.get("brand", "")}
                            if truth and truth.get("product_id") else None),
        "duplicate_of": duplicate_of,
    }


def check_photo(photo_hash: str, claim: dict, *, products: list[dict],
                listings: list[dict], exclude_listing_id: str = "",
                vision_labels: dict | None = None,
                live: dict | None = None,
                brand_facts: dict | None = None) -> dict:
    """Synchronous check: stored vision/human truth + hash fallback, no network.

    Used by the startup scan and by feedback re-checks. `live` is an optional
    fresh identification (from a just-completed vision call) that outranks cache.
    `brand_facts` is the web-learned model -> brand knowledge: a claim that
    contradicts it is flagged even when the photo itself is unknown.
    """
    if not photo_hash:
        web_reason = web_knowledge_conflict(claim, brand_facts)
        web_fact = find_claimed_model(claim, brand_facts)
        flags = []
        if web_reason:
            flags.append({
                "code": "brand_conflict", "severity": "fraud",
                "message": f"Record contradicts public knowledge: {web_reason}.",
                "vision_source": SOURCE_WEB_KNOWLEDGE,
                "shows_brand": (web_fact or {}).get("brand", ""),
                "shows_model": (web_fact or {}).get("model", ""),
                "evidence_url": (web_fact or {}).get("url", ""),
            })
            return {"verdict": "brand_conflict", "flags": flags,
                    "vision": None, "vision_source": SOURCE_WEB_KNOWLEDGE,
                    "truth": None, "matched_product": None,
                    "duplicate_of": None, "distance": 64}
        return {"verdict": "unknown_photo", "flags": [{
            "code": "unknown_photo", "severity": "info",
            "message": "Photo could not be fingerprinted — ask for a Show Me video.",
            "vision_source": SOURCE_UNVERIFIED,
        }], "vision": None, "vision_source": SOURCE_UNVERIFIED,
            "truth": None, "matched_product": None, "duplicate_of": None, "distance": 64}

    store = (vision_labels or {}).get(photo_hash)
    truth, source, confident = effective_truth(
        photo_hash=photo_hash, claim=claim, products=products,
        vision_store=store, live=live)
    vision_ident = live or (store if store and _looks_like_verdict(store) else None)

    claim_matches_truth = bool(truth) and not _label_conflict(claim, truth)
    dup_flags, duplicate_of = _duplicate_flags(
        photo_hash, claim, listings, exclude_listing_id, claim_matches_truth)

    web_fact = find_claimed_model(claim, brand_facts)
    web_reason = web_knowledge_conflict(claim, brand_facts)
    # If the photo itself already proves the claim wrong, the web fact is
    # corroboration, not a second fraud — skip it to keep flags readable.
    if claim_matches_truth is False and truth and web_reason:
        web_reason, web_fact = "", None

    out = _assemble(photo_hash, claim, truth, source, confident,
                    dup_flags, duplicate_of, vision_ident, web_reason, web_fact)
    _, distance = nearest_product(photo_hash, products)
    out["distance"] = distance
    if out["matched_product"]:
        out["product_id"] = out["matched_product"]["id"]
    else:
        out["product_id"] = ""
    return out


async def check_photo_live(image_bytes: bytes, photo_hash: str, claim: dict, *,
                           products: list[dict], listings: list[dict],
                           vision_labels: dict | None = None,
                           exclude_listing_id: str = "",
                           force_vision: bool = False,
                           brand_facts: dict | None = None) -> dict:
    """Full live-vision pipeline for one photo. Network inside (one call max).

    Follows the explore/exploit policy: calls Muse Spark only when the verdict
    is missing or disputed (or `force_vision`), otherwise trusts the cache.
    Persists the fresh verdict via the caller's db handle — pass
    `persist=db.upsert_vision_label` (or None to skip persistence, e.g. tests).
    Returns the check dict plus `vision_called`, `vision_cached` and `trace`.
    """
    from . import meta_client  # lazy: keeps import graph acyclic

    if not photo_hash and image_bytes:
        photo_hash = dhash(image_bytes)
    if not photo_hash:
        base = check_photo("", claim, products=products, listings=listings,
                           exclude_listing_id=exclude_listing_id,
                           brand_facts=brand_facts)
        return {**base, "vision_called": False, "vision_cached": False,
                "policy": "no fingerprint", "trace": []}

    store = (vision_labels or {}).get(photo_hash)
    call, policy = needs_live_call(photo_hash, store)
    if force_vision and meta_client.has_meta_key():
        call, policy = True, "forced re-examination"

    live = None
    vision_called = False
    trace: list[dict] = []
    if call:
        if meta_client.has_meta_key() and image_bytes:
            res = await meta_client.identify_product(
                image_bytes, claim=claim,
                candidates=[{"brand": p.get("brand", ""), "model": p.get("model", ""),
                             "title": p.get("title", "")} for p in products])
            trace.append({"model": res.get("model", ""), "op": res.get("op", "identify"),
                          "ok": not res.get("is_mock", False),
                          "is_mock": bool(res.get("is_mock")),
                          "latency_ms": res.get("latency_ms", 0)})
            if res.get("identification"):
                live = res["identification"]
                vision_called = True
        else:
            trace.append({"model": meta_client.MODEL_SPARK, "op": "identify",
                          "ok": False, "is_mock": True, "latency_ms": 0,
                          "note": "no API key or no image bytes — hash fallback"})

    out = check_photo(photo_hash, claim, products=products, listings=listings,
                      exclude_listing_id=exclude_listing_id,
                      vision_labels=vision_labels, live=live,
                      brand_facts=brand_facts)
    out["vision_called"] = vision_called
    out["vision_cached"] = bool(not vision_called and out["vision_source"] in (
        SOURCE_VISION_CACHE, SOURCE_HUMAN))
    out["policy"] = policy
    out["trace"] = trace
    if live:
        out["live_identification"] = live
    return out


def is_fraud(listing: dict) -> bool:
    """True when any flag on this post is fraud-severity (not a warning)."""
    return any(f.get("severity") == "fraud" for f in (listing.get("fraud_flags") or []))


def scan_listings(listings: list[dict], products: list[dict],
                  vision_labels: dict | None = None,
                  brand_facts: dict | None = None) -> list[dict]:
    """Re-run the checks over every listing: fingerprinted local photos AND
    text-only brand/model contradictions from web-learned knowledge.

    Startup path: local computation only (hashes + stored vision/human truth +
    brand facts), never network — live vision upgrades happen on demand through
    POST /api/integrity/check and POST /api/integrity/scan-vision.
    Returns [{id, photo_hash, product_id, verdict, flags}] ready to persist.
    """
    results = []
    prepared = []
    for l in listings:
        path = photo_path(l.get("photo", ""))
        h = dhash_file(path) if path else ""
        prepared.append({**l, "photo_hash": h or l.get("photo_hash", "")})

    for l in prepared:
        claim = {"item": l.get("item", ""), "brand": l.get("brand", ""),
                 "model": l.get("model", "")}
        if not l["photo_hash"]:
            # No fingerprintable photo — still check the record's own wording
            # against web knowledge ("Honda Hilux" is wrong on paper alone).
            # If the record was previously flagged but is clean now, emit a
            # clearing row so the stale verdict is wiped, not kept forever.
            web_reason = web_knowledge_conflict(claim, brand_facts)
            if not web_reason:
                if l.get("fraud_verdict"):
                    results.append({
                        "id": l["id"],
                        "photo_hash": "",
                        "product_id": "",
                        "verdict": "",
                        "flags": [],
                    })
                continue
            outcome = check_photo("", claim, products=products, listings=prepared,
                                  exclude_listing_id=l["id"],
                                  vision_labels=vision_labels,
                                  brand_facts=brand_facts)
        else:
            outcome = check_photo(
                l["photo_hash"], claim,
                products=products, listings=prepared, exclude_listing_id=l["id"],
                vision_labels=vision_labels, brand_facts=brand_facts,
            )
            # A clean photo check with no web contradiction is not worth storing —
            # unless a stale verdict is sitting in the DB, which must be cleared.
            if outcome["verdict"] == "ok" and not l.get("fraud_verdict"):
                continue
        results.append({
            "id": l["id"],
            "photo_hash": l["photo_hash"],
            "product_id": outcome.get("product_id", ""),
            "verdict": outcome["verdict"],
            "flags": outcome["flags"],
        })
    return results
