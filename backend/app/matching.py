"""F1 request parsing + F2 matching/ranking (PRD sections 6–7)."""
from __future__ import annotations
import re
from .models import ParsedRequest
from . import catalog, integrity
from .forex import score_footprint

# ── Ranking weights (PRD §7) ─────────────────────────────────────────────────
# Named constants instead of inline magic numbers so the scoring can be tuned
# in one place and reasoned about in review.
CATEGORY_BONUS = 60
TRUST_WEIGHT = 0.5
MAX_DISTANCE_PENALTY = 30
DISTANCE_WEIGHT = 0.2
FOREX_WEIGHT = 0.3
VERIFICATION_BONUS = {"verified": 15, "pending": 5, "unverified": 0}
IN_STOCK_BONUS = 10
OUT_OF_STOCK_PENALTY = -20
BUDGET_OVERAGE_WEIGHT = 40
LOW_FOREX_THRESHOLD = 30
HIGH_FOREX_THRESHOLD = 70
DEFAULT_RESULT_LIMIT = 8

# Words that look like a product category but are NOT things the market sells.
# Matched as WHOLE WORDS (\b) so "plates" never fires inside "templates" and "ship"
# never fires inside "relationship". Deliberately EXCLUDED from this list:
# bag (the market's standard unit), meal/food (maize meal + food vertical are
# real products), land (would collide with "Land Cruiser").
UNKNOWN_NOUNS = [
    "number plates", "number plate", "plates", "plate",
    "phones", "phone", "laptops", "laptop", "computer", "computers",
    "television", "tv", "radio", "radios", "fridge", "refrigerator",
    "sofa", "sofas", "bed", "beds", "chair", "chairs", "table", "tables",
    "clothes", "shoes", "shoe", "dress", "dresses", "suit", "suits",
    "handbag", "handbags",
    "helicopter", "helicopters", "aeroplane", "airplane", "boat", "boats",
    "ship", "ships",
    "haircut", "salon",
    # NOTE: cash/money/loan/job/house/plot are deliberately NOT here — they are
    # payment or life needs, never the head product noun. The parser extracts
    # payment separately; "cash for fertilizer" must reach fertilizer.
]

ITEM_KEYWORDS = {
    "fertilizer": ["fertilizer", "npk", "urea", "dap", "can ", " fertilizer"],
    "vehicle": ["vehicle", "car", "hilux", "navara", "van", "motorcycle", "bike", "bongo", "pickup", "truck"],
    "seed": ["seed", "seeds"],
    "maize": ["maize", "grain"],
    "parts": ["part", "alternator", "clutch", "brake", "wheel", "spare"],
    "equipment": ["pump", "irrigation", "solar", "kit", "treadle"],
    "agrochem": ["pesticide", "herbicide", "chemical"],
}

LOCATION_KEYWORDS = ["lilongwe", "blantyre", "mzuzu", "dedza", "zomba", "kasungu", "mangochi"]


def _word_hits(text: str, phrases: list[str]) -> list[tuple[int, str]]:
    """Whole-word hits as (position, phrase), longest-phrase-first at each spot."""
    import re as _re
    hits: list[tuple[int, str]] = []
    for phrase in sorted(phrases, key=len, reverse=True):
        for m in _re.finditer(rf"\b{_re.escape(phrase)}\b", text):
            hits.append((m.start(), phrase))
    return sorted(hits)


def classify_query(text: str) -> tuple[str, str]:
    """Category + ambiguous noun for a buyer request.

    The LAST noun wins: in English the head noun comes last, so "car plates"
    is about plates (unknown → clarify) while "cash for fertilizer" is about
    fertilizer. An adjective alone ("car" in "car plates") must never hijack
    the query into its category — that was the Navara-for-plates hallucination.
    Returns (category, unknown_noun). category "general" + unknown noun set
    means: ask the buyer which kind, do not rank anything.
    """
    t = (text or "").lower()
    # Possessive + noun = already owned, never the thing to buy:
    # "number plates for MY CAR" is about plates, not cars.
    t = re.sub(r"\b(my|your|his|her|our|their)\s+\w+", " ", t)
    known: list[tuple[int, str]] = []
    for cat, kws in ITEM_KEYWORDS.items():
        for pos, kw in _word_hits(t, kws):
            known.append((pos, cat))
    unknown = _word_hits(t, UNKNOWN_NOUNS)
    if not known and not unknown:
        return "general", ""
    if unknown and (not known or unknown[-1][0] > known[-1][0]):
        # Longest phrase wins: "number plates" contains "plates" at a later
        # offset, but the buyer said the whole thing.
        noun = max((u for _, u in unknown), key=len)
        return "general", noun
    return known[-1][1], ""


def parse_request_text(text: str) -> ParsedRequest:
    t = (text or "").lower()
    category, _ = classify_query(text)
    item_type = category
    m = re.search(r"(\d+)\s*(bag|kg|ton|unit|piece|kit|vehicle|car)s?", t)
    quantity = int(m.group(1)) if m else 1
    m2 = re.search(r"(\d[\d,]*)\s*(mwk|kwacha|k)", t)
    budget = float(m2.group(1).replace(",", "")) if m2 else None
    location = "Lilongwe"
    for loc in LOCATION_KEYWORDS:
        if loc in t:
            location = loc.capitalize()
            break
    pay = "any"
    for p in ["mobile money", "cash", "kwacha", "barter", "goods", "credit"]:
        if p in t:
            pay = p
            break
    return ParsedRequest(
        item_type=item_type, quantity=quantity, budget_mwk=budget,
        location=location, payment_preference=pay, raw_text=text or "", category=category,
    )
def rank_listings(parsed: ParsedRequest, limit: int = DEFAULT_RESULT_LIMIT) -> list[dict]:
    """Rank seeded listings: category match, trust, distance, price, forex."""
    scored: list[tuple[float, dict]] = []
    for l in catalog.listings():
        if integrity.is_fraud(l):
            continue  # a flagged post is never a result
        seller = catalog.seller_by_id(l["seller_id"])
        fx = score_footprint(l["origin"], l["stock_status"], seller["distance_km"], l["reusable"])
        cat_bonus = CATEGORY_BONUS if (parsed.category == "general"
                                       or l["category"] == parsed.category) else 0
        trust = seller["trust_score"] * TRUST_WEIGHT
        dist_pen = min(MAX_DISTANCE_PENALTY, seller["distance_km"] * DISTANCE_WEIGHT)
        fx_pen = fx.score * FOREX_WEIGHT
        ver_bonus = VERIFICATION_BONUS.get(l["verification_status"], 0)
        stock_bonus = (IN_STOCK_BONUS if l["quantity_available"] >= max(1, parsed.quantity)
                       else OUT_OF_STOCK_PENALTY)
        price_pen = 0.0
        if parsed.budget_mwk and l["price_mwk"]:
            price_pen = max(0, (l["price_mwk"] - parsed.budget_mwk)
                            / max(1, parsed.budget_mwk) * BUDGET_OVERAGE_WEIGHT)
        total = cat_bonus + trust + ver_bonus + stock_bonus - dist_pen - fx_pen - price_pen
        parts = []
        if l["verification_status"] == "verified":
            parts.append("verified seller, in stock")
        if fx.score <= LOW_FOREX_THRESHOLD:
            parts.append("low forex need")
        elif fx.score >= HIGH_FOREX_THRESHOLD:
            parts.append("high forex dependence")
        parts.append(f"{seller['distance_km']:.0f} km away")
        scored.append((total, {**l, "seller": seller, "forex": fx, "rank_explanation": "; ".join(parts)}))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:limit]]
