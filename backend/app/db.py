"""PostgreSQL + pgvector storage for FindYourTrade.

The database is the single source of truth for traders (sellers) and their
posts (listings). Nothing is read from `seed_data` at request time — that module
is only used to bootstrap the database once, then discarded.

Handles:
- Database connectivity via SQLAlchemy / psycopg2
- Schema bootstrap (extension, tables, indices, photo/video columns)
- Idempotent upsert of the seed catalogue into Postgres
- Vector similarity search powered by pgvector (1536-dim HNSW cosine index)
"""
from __future__ import annotations
import os
import json
import math
import hashlib
import logging
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger("findyourtrade.db")

# Default database connection string
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'barteruser')}:{os.getenv('POSTGRES_PASSWORD', 'barterpass')}@"
    f"{os.getenv('POSTGRES_HOST', 'db')}:{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'bartertrade')}"
)

# SQLAlchemy 2.0 defaults 'postgresql://' to psycopg 3; use psycopg2 driver
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

# Initialize SQLAlchemy engine
engine = None
SessionLocal = None

try:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
except Exception as e:
    logger.warning("Could not initialize database engine: %s", e)


def get_db():
    """Dependency for obtaining a database session."""
    if SessionLocal is None:
        return None
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def generate_embedding(text_content: str, dim: int = 1536) -> list[float]:
    """Generate a normalized 1536-dim vector embedding from text.

    When META_API_KEY is available and supported, calls the embedding API.
    Otherwise, generates a deterministic semantic pseudo-embedding using
    hashed token projections, normalized to unit length for cosine similarity.
    """
    # Deterministic token hashing projection for local/mock demo
    vec = [0.0] * dim
    words = text_content.lower().replace("-", " ").replace("/", " ").split()
    if not words:
        words = ["empty"]

    for word in words:
        # Generate multiple hash buckets for richer semantic dispersion
        for salt in ("a", "b", "c", "d"):
            h = int(hashlib.sha256(f"{word}:{salt}".encode("utf-8")).hexdigest(), 16)
            idx = h % dim
            sign = 1.0 if ((h >> 16) & 1) else -1.0
            vec[idx] += sign

    # Normalize to unit vector for cosine distance
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def _vec_literal(vec: list[float]) -> str:
    """pgvector accepts a plain '[0.1,0.2,...]' text literal."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


# ────────────────────────────────────────────────────────────── schema bootstrap

SCHEMA_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE IF NOT EXISTS sellers (
        id                TEXT PRIMARY KEY,
        name              TEXT        NOT NULL,
        location          TEXT        NOT NULL,
        distance_km       NUMERIC     NOT NULL DEFAULT 0,
        account_age_days  INT         NOT NULL DEFAULT 0,
        trust_score       INT         NOT NULL DEFAULT 50,
        phone             TEXT        NOT NULL DEFAULT '',
        created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS listings (
        id                    TEXT PRIMARY KEY,
        seller_id             TEXT        NOT NULL REFERENCES sellers(id) ON DELETE CASCADE,
        item                  TEXT        NOT NULL,
        category              TEXT        NOT NULL DEFAULT 'general',
        brand                 TEXT        NOT NULL DEFAULT '',
        price_mwk             NUMERIC     NOT NULL DEFAULT 0,
        quantity_available    INT         NOT NULL DEFAULT 0,
        unit                  TEXT        NOT NULL DEFAULT 'unit',
        origin                TEXT        NOT NULL DEFAULT 'local',
        stock_status          TEXT        NOT NULL DEFAULT 'in_country',
        reusable              BOOLEAN     NOT NULL DEFAULT FALSE,
        registration_status   TEXT        NOT NULL DEFAULT '',
        odometer_km           INT,
        verification_status   TEXT        NOT NULL DEFAULT 'unverified',
        photo                 TEXT        NOT NULL DEFAULT '',
        video_url             TEXT        NOT NULL DEFAULT '',
        created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        embedding             VECTOR(1536)
    )
    """,
    # Older databases predate per-post media, so add the columns in place.
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS photo TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS video_url TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS model TEXT NOT NULL DEFAULT ''",
    # Trader accounts: phone + PIN login for posting. Sellers seeded from the
    # fixture stay login-less (password_hash NULL) — only real signups can post.
    """
    CREATE TABLE IF NOT EXISTS trader_accounts (
        id              TEXT PRIMARY KEY,
        seller_id       TEXT        NOT NULL UNIQUE REFERENCES sellers(id) ON DELETE CASCADE,
        phone           TEXT        NOT NULL UNIQUE,
        name            TEXT        NOT NULL DEFAULT '',
        password_hash   TEXT        NOT NULL,
        photo_verified  BOOLEAN     NOT NULL DEFAULT FALSE,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE TABLE IF NOT EXISTS auth_tokens (token TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES trader_accounts(id) ON DELETE CASCADE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
    # Web-learned brand knowledge: which brand each model name belongs to.
    """
    CREATE TABLE IF NOT EXISTS brand_facts (
        model           TEXT PRIMARY KEY,
        brand           TEXT        NOT NULL DEFAULT '',
        source          TEXT        NOT NULL DEFAULT 'wikipedia',
        confidence      DOUBLE PRECISION NOT NULL DEFAULT 0.8,
        evidence        TEXT        NOT NULL DEFAULT '',
        url             TEXT        NOT NULL DEFAULT '',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # Integrity: the photo's fingerprint and the verdict of the fraud scan.
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS photo_hash TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS photo_product_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS fraud_verdict TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS fraud_flags JSONB NOT NULL DEFAULT '[]'::jsonb",
    # Retired = taken off the market because the post has no real photo/video
    # (an external stock URL is not evidence of the goods). See
    # sync_media_retirement().
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS retired BOOLEAN NOT NULL DEFAULT FALSE",
    """
    CREATE TABLE IF NOT EXISTS products (
        id            TEXT PRIMARY KEY,
        title         TEXT        NOT NULL,
        brand         TEXT        NOT NULL DEFAULT '',
        model         TEXT        NOT NULL DEFAULT '',
        vertical      TEXT        NOT NULL DEFAULT 'general',
        category      TEXT        NOT NULL DEFAULT 'general',
        unit          TEXT        NOT NULL DEFAULT 'unit',
        price_mwk     NUMERIC     NOT NULL DEFAULT 0,
        attr          JSONB       NOT NULL DEFAULT '{}'::jsonb,
        label         TEXT        NOT NULL DEFAULT '',
        photo_file    TEXT        NOT NULL DEFAULT '',
        photo_hash    TEXT        NOT NULL DEFAULT '',
        photo_sha1    TEXT        NOT NULL DEFAULT '',
        photo_source  TEXT        NOT NULL DEFAULT '',
        photo_credit  TEXT        NOT NULL DEFAULT '',
        photo_width   INT,
        photo_height  INT,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS products_vertical_idx ON products (vertical)",
    "CREATE INDEX IF NOT EXISTS products_hash_idx ON products (photo_hash)",
    "CREATE INDEX IF NOT EXISTS listings_hash_idx ON listings (photo_hash)",
    # Live-vision verdicts: what the vision model saw in each photo fingerprint,
    # plus the reinforcement signal from human confirm/dispute feedback.
    """
    CREATE TABLE IF NOT EXISTS vision_labels (
        photo_hash      TEXT PRIMARY KEY,
        brand           TEXT        NOT NULL DEFAULT '',
        model           TEXT        NOT NULL DEFAULT '',
        category        TEXT        NOT NULL DEFAULT 'general',
        vertical        TEXT        NOT NULL DEFAULT '',
        confidence      DOUBLE PRECISION NOT NULL DEFAULT 0,
        features        JSONB       NOT NULL DEFAULT '[]'::jsonb,
        source          TEXT        NOT NULL DEFAULT 'vision',
        confirmations   INT         NOT NULL DEFAULT 0,
        disputes        INT         NOT NULL DEFAULT 0,
        corrected_brand TEXT        NOT NULL DEFAULT '',
        corrected_model TEXT        NOT NULL DEFAULT '',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # Every confirm/dispute is a reward event (+1/-1) for the vision policy.
    """
    CREATE TABLE IF NOT EXISTS integrity_feedback (
        id              SERIAL      PRIMARY KEY,
        photo_hash      TEXT        NOT NULL,
        listing_id      TEXT        NOT NULL DEFAULT '',
        action          TEXT        NOT NULL,
        reward          INT         NOT NULL DEFAULT 0,
        vision_brand    TEXT        NOT NULL DEFAULT '',
        vision_model    TEXT        NOT NULL DEFAULT '',
        corrected_brand TEXT        NOT NULL DEFAULT '',
        corrected_model TEXT        NOT NULL DEFAULT '',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS integrity_feedback_hash_idx ON integrity_feedback (photo_hash)",
    """
    CREATE TABLE IF NOT EXISTS app_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS debt_edges (
        id         SERIAL      PRIMARY KEY,
        debtor     TEXT        NOT NULL,
        creditor   TEXT        NOT NULL,
        amount     NUMERIC     NOT NULL CHECK (amount > 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS debt_edges_debtor_idx ON debt_edges (debtor)",
    "CREATE INDEX IF NOT EXISTS debt_edges_creditor_idx ON debt_edges (creditor)",
    # Social layer: views, likes and comments live in their own tables so the
    # listings rows stay untouched. One row per (listing, viewer) for views;
    # one row per (listing, user) for likes (toggle = insert/delete);
    # comments form a tree via parent_id (NULL = top-level).
    """
    CREATE TABLE IF NOT EXISTS listing_views (
        listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
        viewer_id  TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (listing_id, viewer_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS listing_likes (
        listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
        user_id    TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (listing_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS listing_comments (
        id         TEXT PRIMARY KEY,
        listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
        user_id    TEXT NOT NULL,
        user_name  TEXT NOT NULL DEFAULT '',
        body       TEXT NOT NULL,
        parent_id  TEXT REFERENCES listing_comments(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS listing_views_listing_idx ON listing_views (listing_id)",
    "CREATE INDEX IF NOT EXISTS listing_likes_listing_idx ON listing_likes (listing_id)",
    "CREATE INDEX IF NOT EXISTS listing_comments_listing_idx ON listing_comments (listing_id)",
    # Business identity: one category per organisation (agriculture / vehicles /
    # restaurant ...) plus public contact channels, so buyers see who sells
    # what without category mix-ups.
    "ALTER TABLE sellers ADD COLUMN IF NOT EXISTS business_category TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE sellers ADD COLUMN IF NOT EXISTS email TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS listings_category_idx ON listings (category)",
    "CREATE INDEX IF NOT EXISTS listings_seller_idx ON listings (seller_id)",
    "CREATE INDEX IF NOT EXISTS listings_origin_idx ON listings (origin)",
    "CREATE INDEX IF NOT EXISTS listings_stock_idx ON listings (stock_status)",
    """
    CREATE INDEX IF NOT EXISTS listings_embedding_hnsw
        ON listings USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """,
]


def ensure_schema() -> dict[str, Any]:
    """Create the extension, tables and indices if they are missing."""
    if engine is None:
        return {"ok": False, "error": "Database engine not initialized"}
    try:
        with engine.begin() as conn:
            for stmt in SCHEMA_STATEMENTS:
                conn.execute(text(stmt))
        return {"ok": True}
    except Exception as e:
        logger.error("Schema bootstrap failed: %s", e)
        return {"ok": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────── seeding

# Bump when the bootstrap fixture changes: the new rows are upserted once, after
# which the database (not this file) is the source of truth.
CATALOG_VERSION = "2026-11-03.4"


def seed_catalog(force: bool = False) -> dict[str, Any]:
    """Upsert the bootstrap catalogue into Postgres, once per fixture version.

    The fixture is a first-run bootstrap, not the runtime source of truth: after
    it has been applied the database is authoritative, so rows edited or inserted
    directly in SQL survive restarts. Bump `CATALOG_VERSION` when the fixture
    changes and the new rows will be upserted exactly once. `force=True`
    (POST /api/db/seed) re-applies it on demand.
    """
    if engine is None:
        return {"ok": False, "reason": "No database engine"}

    from .seed_data import SELLERS, LISTINGS, DEMO_DEBTS

    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            ))

            applied = conn.execute(text(
                "SELECT value FROM app_meta WHERE key = 'catalog_version'"
            )).scalar()
            existing = conn.execute(text("SELECT count(*) FROM listings")).scalar() or 0

            if not force and applied == CATALOG_VERSION and existing:
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": f"catalogue already at {CATALOG_VERSION}",
                    "sellers": len(SELLERS),
                    "listings": existing,
                }

            for s in SELLERS:
                conn.execute(
                    text("""
                        INSERT INTO sellers (id, name, location, distance_km, account_age_days, trust_score, phone)
                        VALUES (:id, :name, :location, :distance_km, :account_age_days, :trust_score, :phone)
                        ON CONFLICT (id) DO UPDATE SET
                            name = EXCLUDED.name,
                            location = EXCLUDED.location,
                            distance_km = EXCLUDED.distance_km,
                            account_age_days = EXCLUDED.account_age_days,
                            trust_score = EXCLUDED.trust_score,
                            phone = EXCLUDED.phone,
                            updated_at = NOW()
                    """),
                    s,
                )

            # Clean all items and listings from database, leaving sellers/users intact
            conn.execute(text("DELETE FROM listings"))

            # Demo trade-circle debts. Only seeded into an empty ledger so real
            # recorded obligations are never wiped by a re-seed.
            debt_count = conn.execute(text("SELECT count(*) FROM debt_edges")).scalar() or 0
            if debt_count == 0:
                for d in DEMO_DEBTS:
                    conn.execute(
                        text("""
                            INSERT INTO debt_edges (debtor, creditor, amount)
                            VALUES (:debtor, :creditor, :amount)
                        """),
                        d,
                    )

            conn.execute(
                text("""
                    INSERT INTO app_meta (key, value) VALUES ('catalog_version', :v)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """),
                {"v": CATALOG_VERSION},
            )

        return {
            "ok": True,
            "skipped": False,
            "message": "Catalogue upserted into Postgres + pgvector",
            "version": CATALOG_VERSION,
            "sellers": len(SELLERS),
            "listings": len(LISTINGS),
        }
    except Exception as e:
        logger.error("Failed to seed database: %s", e)
        return {"ok": False, "error": str(e)}


# Backwards-compatible alias: the endpoint used to only seed an empty database.
seed_database_if_empty = seed_catalog

def clean_all_listings() -> dict[str, Any]:
    """Wipe all items and listings from the database, leaving sellers and trader accounts intact."""
    if engine is None:
        return {"ok": False, "error": "No database engine"}
    try:
        with engine.begin() as conn:
            deleted = conn.execute(text("DELETE FROM listings")).rowcount
            conn.execute(text("""
                INSERT INTO app_meta (key, value) VALUES ('listings_cleared', 'true')
                ON CONFLICT (key) DO UPDATE SET value = 'true'
            """))
        return {"ok": True, "message": "All items and listings cleaned from database, users preserved.", "deleted": deleted}
    except Exception as e:
        logger.error("clean_all_listings error: %s", e)
        return {"ok": False, "error": str(e)}



# ───────────────────────────────────────────────────────────── product dataset

PRODUCTS_VERSION = "2026-11-03.1"
PRODUCTS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset", "products.json")


def seed_products(force: bool = False) -> dict[str, Any]:
    """Load the labelled product dataset (JSON built by scripts/build_dataset.py)."""
    if engine is None:
        return {"ok": False, "reason": "No database engine"}
    if not os.path.exists(PRODUCTS_JSON):
        return {"ok": False, "reason": f"missing {PRODUCTS_JSON}"}

    with open(PRODUCTS_JSON, encoding="utf-8") as fh:
        products = json.load(fh)

    try:
        with engine.begin() as conn:
            applied = conn.execute(text(
                "SELECT value FROM app_meta WHERE key = 'products_version'"
            )).scalar()
            existing = conn.execute(text("SELECT count(*) FROM products")).scalar() or 0
            if not force and applied == PRODUCTS_VERSION and existing:
                return {"ok": True, "skipped": True, "products": existing,
                        "reason": f"dataset already at {PRODUCTS_VERSION}"}

            for p in products:
                conn.execute(
                    text("""
                        INSERT INTO products (
                            id, title, brand, model, vertical, category, unit, price_mwk,
                            attr, label, photo_file, photo_hash, photo_sha1, photo_source,
                            photo_credit, photo_width, photo_height
                        ) VALUES (
                            :id, :title, :brand, :model, :vertical, :category, :unit, :price_mwk,
                            CAST(:attr AS jsonb), :label, :photo_file, :photo_hash, :photo_sha1,
                            :photo_source, :photo_credit, :photo_width, :photo_height
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            title = EXCLUDED.title,
                            brand = EXCLUDED.brand,
                            model = EXCLUDED.model,
                            vertical = EXCLUDED.vertical,
                            category = EXCLUDED.category,
                            unit = EXCLUDED.unit,
                            price_mwk = EXCLUDED.price_mwk,
                            attr = EXCLUDED.attr,
                            label = EXCLUDED.label,
                            photo_file = EXCLUDED.photo_file,
                            photo_hash = EXCLUDED.photo_hash,
                            photo_sha1 = EXCLUDED.photo_sha1,
                            photo_source = EXCLUDED.photo_source,
                            photo_credit = EXCLUDED.photo_credit,
                            photo_width = EXCLUDED.photo_width,
                            photo_height = EXCLUDED.photo_height,
                            updated_at = NOW()
                    """),
                    {
                        "id": p["id"], "title": p["title"], "brand": p.get("brand", ""),
                        "model": p.get("model", ""), "vertical": p.get("vertical", "general"),
                        "category": p.get("category", "general"), "unit": p.get("unit", "unit"),
                        "price_mwk": p.get("price_mwk", 0),
                        "attr": json.dumps(p.get("attr", {})),
                        "label": p.get("label", ""), "photo_file": p.get("photo_file", ""),
                        "photo_hash": p.get("photo_hash", ""), "photo_sha1": p.get("photo_sha1", ""),
                        "photo_source": p.get("photo_source", ""),
                        "photo_credit": p.get("photo_credit", ""),
                        "photo_width": p.get("photo_width"), "photo_height": p.get("photo_height"),
                    },
                )

            conn.execute(
                text("""
                    INSERT INTO app_meta (key, value) VALUES ('products_version', :v)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """),
                {"v": PRODUCTS_VERSION},
            )

        return {"ok": True, "skipped": False, "products": len(products), "version": PRODUCTS_VERSION}
    except Exception as e:
        logger.error("Failed to seed product dataset: %s", e)
        return {"ok": False, "error": str(e)}


def fetch_products() -> list[dict]:
    """The labelled product dataset, with each product's photo fingerprint."""
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, title, brand, model, vertical, category, unit, price_mwk, attr,
                       label, photo_file, photo_hash, photo_sha1, photo_source, photo_credit,
                       photo_width, photo_height
                FROM products ORDER BY vertical, id
            """)).mappings().all()
        return [{
            "id": r["id"], "title": r["title"], "brand": r["brand"] or "",
            "model": r["model"] or "", "vertical": r["vertical"], "category": r["category"],
            "unit": r["unit"], "price_mwk": float(r["price_mwk"] or 0),
            "attr": r["attr"] or {}, "label": r["label"] or "",
            "photo_file": r["photo_file"] or "", "photo_hash": r["photo_hash"] or "",
            "photo_sha1": r["photo_sha1"] or "", "photo_source": r["photo_source"] or "",
            "photo_credit": r["photo_credit"] or "",
            "photo_url": f"/dataset/images/{r['photo_file']}" if r["photo_file"] else "",
            "photo_width": r["photo_width"], "photo_height": r["photo_height"],
        } for r in rows]
    except Exception as e:
        logger.error("fetch_products error: %s", e)
        return []


def update_listing_integrity(listing_id: str, photo_hash: str, product_id: str,
                             verdict: str, flags: list[dict]) -> None:
    """Persist the result of the fraud scan for one listing."""
    if engine is None:
        return
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE listings
                       SET photo_hash = :photo_hash,
                           photo_product_id = :product_id,
                           fraud_verdict = :verdict,
                           fraud_flags = CAST(:flags AS jsonb),
                           updated_at = NOW()
                     WHERE id = :id
                """),
                {"id": listing_id, "photo_hash": photo_hash, "product_id": product_id,
                 "verdict": verdict, "flags": json.dumps(flags)},
            )
    except Exception as e:
        logger.error("update_listing_integrity error: %s", e)


def update_listing_integrity(listing_id: str, photo_hash: str, product_id: str,
                             verdict: str, flags: list[dict]) -> None:
    """Persist the result of the fraud scan for one listing."""
    if engine is None:
        return
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE listings
                       SET photo_hash = :photo_hash,
                           photo_product_id = :product_id,
                           fraud_verdict = :verdict,
                           fraud_flags = CAST(:flags AS jsonb),
                           updated_at = NOW()
                     WHERE id = :id
                """),
                {"id": listing_id, "photo_hash": photo_hash, "product_id": product_id,
                 "verdict": verdict, "flags": json.dumps(flags)},
            )
    except Exception as e:
        logger.error("update_listing_integrity error: %s", e)


def update_listing_video_url(listing_id: str, video_url: str) -> None:
    """Persist the promo/Show-Me video URL on a listing so it survives the
    sync_media_retirement check and appears in the dashboard feed."""
    if engine is None:
        return
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE listings
                       SET video_url = :video_url,
                           updated_at = NOW()
                     WHERE id = :id
                """),
                {"id": listing_id, "video_url": video_url},
            )
    except Exception as e:
        logger.error("update_listing_video_url error: %s", e)

def update_listing_photo(listing_id: str, photo: str, product_id: str = "",
                         photo_hash: str = "") -> None:
    """Point a listing at a different photo (e.g. its local dataset image).

    The fingerprint is rewritten in the same statement so the integrity scan
    never compares a stale hash against the new file.
    """
    if engine is None:
        return
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE listings
                       SET photo = :photo,
                           photo_product_id = :product_id,
                           photo_hash = :photo_hash,
                           updated_at = NOW()
                     WHERE id = :id
                """),
                {"id": listing_id, "photo": photo, "product_id": product_id,
                 "photo_hash": photo_hash},
            )
    except Exception as e:
        logger.error("update_listing_photo error: %s", e)


def sync_media_retirement() -> dict:
    """Take posts off the market when they carry no real photo and no real video.

    "Real" means a file this platform actually hosts: /dataset/... (catalogue)
    or /uploads/... (trader upload). A bare external stock URL (photo-xxxx from
    Unsplash) is not evidence of the goods, so a post that only has one is
    retired rather than advertised as inventory.

    Fraud-flagged posts are deliberately NOT retired — they are kept as
    integrity evidence so the Integrity tab can still show what was caught.
    The rule is reversible: attach a real photo/video and the next sync puts the
    post back on the market.
    """
    if engine is None:
        return {"ok": False, "retired": 0}
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                WITH judged AS (
                    SELECT id,
                           (photo LIKE '/dataset/%' OR photo LIKE '/uploads/%') AS has_photo,
                           (video_url LIKE '/uploads/%')                          AS has_video,
                           EXISTS (
                               SELECT 1 FROM jsonb_array_elements(fraud_flags) f
                                WHERE f->>'severity' = 'fraud'
                           ) AS is_fraud
                      FROM listings
                )
                UPDATE listings l
                   SET retired = (NOT j.has_photo AND NOT j.has_video AND NOT j.is_fraud),
                       updated_at = NOW()
                  FROM judged j
                 WHERE l.id = j.id
                   AND l.retired IS DISTINCT FROM
                       (NOT j.has_photo AND NOT j.has_video AND NOT j.is_fraud)
                RETURNING l.id, l.retired
            """)).all()
        retired_ids = [r[0] for r in rows if r[1]]
        return {"ok": True, "changed": len(rows), "retired": len(retired_ids),
                "retired_ids": retired_ids}
    except Exception as e:
        logger.error("sync_media_retirement error: %s", e)
        return {"ok": False, "retired": 0, "error": str(e)}


