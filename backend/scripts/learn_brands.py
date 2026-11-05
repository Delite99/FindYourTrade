"""Learn brand -> model facts from the internet (Wikipedia) into Postgres.

Grounds the fraud scan in public knowledge: "Hilux is a Toyota" is not an
opinion, it is what the world's encyclopedia says. A listing that contradicts
the learned facts ("Honda Hilux") is flagged even when live vision has never
seen its photo.

Usage:
    python scripts/learn_brands.py            # learn from known model names
    python scripts/learn_brands.py --refresh  # re-fetch everything

Each fact row: (brand, model, source, confidence, evidence). Facts confirmed
by *two* independent sources (Wikipedia extract + Wikidata claim) get
confidence 1.0; a single source gets 0.8. Human corrections via the Integrity
tab outrank everything (confidence 1.0, source 'human').
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import db  # noqa: E402

UA = {"User-Agent": "FindYourTrade-brandlearn/1.0 (demo; contact: dev@findyourtrade.local)"}
SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
WIKIDATA_URL = "https://www.wikidata.org/w/api.php"

# Model names the platform knows about (dataset models + common confusions).
# Key = Wikipedia article; the script learns which brand each model belongs to.
MODEL_ARTICLES = [
    "Toyota_Hilux", "Nissan_Navara", "Toyota_Hiace", "Tesla_Model_Y",
    "Tesla_Model_3", "Toyota_Corolla", "Toyota_Land_Cruiser", "Toyota_RAV4",
    "Honda_Fit", "Honda_Civic", "Honda_Ridgeline", "Honda_CR-V",
    "Mazda_Bongo", "Mazda_CX-5", "Mitsubishi_Pajero", "Mitsubishi_Triton",
    "Ford_Ranger", "Ford_Everest", "Nissan_Note", "Nissan_X-Trail",
    "Volkswagen_Polo", "Mercedes-Benz_C-Class", "BMW_3_Series",
    "Isuzu_D-Max", "Isuzu_Forward", "Scania_P-series",
]

KNOWN_BRANDS = ["Toyota", "Nissan", "Honda", "Mazda", "Mitsubishi", "Ford",
                "Tesla", "Volkswagen", "Mercedes-Benz", "Mercedes", "BMW",
                "Isuzu", "Scania", "Kia", "Hyundai", "Suzuki", "Subaru",
                "Land Rover", "Audi", "Volvo"]


def brand_from_text(text: str, subject: str = "") -> str:
    """Manufacturer of the article's subject.

    Only the FIRST sentence describes the subject ("The Isuzu D-Max is ...
    manufactured ... by Isuzu."); later sentences describe platforms and
    partners that must not win. Within that sentence, the "manufactured by X"
    phrase outranks any passing brand mention.
    """
    first = (text or "").split(".")[0]
    scope = f"{subject} {first}" if subject else first
    m = re.search(
        r"(?:produced|manufactured|marketed|made|built|developed)\s+(?:and\s+\w+\s+)?by\s+"
        r"(?:the\s+[\w\-]+\s+automobile\s+manufacturer\s+)?([\w\-]+(?:\s+[\w\-]+)?)",
        scope, re.I)
    if m:
        cand = brand_from_text(m.group(1))
        if cand:
            return cand
    for b in KNOWN_BRANDS:
        if re.search(rf"\b{re.escape(b)}\b", scope, re.I):
            return "Mercedes-Benz" if b == "Mercedes" else b
    return ""


def learn_wikipedia(client: httpx.Client, article: str) -> dict | None:
    try:
        r = client.get(SUMMARY_URL.format(article))
        if r.status_code != 200:
            return None
        d = r.json()
    except Exception:
        return None
    extract = d.get("extract") or ""
    brand = brand_from_text(extract, subject=article.split("/")[-1].replace("_", " "))
    if not brand:
        return None
    # "The Toyota Hilux ... is a series of pickup trucks produced ... by Toyota"
    title = article.split("/")[-1].replace("_", " ")
    model = re.sub(r"^(?:Toyota|Nissan|Honda|Mazda|Mitsubishi|Ford|Tesla|"
                   r"Volkswagen|Mercedes-Benz|BMW|Isuzu|Scania|Kia|Hyundai|"
                   r"Suzuki|Subaru|Audi|Volvo|Land Rover)\s+", "", title, flags=re.I)
    model_core = model.split("(")[0].strip()
    return {"brand": brand, "model": model_core,
            "evidence": extract[:300].replace("\n", " "),
            "url": (d.get("content_urls") or {}).get("desktop", {}).get("page", "")}


def wikidata_id(client: httpx.Client, article: str) -> str:
    try:
        r = client.get(SUMMARY_URL.format(article))
        if r.status_code != 200:
            return ""
        return (r.json().get("wikibase_item") or "")
    except Exception:
        return ""


def learn_wikidata(client: httpx.Client, qid: str) -> str:
    """Manufacturer (P176) of the Wikidata item, resolved to an English label."""
    if not qid:
        return ""
    try:
        r = client.get(WIKIDATA_URL, params={
            "action": "wbgetclaims", "entity": qid, "property": "P176",
            "format": "json"})
        claims = (r.json().get("claims") or {}).get("P176") or []
        if not claims:
            return ""
        mid = claims[0]["mainsnak"]["datavalue"]["value"]["id"]
        r2 = client.get(WIKIDATA_URL, params={
            "action": "wbgetentities", "ids": mid, "props": "labels",
            "languages": "en", "format": "json"})
        label = (((r2.json().get("entities") or {}).get(mid) or {})
                 .get("labels") or {}).get("en", {}).get("value", "")
        return brand_from_text(label) or label
    except Exception:
        return ""


def main() -> None:
    refresh = "--refresh" in sys.argv
    schema = db.ensure_schema()
    if not schema.get("ok"):
        print("schema error:", schema.get("error"))
        sys.exit(1)

    learned: dict[str, dict] = {}
    with httpx.Client(headers=UA, timeout=30, follow_redirects=True) as client:
        for article in MODEL_ARTICLES:
            time.sleep(1.0)
            wiki = learn_wikipedia(client, article)
            if not wiki:
                print(f"  ! no brand in {article}")
                continue
            second = ""
            try:
                qid = wikidata_id(client, article)
                second = learn_wikidata(client, qid) if qid else ""
                time.sleep(0.5)
            except Exception:
                pass
            agreed = bool(second) and brand_from_text(second) == wiki["brand"]
            # On disagreement, trust the article's "manufactured by X" phrase over
            # the Wikidata holding-company value (e.g. Isuzu D-Max: Wikipedia says
            # Isuzu, Wikidata P176 says General Motors from a JV era).
            fact = {
                "brand": wiki["brand"], "model": wiki["model"],
                "source": "wikipedia+wikidata" if agreed else "wikipedia",
                "confidence": 1.0 if agreed else 0.8,
                "evidence": wiki["evidence"]
                + (f" | Wikidata manufacturer: {second}" if second else ""),
                "url": wiki["url"],
            }
            learned.setdefault(wiki["model"].lower(), fact)
            mark = "✓✓" if agreed else "✓ "
            print(f"  {mark} {wiki['model']} → {wiki['brand']}"
                  + (f" (Wikidata: {second})" if second else ""))

    res = db.upsert_brand_facts(list(learned.values()), refresh=refresh)
    print("brand facts:", res)


if __name__ == "__main__":
    main()
