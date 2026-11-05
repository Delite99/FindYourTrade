-- ============================================================
-- FindYourTrade — PostgreSQL + pgvector initialisation script
-- Runs automatically when the container starts for the first time
-- ============================================================

-- Enable the pgvector extension (provides the `vector` type + index ops)
CREATE EXTENSION IF NOT EXISTS vector;

-- ── Sellers ─────────────────────────────────────────────────────────────────
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
);

-- ── Listings ─────────────────────────────────────────────────────────────────
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
    -- Per-post media: every post is a photo or a video the trader uploaded
    photo                 TEXT        NOT NULL DEFAULT '',
    video_url             TEXT        NOT NULL DEFAULT '',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- pgvector column: 1536-dim embedding (OpenAI ada-002 / Muse-compatible)
    -- Increase or decrease the dimension to match your actual embedding model
    embedding             VECTOR(1536)
);

-- Older volumes predate per-post media; keep the columns in sync on re-init.
ALTER TABLE listings ADD COLUMN IF NOT EXISTS photo     TEXT NOT NULL DEFAULT '';
ALTER TABLE listings ADD COLUMN IF NOT EXISTS video_url TEXT NOT NULL DEFAULT '';

-- HNSW index for fast approximate nearest-neighbour search on embeddings
CREATE INDEX IF NOT EXISTS listings_embedding_hnsw
    ON listings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Standard B-tree indices for common filter queries
CREATE INDEX IF NOT EXISTS listings_category_idx   ON listings (category);
CREATE INDEX IF NOT EXISTS listings_seller_idx     ON listings (seller_id);
CREATE INDEX IF NOT EXISTS listings_origin_idx     ON listings (origin);
CREATE INDEX IF NOT EXISTS listings_stock_idx      ON listings (stock_status);

-- ── Show-Me verification records ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS showme_verifications (
    id            SERIAL      PRIMARY KEY,
    listing_id    TEXT        NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    code_word     TEXT        NOT NULL,
    issued_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    verdict       TEXT,                         -- 'Match' | 'Partial match' | 'Mismatch'
    counted_items INT,
    code_word_heard BOOLEAN,
    transcript    TEXT,
    is_mock       BOOLEAN     NOT NULL DEFAULT TRUE,
    verified_at   TIMESTAMPTZ
);

-- ── Debt-netting edges ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS debt_edges (
    id          SERIAL  PRIMARY KEY,
    debtor      TEXT    NOT NULL,
    creditor    TEXT    NOT NULL,
    amount      NUMERIC NOT NULL CHECK (amount > 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS debt_edges_debtor_idx   ON debt_edges (debtor);
CREATE INDEX IF NOT EXISTS debt_edges_creditor_idx ON debt_edges (creditor);

-- ── Forex footprint cache ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS forex_footprints (
    id          SERIAL      PRIMARY KEY,
    listing_id  TEXT        NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    score       INT         NOT NULL,
    reason      TEXT        NOT NULL,
    is_estimate BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Helper: auto-update updated_at on row changes ────────────────────────────
CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER sellers_updated_at
    BEFORE UPDATE ON sellers
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE OR REPLACE TRIGGER listings_updated_at
    BEFORE UPDATE ON listings
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