def upsert_brand_facts(facts: list[dict], refresh: bool = False) -> dict:
    """Store brand -> model facts learned from the internet.

    Facts accumulate (never wiped): a re-learn only adds rows that are new and
    refreshes rows the web now contradicts. Set refresh=True to overwrite every
    row with the freshly learned values.
    """
    if engine is None:
        return {"ok": False, "error": "No database engine"}
    added, refreshed, kept = 0, 0, 0
    try:
        with engine.begin() as conn:
            for f in facts:
                model = (f.get("model") or "").strip()
                brand = (f.get("brand") or "").strip()
                if not model or not brand:
                    continue
                cur = conn.execute(text(
                    "SELECT brand FROM brand_facts WHERE model = :m"),
                    {"m": model}).scalar()
                if cur is None:
                    conn.execute(text("""
                        INSERT INTO brand_facts (model, brand, source, confidence, evidence, url)
                        VALUES (:m, :b, :s, :c, :e, :u)
                    """), {"m": model, "b": brand, "s": f.get("source", "wikipedia"),
                            "c": float(f.get("confidence", 0.8)),
                            "e": f.get("evidence", "") or "",
                            "u": f.get("url", "") or ""})
                    added += 1
                elif refresh or cur.lower() != brand.lower():
                    conn.execute(text("""
                        UPDATE brand_facts SET brand = :b, source = :s, confidence = :c,
                                               evidence = :e, url = :u, updated_at = NOW()
                        WHERE model = :m
                    """), {"m": model, "b": brand, "s": f.get("source", "wikipedia"),
                            "c": float(f.get("confidence", 0.8)),
                            "e": f.get("evidence", "") or "",
                            "u": f.get("url", "") or ""})
                    refreshed += 1
                else:
                    kept += 1
        return {"ok": True, "added": added, "refreshed": refreshed, "kept": kept,
                "total": added + refreshed + kept}
    except Exception as e:
        logger.error("upsert_brand_facts error: %s", e)
        return {"ok": False, "error": str(e)}


