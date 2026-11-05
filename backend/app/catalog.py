"""Read layer for traders and posts.

Postgres is the source of truth: `catalog` pulls sellers and listings out of the
database (via `db.fetch_*`) and keeps a short-lived in-process snapshot so hot
endpoints do not hit the DB on every request. `refresh()` reloads on demand.

If the database is unreachable the module falls back to the bootstrap fixture so
the demo still renders, but reports `source == "seed-fallback"` in `stats()` so
the degradation is visible rather than silent.
"""
from __future__ import annotations

import logging
import threading
import time

from . import db

logger = logging.getLogger("findyourtrade.catalog")

# How long a snapshot is trusted before the next read refreshes it.
CACHE_TTL_SECONDS = 15.0

_lock = threading.RLock()
_state: dict = {
    "sellers": [],
    "listings": [],
    "sellers_by_id": {},
    "listings_by_id": {},
    "by_seller": {},
    "products": [],
    "products_by_id": {},
    "debts": [],
    "loaded_at": 0.0,
    "source": "unloaded",
}


def _fallback() -> tuple[list[dict], list[dict], list[dict]]:
    """Bootstrap fixture, used only when Postgres cannot be read."""
    from .seed_data import SELLERS, LISTINGS, DEMO_DEBTS
    return list(SELLERS), list(LISTINGS), list(DEMO_DEBTS)


def _load() -> None:
    sellers = db.fetch_sellers()
    listings = db.fetch_listings()
    # Posts with no real photo/video are off the market (see
    # db.sync_media_retirement). They never reach the feed, search or chat.
    listings = [l for l in listings if not l.get("retired")]
    products = db.fetch_products()
    debts = db.fetch_debts()
    source = "postgres"
    if not sellers:
        logger.warning("Catalogue empty or DB unreachable — falling back to bootstrap fixture")
        sellers, listings, debts = _fallback()
        source = "seed-fallback"

    by_seller: dict[str, list[dict]] = {}
    for l in listings:
        by_seller.setdefault(l["seller_id"], []).append(l)

    with _lock:
        _state.update({
            "sellers": sellers,
            "listings": listings,
            "sellers_by_id": {s["id"]: s for s in sellers},
            "listings_by_id": {l["id"]: l for l in listings},
            "by_seller": by_seller,
            "products": products,
            "products_by_id": {p["id"]: p for p in products},
            "debts": debts,
            "loaded_at": time.time(),
            "source": source,
        })


def refresh(force: bool = False) -> dict:
    """Reload the snapshot if it is stale (or `force`d)."""
    with _lock:
        fresh = bool(_state["listings"]) and (time.time() - _state["loaded_at"] < CACHE_TTL_SECONDS)
    if force or not fresh:
        _load()
    return stats()


def _ensure() -> None:
    with _lock:
        loaded = bool(_state["listings"])
    if not loaded:
        _load()
    elif time.time() - _state["loaded_at"] >= CACHE_TTL_SECONDS:
        # Refresh lazily; a failed reload keeps the previous snapshot in place.
        try:
            _load()
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Catalogue refresh failed, keeping previous snapshot: %s", e)


def sellers() -> list[dict]:
    _ensure()
    return _state["sellers"]


def listings() -> list[dict]:
    _ensure()
    return _state["listings"]


def seller_by_id(seller_id: str) -> dict | None:
    _ensure()
    return _state["sellers_by_id"].get(seller_id)


def listing_by_id(listing_id: str) -> dict | None:
    _ensure()
    return _state["listings_by_id"].get(listing_id)


def listings_by_seller() -> dict[str, list[dict]]:
    _ensure()
    return _state["by_seller"]


def products() -> list[dict]:
    """The labelled product dataset (title, brand, photo, fingerprint)."""
    _ensure()
    return _state["products"]


def product_by_id(product_id: str) -> dict | None:
    _ensure()
    return _state["products_by_id"].get(product_id)


def product_by_hash(photo_hash: str) -> dict | None:
    _ensure()
    for p in _state["products"]:
        if p["photo_hash"] == photo_hash:
            return p
    return None


def debts() -> list[dict]:
    """The debt ledger (debtor, creditor, amount) from Postgres."""
    _ensure()
    return _state["debts"]


def stats() -> dict:
    with _lock:
        return {
            "source": _state["source"],
            "sellers": len(_state["sellers"]),
            "listings": len(_state["listings"]),
            "products": len(_state["products"]),
            "debts": len(_state["debts"]),
            "loaded_at": _state["loaded_at"],
            "age_seconds": round(time.time() - _state["loaded_at"], 1) if _state["loaded_at"] else None,
        }