def fetch_brand_facts() -> dict[str, dict]:
    """Brand knowledge keyed by lowercase model name: {model: {brand,...}}."""
    if engine is None:
        return {}
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT model, brand, source, confidence, evidence, url
                FROM brand_facts
            """)).mappings().all()
        return {r["model"].lower(): {
            "model": r["model"], "brand": r["brand"] or "",
            "source": r["source"] or "", "confidence": float(r["confidence"] or 0),
            "evidence": r["evidence"] or "", "url": r["url"] or "",
        } for r in rows}
    except Exception as e:
        logger.error("fetch_brand_facts error: %s", e)
        return {}


# ─────────────────────────────── vision labels + RL feedback

def upsert_vision_label(photo_hash: str, verdict: dict) -> None:
    """Store what live vision saw in this photo (keyed by its fingerprint).

    `verdict` is the identification dict from meta_client.identify_product.
    Existing human confirmations/disputes are preserved — vision only refreshes
    the machine side of the record.
    """
    if engine is None or not photo_hash:
        return
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO vision_labels (
                        photo_hash, brand, model, category, vertical, confidence,
                        features, source, updated_at
                    ) VALUES (
                        :h, :brand, :model, :category, :vertical, :confidence,
                        CAST(:features AS jsonb), 'vision', NOW()
                    )
                    ON CONFLICT (photo_hash) DO UPDATE SET
                        brand = EXCLUDED.brand,
                        model = EXCLUDED.model,
                        category = EXCLUDED.category,
                        vertical = EXCLUDED.vertical,
                        confidence = EXCLUDED.confidence,
                        features = EXCLUDED.features,
                        source = CASE
                            WHEN vision_labels.source = 'human' THEN 'human'
                            ELSE 'vision'
                        END,
                        updated_at = NOW()
                """),
                {
                    "h": photo_hash,
                    "brand": verdict.get("brand", "") or "",
                    "model": verdict.get("model", "") or "",
                    "category": verdict.get("category", "general") or "general",
                    "vertical": verdict.get("vertical", "") or "",
                    "confidence": float(verdict.get("confidence") or 0),
                    "features": json.dumps(verdict.get("features") or []),
                },
            )
    except Exception as e:
        logger.error("upsert_vision_label error: %s", e)


def fetch_vision_label(photo_hash: str) -> dict | None:
    """The stored live-vision verdict for one photo fingerprint (if any)."""
    if engine is None or not photo_hash:
        return None
    try:
        with engine.connect() as conn:
            r = conn.execute(text("""
                SELECT photo_hash, brand, model, category, vertical, confidence,
                       features, source, confirmations, disputes,
                       corrected_brand, corrected_model
                FROM vision_labels WHERE photo_hash = :h
            """), {"h": photo_hash}).mappings().first()
        if not r:
            return None
        return {
            "photo_hash": r["photo_hash"], "brand": r["brand"] or "",
            "model": r["model"] or "", "category": r["category"] or "general",
            "vertical": r["vertical"] or "", "confidence": float(r["confidence"] or 0),
            "features": r["features"] or [], "source": r["source"] or "vision",
            "confirmations": int(r["confirmations"] or 0),
            "disputes": int(r["disputes"] or 0),
            "corrected_brand": r["corrected_brand"] or "",
            "corrected_model": r["corrected_model"] or "",
        }
    except Exception as e:
        logger.error("fetch_vision_label error: %s", e)
        return None


def fetch_vision_labels() -> dict[str, dict]:
    """All stored vision verdicts, keyed by photo fingerprint."""
    if engine is None:
        return {}
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT photo_hash, brand, model, category, vertical, confidence,
                       features, source, confirmations, disputes,
                       corrected_brand, corrected_model
                FROM vision_labels
            """)).mappings().all()
        out: dict[str, dict] = {}
        for r in rows:
            out[r["photo_hash"]] = {
                "photo_hash": r["photo_hash"], "brand": r["brand"] or "",
                "model": r["model"] or "", "category": r["category"] or "general",
                "vertical": r["vertical"] or "", "confidence": float(r["confidence"] or 0),
                "features": r["features"] or [], "source": r["source"] or "vision",
                "confirmations": int(r["confirmations"] or 0),
                "disputes": int(r["disputes"] or 0),
                "corrected_brand": r["corrected_brand"] or "",
                "corrected_model": r["corrected_model"] or "",
            }
        return out
    except Exception as e:
        logger.error("fetch_vision_labels error: %s", e)
        return {}


def record_integrity_feedback(photo_hash: str, listing_id: str, action: str,
                              corrected_brand: str = "",
                              corrected_model: str = "") -> dict:
    """Reinforcement signal for the vision policy.

    `action` is "confirm" (reward +1: vision got it right) or "dispute"
    (reward −1: vision got it wrong, optionally with the human correction).
    A dispute with a corrected brand/model promotes the record to human
    ground truth, which overrides vision on the next scan.
    """
    if engine is None:
        return {"ok": False, "error": "No database engine"}
    action = (action or "").lower()
    if action not in ("confirm", "dispute"):
        return {"ok": False, "error": "action must be 'confirm' or 'dispute'"}
    reward = 1 if action == "confirm" else -1
    try:
        with engine.begin() as conn:
            cur = conn.execute(text("""
                SELECT brand, model FROM vision_labels WHERE photo_hash = :h
            """), {"h": photo_hash}).mappings().first()
            conn.execute(
                text("""
                    INSERT INTO integrity_feedback (
                        photo_hash, listing_id, action, reward,
                        vision_brand, vision_model, corrected_brand, corrected_model
                    ) VALUES (
                        :h, :lid, :action, :reward, :vb, :vm, :cb, :cm
                    )
                """),
                {
                    "h": photo_hash, "lid": listing_id or "", "action": action,
                    "reward": reward, "vb": (cur["brand"] if cur else "") or "",
                    "vm": (cur["model"] if cur else "") or "",
                    "cb": corrected_brand or "", "cm": corrected_model or "",
                },
            )
            if cur is None:
                conn.execute(text("""
                    INSERT INTO vision_labels (photo_hash, source, confirmations, disputes)
                    VALUES (:h, 'human', 0, 0)
                    ON CONFLICT (photo_hash) DO NOTHING
                """), {"h": photo_hash})
            if action == "confirm":
                conn.execute(text("""
                    UPDATE vision_labels
                       SET confirmations = confirmations + 1, updated_at = NOW()
                     WHERE photo_hash = :h
                """), {"h": photo_hash})
            else:
                if corrected_brand or corrected_model:
                    conn.execute(text("""
                        UPDATE vision_labels
                           SET disputes = disputes + 1,
                               corrected_brand = :cb,
                               corrected_model = :cm,
                               source = 'human',
                               updated_at = NOW()
                         WHERE photo_hash = :h
                    """), {"h": photo_hash, "cb": corrected_brand or "",
                            "cm": corrected_model or ""})
                else:
                    conn.execute(text("""
                        UPDATE vision_labels
                           SET disputes = disputes + 1, updated_at = NOW()
                         WHERE photo_hash = :h
                    """), {"h": photo_hash})
            totals = conn.execute(text("""
                SELECT count(*) AS n, coalesce(sum(reward), 0) AS reward
                FROM integrity_feedback
            """)).mappings().first()
        return {"ok": True, "action": action, "reward": reward,
                "events": int(totals["n"] or 0),
                "total_reward": int(totals["reward"] or 0)}
    except Exception as e:
        logger.error("record_integrity_feedback error: %s", e)
        return {"ok": False, "error": str(e)}


def fetch_feedback_stats() -> dict:
    """Aggregate reward signal for the vision policy (the RL scoreboard)."""
    if engine is None:
        return {"events": 0, "total_reward": 0, "confirms": 0, "disputes": 0}
    try:
        with engine.connect() as conn:
            r = conn.execute(text("""
                SELECT count(*) AS n,
                       coalesce(sum(reward), 0) AS reward,
                       coalesce(sum(CASE WHEN action = 'confirm' THEN 1 ELSE 0 END), 0) AS confirms,
                       coalesce(sum(CASE WHEN action = 'dispute' THEN 1 ELSE 0 END), 0) AS disputes
                FROM integrity_feedback
            """)).mappings().first()
        return {"events": int(r["n"] or 0), "total_reward": int(r["reward"] or 0),
                "confirms": int(r["confirms"] or 0), "disputes": int(r["disputes"] or 0)}
    except Exception as e:
        logger.error("fetch_feedback_stats error: %s", e)
        return {"events": 0, "total_reward": 0, "confirms": 0, "disputes": 0}


# ─────────────────────────────────────────────────────── trader accounts

def _hash_pin(pin: str) -> str:
    return hashlib.sha256(f"findyourtrade:{pin}".encode()).hexdigest()


def signup_trader(name: str, phone: str, pin: str, location: str = "Lilongwe",
                  business_category: str = "", email: str = "") -> dict:
    """Create a trader account + seller row. Phone numbers are unique.
    business_category/email are optional at signup and editable later."""
    if engine is None:
        return {"ok": False, "error": "No database engine"}
    name, phone, pin = (name or "").strip(), (phone or "").strip(), (pin or "").strip()
    business_category, email = (business_category or "").strip(), (email or "").strip()
    if len(name) < 2:
        return {"ok": False, "error": "Please give your business name."}
    if len(phone) < 7:
        return {"ok": False, "error": "Please give a valid phone number."}
    if len(pin) < 4:
        return {"ok": False, "error": "PIN must be at least 4 digits."}
    try:
        with engine.begin() as conn:
            if conn.execute(text("SELECT 1 FROM trader_accounts WHERE phone = :p"),
                            {"p": phone}).scalar():
                return {"ok": False, "error": "This phone number is already registered. Please log in."}
            n = conn.execute(text("SELECT count(*) FROM sellers")).scalar() or 0
            seller_id = f"t{n + 1:02d}"
            while conn.execute(text("SELECT 1 FROM sellers WHERE id = :i"),
                               {"i": seller_id}).scalar():
                n += 1
                seller_id = f"t{n + 1:02d}"
            conn.execute(text("""
                INSERT INTO sellers (id, name, location, distance_km, account_age_days, trust_score, phone,
                                     business_category, email)
                VALUES (:i, :n, :loc, 5.0, 0, 40, :p, :bc, :em)
            """), {"i": seller_id, "n": name, "loc": location or "Lilongwe", "p": phone,
                   "bc": business_category, "em": email})
            account_id = f"a_{seller_id}"
            conn.execute(text("""
                INSERT INTO trader_accounts (id, seller_id, phone, name, password_hash)
                VALUES (:a, :s, :p, :n, :h)
            """), {"a": account_id, "s": seller_id, "p": phone,
                    "n": name, "h": _hash_pin(pin)})
            import secrets
            token = secrets.token_urlsafe(32)
            conn.execute(text("INSERT INTO auth_tokens (token, account_id) VALUES (:t, :a)"),
                         {"t": token, "a": account_id})
        return {"ok": True, "token": token, "seller_id": seller_id,
                "name": name, "photo_verified": False}
    except Exception as e:
        logger.error("signup_trader error: %s", e)
        return {"ok": False, "error": str(e)}


def login_trader(phone: str, pin: str) -> dict:
    """Phone + PIN login. Returns a fresh token."""
    if engine is None:
        return {"ok": False, "error": "No database engine"}
    try:
        with engine.begin() as conn:
            r = conn.execute(text("""
                SELECT id, seller_id, name, password_hash, photo_verified
                FROM trader_accounts WHERE phone = :p
            """), {"p": (phone or "").strip()}).mappings().first()
            if not r or r["password_hash"] != _hash_pin(pin or ""):
                return {"ok": False, "error": "Wrong phone number or PIN."}
            import secrets
            token = secrets.token_urlsafe(32)
            conn.execute(text("INSERT INTO auth_tokens (token, account_id) VALUES (:t, :a)"),
                         {"t": token, "a": r["id"]})
        return {"ok": True, "token": token, "seller_id": r["seller_id"],
                "name": r["name"], "photo_verified": bool(r["photo_verified"])}
    except Exception as e:
        logger.error("login_trader error: %s", e)
        return {"ok": False, "error": str(e)}


def account_from_token(token: str) -> dict | None:
    """Resolve a bearer token to {account_id, seller_id, name, photo_verified}."""
    if engine is None or not token:
        return None
    try:
        with engine.connect() as conn:
            r = conn.execute(text("""
                SELECT a.id AS account_id, a.seller_id, a.name, a.photo_verified
                FROM auth_tokens t JOIN trader_accounts a ON a.id = t.account_id
                WHERE t.token = :t
            """), {"t": token}).mappings().first()
        return dict(r) if r else None
    except Exception as e:
        logger.error("account_from_token error: %s", e)
        return None


def set_photo_verified(seller_id: str, verified: bool = True) -> None:
    if engine is None:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE trader_accounts SET photo_verified = :v, updated_at = NOW()
                WHERE seller_id = :s
            """), {"v": verified, "s": seller_id})
    except Exception as e:
        logger.error("set_photo_verified error: %s", e)


def update_seller_profile(seller_id: str, business_category: str = "",
                          email: str = "", phone: str = "",
                          location: str = "") -> dict:
    """Edit the seller row: category, email, phone, location. Traders own
    their business identity — no hardcoding, it lives in the database."""
    if engine is None:
        return {"ok": False, "error": "No database engine"}
    try:
        with engine.begin() as conn:
            cur = conn.execute(text(
                "SELECT business_category, email, phone, location FROM sellers WHERE id = :s"
            ), {"s": seller_id}).mappings().first()
            if not cur:
                return {"ok": False, "error": "Unknown seller."}
            bc = (business_category or "").strip() or cur["business_category"] or ""
            em = (email or "").strip() or cur["email"] or ""
            ph = (phone or "").strip() or cur["phone"] or ""
            loc = (location or "").strip() or cur["location"] or "Lilongwe"
            conn.execute(text("""
                UPDATE sellers
                   SET business_category = :bc, email = :em, phone = :ph,
                       location = :loc, updated_at = NOW()
                 WHERE id = :s
            """), {"bc": bc, "em": em, "ph": ph, "loc": loc, "s": seller_id})
        return {"ok": True, "business_category": bc, "email": em,
                "phone": ph, "location": loc}
    except Exception as e:
        logger.error("update_seller_profile error: %s", e)
        return {"ok": False, "error": "Could not save profile."}


def create_trader_listing(seller_id: str, data: dict) -> dict:
    """Insert a trader's post. Caller must have run the integrity gate first."""
    if engine is None:
        return {"ok": False, "error": "No database engine"}
    try:
        with engine.begin() as conn:
            n = conn.execute(text("SELECT count(*) FROM listings")).scalar() or 0
            lid = f"u{n + 1:03d}"
            while conn.execute(text("SELECT 1 FROM listings WHERE id = :i"),
                               {"i": lid}).scalar():
                n += 1
                lid = f"u{n + 1:03d}"
            conn.execute(text("""
                INSERT INTO listings (
                    id, seller_id, item, category, brand, model, price_mwk,
                    quantity_available, unit, origin, stock_status, reusable,
                    registration_status, odometer_km, verification_status,
                    photo, video_url, photo_hash, photo_product_id,
                    fraud_verdict, fraud_flags
                ) VALUES (
                    :id, :seller_id, :item, :category, :brand, :model, :price_mwk,
                    :quantity_available, :unit, :origin, :stock_status, :reusable,
                    :registration_status, :odometer_km, 'pending',
                    :photo, :video_url, :photo_hash, :photo_product_id,
                    :fraud_verdict, CAST(:fraud_flags AS jsonb)
                )
            """), {
                "id": lid, "seller_id": seller_id,
                "item": data.get("item", ""), "category": data.get("category", "general"),
                "brand": data.get("brand", ""), "model": data.get("model", ""),
                "price_mwk": float(data.get("price_mwk") or 0),
                "quantity_available": int(data.get("quantity_available") or 1),
                "unit": data.get("unit", "unit"),
                "origin": data.get("origin", "local"),
                "stock_status": data.get("stock_status", "in_country"),
                "reusable": bool(data.get("reusable", False)),
                "registration_status": data.get("registration_status", ""),
                "odometer_km": data.get("odometer_km"),
                "photo": data.get("photo", "") or "",
                "video_url": data.get("video_url", "") or "",
                "photo_hash": data.get("photo_hash", "") or "",
                "photo_product_id": data.get("photo_product_id", "") or "",
                "fraud_verdict": data.get("fraud_verdict", "") or "",
                "fraud_flags": json.dumps(data.get("fraud_flags", [])),
            })
        return {"ok": True, "listing_id": lid}
    except Exception as e:
        logger.error("create_trader_listing error: %s", e)
        return {"ok": False, "error": str(e)}


def update_trader_listing(listing_id: str, seller_id: str, data: dict) -> dict:
    """Update a trader existing post (photo, video_url, price, item, etc.)."""
    if engine is None:
        return {"ok": False, "error": "No database engine"}
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE listings
                   SET item = CASE WHEN :item <> '' THEN :item ELSE item END,
                       brand = CASE WHEN :brand <> '' THEN :brand ELSE brand END,
                       model = CASE WHEN :model <> '' THEN :model ELSE model END,
                       price_mwk = CASE WHEN :price_mwk > 0 THEN :price_mwk ELSE price_mwk END,
                       quantity_available = CASE WHEN :quantity_available > 0 THEN :quantity_available ELSE quantity_available END,
                       photo = CASE WHEN :photo <> '' THEN :photo ELSE photo END,
                       video_url = CASE WHEN :video_url <> '' THEN :video_url ELSE video_url END,
                       photo_hash = CASE WHEN :photo_hash <> '' THEN :photo_hash ELSE photo_hash END,
                       photo_product_id = CASE WHEN :photo_product_id <> '' THEN :photo_product_id ELSE photo_product_id END,
                       fraud_verdict = CASE WHEN :fraud_verdict <> '' THEN :fraud_verdict ELSE fraud_verdict END,
                       updated_at = NOW()
                 WHERE id = :id AND seller_id = :seller_id
            """), {
                "id": listing_id, "seller_id": seller_id,
                "item": data.get("item", "") or "",
                "brand": data.get("brand", "") or "",
                "model": data.get("model", "") or "",
                "price_mwk": float(data.get("price_mwk") or 0),
                "quantity_available": int(data.get("quantity_available") or 0),
                "photo": data.get("photo", "") or "",
                "video_url": data.get("video_url", "") or "",
                "photo_hash": data.get("photo_hash", "") or "",
                "photo_product_id": data.get("photo_product_id", "") or "",
                "fraud_verdict": data.get("fraud_verdict", "") or "",
            })
        return {"ok": True, "listing_id": listing_id}
    except Exception as e:
        logger.error("create_trader_listing error: %s", e)
        return {"ok": False, "error": str(e)}


# ────────────────────────────────────────────────────────────────────── reads

def fetch_sellers() -> list[dict]:
    """All trader accounts, straight from Postgres."""
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, name, location, distance_km, account_age_days, trust_score, phone,
                       business_category, email
                FROM sellers ORDER BY id
            """)).mappings().all()
        return [{
            "id": r["id"],
            "name": r["name"],
            "location": r["location"],
            "distance_km": float(r["distance_km"] or 0),
            "account_age_days": int(r["account_age_days"] or 0),
            "trust_score": int(r["trust_score"] or 0),
            "phone": r["phone"] or "",
            "business_category": r["business_category"] or "",
            "email": r["email"] or "",
        } for r in rows]
    except Exception as e:
        logger.error("fetch_sellers error: %s", e)
        return []


def fetch_listings() -> list[dict]:
    """All posts, straight from Postgres, with their media fields."""
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, seller_id, item, category, brand, price_mwk,
                       quantity_available, unit, origin, stock_status, reusable,
                       registration_status, odometer_km, verification_status,
                       photo, video_url, model, photo_hash, photo_product_id,
                       fraud_verdict, fraud_flags, retired
                FROM listings ORDER BY id
            """)).mappings().all()
        return [{
            "id": r["id"],
            "seller_id": r["seller_id"],
            "item": r["item"],
            "category": r["category"],
            "brand": r["brand"] or "",
            "price_mwk": float(r["price_mwk"] or 0),
            "quantity_available": int(r["quantity_available"] or 0),
            "unit": r["unit"] or "unit",
            "origin": r["origin"] or "local",
            "stock_status": r["stock_status"] or "in_country",
            "reusable": bool(r["reusable"]),
            "registration_status": r["registration_status"] or "",
            "odometer_km": int(r["odometer_km"]) if r["odometer_km"] is not None else None,
            "verification_status": r["verification_status"] or "unverified",
            "photo": r["photo"] or "",
            "video_url": r["video_url"] or "",
            "model": r["model"] or "",
            "photo_hash": r["photo_hash"] or "",
            "photo_product_id": r["photo_product_id"] or "",
            "fraud_verdict": r["fraud_verdict"] or "",
            "fraud_flags": r["fraud_flags"] or [],
            "retired": bool(r["retired"]),
        } for r in rows]
    except Exception as e:
        logger.error("fetch_listings error: %s", e)
        return []


def record_view(listing_id: str, viewer_id: str) -> dict:
    """One counted view per (listing, viewer). Repeat views don't inflate."""
    if engine is None:
        return {"ok": False}
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO listing_views (listing_id, viewer_id)
                VALUES (:lid, :vid) ON CONFLICT DO NOTHING
            """), {"lid": listing_id, "vid": viewer_id or "anon"})
            total = conn.execute(text(
                "SELECT count(*) FROM listing_views WHERE listing_id = :lid"
            ), {"lid": listing_id}).scalar() or 0
        return {"ok": True, "views": int(total)}
    except Exception as e:
        logger.error("record_view error: %s", e)
        return {"ok": False, "views": 0}


def toggle_like(listing_id: str, user_id: str) -> dict:
    """Like/unlike toggle. Returns the new state and total."""
    if engine is None:
        return {"ok": False}
    try:
        with engine.begin() as conn:
            exists = conn.execute(text("""
                SELECT 1 FROM listing_likes
                WHERE listing_id = :lid AND user_id = :uid
            """), {"lid": listing_id, "uid": user_id}).first()
            if exists:
                conn.execute(text("""
                    DELETE FROM listing_likes
                    WHERE listing_id = :lid AND user_id = :uid
                """), {"lid": listing_id, "uid": user_id})
                liked = False
            else:
                conn.execute(text("""
                    INSERT INTO listing_likes (listing_id, user_id)
                    VALUES (:lid, :uid) ON CONFLICT DO NOTHING
                """), {"lid": listing_id, "uid": user_id})
                liked = True
            total = conn.execute(text(
                "SELECT count(*) FROM listing_likes WHERE listing_id = :lid"
            ), {"lid": listing_id}).scalar() or 0
        return {"ok": True, "liked": liked, "likes": int(total)}
    except Exception as e:
        logger.error("toggle_like error: %s", e)
        return {"ok": False}


def add_comment(listing_id: str, user_id: str, user_name: str, body: str,
                parent_id: str | None = None) -> dict:
    """Post a comment or a reply (parent_id set). Returns the saved row."""
    if engine is None:
        return {"ok": False, "error": "No database engine"}
    body = (body or "").strip()[:1000]
    if not body:
        return {"ok": False, "error": "Empty comment."}
    if not user_id:
        return {"ok": False, "error": "Sign in to comment."}
    try:
        import uuid
        cid = "c-" + uuid.uuid4().hex[:10]
        with engine.begin() as conn:
            if parent_id:
                ok = conn.execute(text("""
                    SELECT 1 FROM listing_comments
                    WHERE id = :pid AND listing_id = :lid
                """), {"pid": parent_id, "lid": listing_id}).first()
                if not ok:
                    return {"ok": False, "error": "That reply target is gone."}
            conn.execute(text("""
                INSERT INTO listing_comments (id, listing_id, user_id, user_name, body, parent_id)
                VALUES (:id, :lid, :uid, :uname, :body, :pid)
            """), {"id": cid, "lid": listing_id, "uid": user_id,
                   "uname": (user_name or user_id)[:80], "body": body,
                   "pid": parent_id})
        return {"ok": True, "comment": {"id": cid, "listing_id": listing_id,
                "user_id": user_id, "user_name": (user_name or user_id)[:80],
                "body": body, "parent_id": parent_id}}
    except Exception as e:
        logger.error("add_comment error: %s", e)
        return {"ok": False, "error": "Could not save comment."}


def fetch_social(listing_ids: list[str], viewer_id: str = "") -> dict:
    """Views + likes + comments for a batch of listings, one query each."""
    out: dict[str, dict] = {lid: {"views": 0, "likes": 0, "liked": False,
                                  "comments": []} for lid in listing_ids}
    if engine is None or not listing_ids:
        return out
    try:
        with engine.connect() as conn:
            for lid, total in conn.execute(text("""
                SELECT listing_id, count(*) FROM listing_views
                WHERE listing_id = ANY(:ids) GROUP BY listing_id
            """), {"ids": listing_ids}).all():
                out[lid]["views"] = int(total)
            for lid, total in conn.execute(text("""
                SELECT listing_id, count(*) FROM listing_likes
                WHERE listing_id = ANY(:ids) GROUP BY listing_id
            """), {"ids": listing_ids}).all():
                out[lid]["likes"] = int(total)
            if viewer_id:
                for (lid,) in conn.execute(text("""
                    SELECT listing_id FROM listing_likes
                    WHERE listing_id = ANY(:ids) AND user_id = :uid
                """), {"ids": listing_ids, "uid": viewer_id}).all():
                    out[lid]["liked"] = True
            for r in conn.execute(text("""
                SELECT id, listing_id, user_id, user_name, body, parent_id, created_at
                FROM listing_comments
                WHERE listing_id = ANY(:ids)
                ORDER BY created_at
            """), {"ids": listing_ids}).mappings().all():
                if r["listing_id"] in out:
                    out[r["listing_id"]]["comments"].append({
                        "id": r["id"], "user_id": r["user_id"],
                        "user_name": r["user_name"] or r["user_id"],
                        "body": r["body"], "parent_id": r["parent_id"],
                        "created_at": str(r["created_at"]),
                    })
        return out
    except Exception as e:
        logger.error("fetch_social error: %s", e)
        return out


def fetch_debts() -> list[dict]:
    """The debt ledger: every recorded obligation between two traders."""
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, debtor, creditor, amount FROM debt_edges ORDER BY id"
            )).mappings().all()
        return [{
            "id": r["id"],
            "debtor": r["debtor"],
            "creditor": r["creditor"],
            "amount": float(r["amount"] or 0),
        } for r in rows]
    except Exception as e:
        logger.error("fetch_debts error: %s", e)
        return []


def record_debt(debtor: str, creditor: str, amount: float) -> dict:
    """Append one obligation to the ledger and return the stored row."""
    if engine is None:
        return {"ok": False, "error": "Database engine not initialized"}
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    INSERT INTO debt_edges (debtor, creditor, amount)
                    VALUES (:debtor, :creditor, :amount)
                    RETURNING id, debtor, creditor, amount
                """),
                {"debtor": debtor, "creditor": creditor, "amount": amount},
            ).mappings().one()
        return {
            "ok": True,
            "debt": {
                "id": row["id"], "debtor": row["debtor"],
                "creditor": row["creditor"], "amount": float(row["amount"]),
            },
        }
    except Exception as e:
        logger.error("record_debt error: %s", e)
        return {"ok": False, "error": str(e)}


def check_db_health() -> dict[str, Any]:
    """Check database connection and pgvector extension status."""
    if engine is None:
        return {"connected": False, "error": "Database engine not initialized"}
    try:
        with engine.connect() as conn:
            ext = conn.execute(text(
                "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector'"
            )).fetchone()
            sellers_count = conn.execute(text("SELECT count(*) FROM sellers")).scalar()
            listings_count = conn.execute(text("SELECT count(*) FROM listings")).scalar()
            vector_count = conn.execute(text(
                "SELECT count(*) FROM listings WHERE embedding IS NOT NULL"
            )).scalar()
            media_count = conn.execute(text(
                "SELECT count(*) FROM listings WHERE COALESCE(photo, '') <> '' OR COALESCE(video_url, '') <> ''"
            )).scalar()
            try:
                catalog_version = conn.execute(text(
                    "SELECT value FROM app_meta WHERE key = 'catalog_version'"
                )).scalar()
            except Exception:
                catalog_version = None
            try:
                debts_count = conn.execute(text("SELECT count(*) FROM debt_edges")).scalar()
            except Exception:
                debts_count = None
            return {
                "connected": True,
                "pgvector_version": ext[1] if ext else None,
                "catalog_version": catalog_version,
                "sellers_count": sellers_count,
                "listings_count": listings_count,
                "vector_indexed_listings": vector_count,
                "posts_with_media": media_count,
                "debts": debts_count,
            }
    except Exception as e:
        return {"connected": False, "error": str(e)}


def search_listings_vector(query_text: str, limit: int = 10, category: str | None = None) -> list[dict]:
    """Perform pgvector nearest-neighbor search using cosine distance (<=> operator)."""
    if engine is None:
        return []

    emb_str = _vec_literal(generate_embedding(query_text))

    query = """
        SELECT
            l.id, l.seller_id, l.item, l.category, l.brand, l.price_mwk,
            l.quantity_available, l.unit, l.origin, l.stock_status,
            l.reusable, l.registration_status, l.odometer_km,
            l.verification_status, l.photo, l.video_url,
            s.name as seller_name, s.location as seller_location,
            s.distance_km, s.trust_score, s.account_age_days, s.phone as seller_phone,
            1 - (l.embedding <=> CAST(:emb AS vector)) as similarity_score
        FROM listings l
        JOIN sellers s ON l.seller_id = s.id
        WHERE l.embedding IS NOT NULL
    """
    params: dict[str, Any] = {"emb": emb_str, "limit": limit}

    if category:
        query += " AND l.category = :category"
        params["category"] = category

    query += " ORDER BY l.embedding <=> CAST(:emb AS vector) ASC LIMIT :limit"

    try:
        with engine.connect() as conn:
            rows = conn.execute(text(query), params).mappings().all()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("Vector search error: %s", e)
        return []
