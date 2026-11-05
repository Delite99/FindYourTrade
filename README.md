# FindYourTrade 🇲🇼 — Trade local. Trust first. Spend less forex.

AI trade assistant for Malawi (Meta Global AI Hackathon, Indie track).
Text/voice need → nearby verified sellers → Forex Footprint ranking → Show Me video
verification → fraud-check chat → vehicle compare → debt netting → **Discover
recommender (legitimate products only)**.

## Quickstart

```bash
# backend (needs Python 3.11+)
cd backend
pip install -r requirements.txt
copy ..\.env.example ..\.env       # then paste your META_API_KEY
uvicorn app.main:app --port 8000   # → http://localhost:8000/api/health

# frontend (needs Node 18+) — new terminal
cd frontend
npm install
npm run dev                        # → http://localhost:5173
```

The Vite dev server proxies `/api` to `http://localhost:9000` (docker) by default.
Running uvicorn locally instead? Start it with `VITE_API_TARGET=http://localhost:8000 npm run dev`.

Without `META_API_KEY` the app runs in **transparent mock mode**: every response
carries `is_mock: true` and the UI shows a “Mock AI” badge, so nothing pretends to be real.

### Where the data lives

Traders, their posts and the debt ledger are stored in **PostgreSQL + pgvector**, not
in application code. `db/init.sql` creates `sellers`, `listings` (including each post's
`photo` and `video_url` plus a 1536-dim `embedding` with an HNSW cosine index) and
`debt_edges` (`debtor`, `creditor`, `amount`).

On startup the backend calls `db.ensure_schema()` → `db.seed_catalog()` → `catalog.refresh()`:

- `ensure_schema()` creates the extension/tables/indices and adds the media columns
  to an older volume if they are missing.
- `seed_catalog()` upserts the bootstrap fixture in `app/seed_data.py` into Postgres
  (`ON CONFLICT DO UPDATE`), so it is only a first-run fixture — never read at request time.
- `catalog.py` is the read layer: it pulls sellers and listings from Postgres into a
  15-second in-process snapshot, so edits made in the database appear without a restart.

```bash
# inspect / re-seed the catalogue
curl localhost:9000/api/db/status        # counts, pgvector version, catalog snapshot
curl -X POST localhost:9000/api/db/seed  # re-upsert the fixture, then reload the snapshot
```

Adding a trader, a post or a debt is a plain SQL `INSERT` into `sellers` / `listings` /
`debt_edges`; the feed and every endpoint pick it up within the cache TTL.

### Product dataset and photo integrity (live vision + reinforcement learning)

`backend/app/dataset/` holds **51 labelled products across 6 verticals** — vehicles,
farming, food, parts, building and electronics — each with its own photo and a 64-bit
perceptual hash. Photos come from the product's Wikipedia article, so the label is true
by construction: the picture of the "Toyota Hilux" page *is* a Hilux.

Rebuild or extend it with:

```bash
cd backend
python scripts/build_dataset.py            # fetch missing products, hash, write products.json
python scripts/build_dataset.py --refresh  # re-download everything
```

Two rules are enforced on every post — and **both now come from live vision**:

1. **One photo, one product.** Photos are compared by dHash with a Hamming threshold of 6
   bits, so a resized, re-compressed or lightly cropped copy is still recognised. Reusing
   a picture for a different product is flagged as fraud; reusing it for the *same* product
   is a warning.
2. **Live vision judges the label.** The raw image bytes go to Muse Spark vision
   (`meta_client.identify_product`, 1500-token budget — contributor models truncate
   structured image answers at 500), which looks at the pixels and names the brand/model
   it sees, with a confidence. The claim is judged against what vision *saw*, not a stored
   label. A Hilux photo posted as a Tesla returns `mismatch` and is dropped from search
   and the feed. Low-confidence vision (below 0.5) warns instead of convicting; photos
   nobody has identified need a Show Me video.

Verdicts are cached in `vision_labels` keyed by photo fingerprint (one model call per
unique photo, ever) and the startup scan is local-only — vision upgrades happen on demand.

**Reinforcement learning:** every vision verdict can be confirmed (+1 reward) or disputed
(−1, optionally with the correction) via `POST /api/integrity/feedback`. A disputed
verdict is re-examined; a human correction becomes ground truth and overrides vision
immediately. `POST /api/integrity/scan-vision` runs vision over unscanned or disputed
photos. The Integrity tab shows the vision source of every flag (live / cache / human /
dataset / unverified), the running reward score, and confirm/dispute buttons.
Every `vision_source` is reported honestly — the platform never pretends to know.

### Trader Studio, grounded assistant, and brand knowledge

**Open browsing, verified posting.** Anyone can browse the dashboard, search and chat without
logging in. Posting requires a trader account: name + phone + PIN at **Trader Studio**
(`POST /api/trader/signup`, `/api/trader/login`, token in `Authorization: Bearer`).
Step 1 is a real photo upload (`POST /api/trader/photo-verify`) — live vision must identify
actual goods before the account can post. Step 2 (`POST /api/trader/post`) runs the full
integrity gate; fraud is stored but held out of the dashboard.

**Photo-to-listing magic** (`POST /api/trader/describe-photo`): upload any photo and vision
pre-fills brand/model/category, suggests a fair price (live-market median), and runs the
fraud pre-check before the trader types a word.

**Studio magic, never fake:** `/api/trader/regenerate-photo` builds a clean studio trade card
with Muse Image from the *vision description of the verified goods* (never the seller's words,
never stock). `/api/trader/promo-video` directs a 30-second promo with Muse Wonder (hook +
voiceover + shot list); the trader records the video, the AI directs it.

**Assistant that cannot hallucinate.** `/api/chat` is fenced: hard refusal on weapons, drugs,
violence and theft (returns `risk_flags: ["refused:..."]`); uncategorized requests
("helicopter") get an honest "nobody has posted it yet" plus the real category shelf;
the live model only ever sees `REAL LIVE SEARCH RESULTS` with an explicit "recommending
anything else is lying" instruction; fraud/detail answers read straight from the catalogue.

**Brand knowledge from the internet** (`backend/scripts/learn_brands.py`): learns which brand
each model belongs to from Wikipedia + Wikidata into `brand_facts` (28 facts, e.g. Hilux →
Toyota 1.0, Ridgeline → Honda 1.0). The scan flags records that contradict public knowledge
("Honda Hilux") as `brand_conflict` fraud — with a parts-maker guard so "Denso" on a Hilux
alternator stays clean.

### Rebuilding the bundle when running in Docker

`frontend/dist` is bind-mounted into the nginx container, and on Windows nginx
holds open handles on those files — `vite build` fails while emptying the folder.
Stop the container first, then rebuild and start it again:

```bash
docker compose stop frontend
cd frontend && npm run build
docker compose start frontend
```

Backend code needs no rebuild: `backend/app` is bind-mounted, so a container
restart is enough (`docker compose restart backend`).

## Interface

The UI follows the tesla.com design language rather than a dashboard template:

- **One accent.** Ink `#171a20`, paper `#fff`, hairlines `#d0d1d2`, a single blue
  `#3e6ae1` for links and the primary CTA. Colour appears only in data (forex
  score, verification state) so it always means something.
- **Flat surfaces.** No drop shadows, no gradients on chrome, 4 px radii. Depth
  comes from spacing and 1 px rules.
- **Type-led.** Inter Tight display sizes with tight tracking, uppercase
  micro-labels at 0.14–0.24 em. No emoji in the interface; category art is a
  thin-line glyph set.
- **Full-bleed hero, fixed nav.** The nav is transparent over the Discover hero
  and turns to frosted white on scroll or on any other tab. Under 1000 px it
  collapses to a full-screen drawer with large left-aligned items.
- **Product grid.** Image-first tiles, name and price on one baseline, spec
  chips, a 2 px forex rule instead of a coloured box, actions pinned to the card
  bottom so a row of cards lines up.

Tab state is addressable (`?tab=chat`, `?tab=netting`, …) so a section can be
linked or reloaded directly.

## Meta model integration

All five model slots are wired through one hardened client (`backend/app/meta_client.py`),
and every slot uses the single Meta endpoint `META_API_BASE=https://api.meta.ai/v1`.

| Slot | Model (env) | Endpoint | Used by |
|---|---|---|---|
| `spark` | `MODEL_SPARK` (`muse-spark-1.3-contributor`) | `META_API_BASE` (OpenAI-compatible) | F1 parsing, F5 fraud, F6 compare, chat, F4 verdict, bulk ranking |
| `wonder` | `MODEL_WONDER` (`muse-wonder-1.0`) | `META_API_BASE` | F7 netting explanation |
| `sam` | `MODEL_SAM` (`sam-3.1`) | `META_API_BASE` | F4 item counting over frames |
| `transcribe` | `MODEL_TRANSCRIBE` (`muse-voice-transcribe-1.0`) | `META_API_BASE` | voice notes + code word |
| `image` | `MODEL_IMAGE` (`muse-image-1.0`) | `META_API_BASE` | P2 trade cards |

Only `META_API_KEY` is required — one key covers every slot. Contributor mode is
selected purely by the model id (`muse-spark-1.3-contributor`): same endpoint,
discounted price, and Meta may use that content to train future models. Each slot
is independent: configure only the keys you have; everything else stays labelled mock.

Muse Spark 1.3 reasons before answering, so the budget must be large enough for
`reasoning_tokens` to finish or `content` comes back empty — the client sends a
generous `max_tokens` by default and `/api/match` validates the ranking reply by
extracting a verdict sentence naming the listing id, falling back to the
deterministic note otherwise.

What the client handles for you:

- **Retries** with exponential backoff on 408/409/425/429/5xx and network errors (`META_MAX_RETRIES`)
- **Timeouts** per request (`META_TIMEOUT`)
- **JSON mode** with `response_format`, plus a balanced-brace parser and a plain-text retry if the deployment rejects it
- **TTL cache** so repeated demo runs cost nothing (`META_CACHE_TTL`)
- **Spend guard** — hard cap on live calls per process (`META_MAX_CALLS`), returns a labelled error past the cap
- **Graceful degradation** — any model failure returns `is_mock` + an `error` block instead of a 500
- **Telemetry** — model, op, latency, tokens, cache hit for every call

### Show Me pipeline (real, not simulated)

```
upload → OpenCV samples 6 frames → Muse Spark (vision) counts items
       → ffmpeg extracts 16 kHz mono audio → Muse Voice Transcribe
       → Muse Spark compares count + code word + listing → Match / Partial / Mismatch
```

Frame sampling uses `opencv-python-headless`; audio extraction uses **ffmpeg**.
SAM 3.1 is *not* in this path — it is segmentation on `/v1/responses` and returns
boxes/masks rather than a count, so Muse Spark's image understanding does the
counting. `GET /api/models/status` reports which of opencv/ffmpeg are present.

### Configuration

Everything is read from `.env`; nothing is hardcoded in the code.

| Variable | Default | Purpose |
|---|---|---|
| `META_API_BASE` | `https://api.meta.ai/v1` | Model API base URL |
| `META_API_KEY` | — | One key covers every model slot |
| `MODEL_SPARK` / `MODEL_WONDER` / `MODEL_TRANSCRIBE` / `MODEL_SAM` / `MODEL_IMAGE` | `muse-spark-1.3-contributor` / `muse-wonder-1.0` / `muse-voice-transcribe-1.0` / `sam-3.1` / `muse-image-1.0` | Model ids |
| `META_TIMEOUT` / `META_MAX_RETRIES` | `45` / `3` | Per-request timeout, retry budget |
| `META_CACHE_TTL` / `META_MAX_CALLS` | `900` / `400` | Response cache, spend guard |
| `VISION_MAX_FRAMES` / `FRAME_MAX_EDGE` | `6` / `768` | Frame sampling |
| `ASR_MODE` | `ENDPOINTING` | `ENDPOINTING`, `DIARIZATION` (speaker labels), `PUSH_TO_TALK` |
| `ASR_LANGUAGE_BIAS` | `English` | Comma-separated language hints (25 supported) |
| `ASR_KEYWORDS` | — | Comma-separated domain vocabulary to bias recognition |
| `CORS_ORIGINS` | `*` | Comma-separated allow-list; set explicitly before going public |
| `VITE_API_BASE` | *(empty)* | Frontend only — empty means same origin via nginx |

Scoring weights live as named constants in `matching.py` (`CATEGORY_BONUS`,
`TRUST_WEIGHT`, …) and `recommender.py` (`LEGIT_BASE`, `AFFINITY_WEIGHT`, …),
so ranking can be tuned in one place instead of hunting inline numbers.

### Verifying the integration

```bash
# what is configured + recent call telemetry
curl http://localhost:8000/api/models/status

# ping every model slot with a minimal request
curl -X POST http://localhost:8000/api/models/selftest

# every route, end to end (26 checks, exits non-zero on failure)
python backend/scripts/test_all_endpoints.py

# Muse Voice Transcribe with real speech — pass your own WAV or let it fetch a sample
python backend/scripts/test_voice.py
python backend/scripts/test_voice.py path/to/clip.wav
```

`test_voice.py` prints the transcript plus each detected turn (with speaker label
when `ASR_MODE=DIARIZATION`) and fails loudly if the response is mock, errored, or
empty — so a silently-broken voice path can't pass as working.

Or open the **⚙️ AI Engine** tab in the UI: per-slot pass/fail with latency, live
call count, cache hits, spend-cap usage, and pipeline readiness. A slot failing
with 400/404 usually means that model id isn't in your account catalog — override
it with the matching `MODEL_*` variable.

## API cheat sheet

| Endpoint | PRD | Example |
|---|---|---|
| `POST /api/parse` | F1 | `{"text":"I need 4 bags of fertilizer near Lilongwe"}` |
| `POST /api/transcribe` | F1 | multipart voice file → transcript |
| `POST /api/match` | F2+F3 | text → parsed request + ranked listings w/ Forex |
| `GET /api/listings` | F2 | every post in Postgres, with seller, photo and video |
| `GET /api/dataset/stats` / `GET /api/dataset/products` | Integrity | dataset size per vertical, browsable products |
| `GET /api/integrity/flags` | Integrity | posts with a duplicate-photo or label-mismatch flag |
| `POST /api/integrity/check` | Integrity | live vision over the photo + `item`/`brand`/`model` → verdict |
| `POST /api/integrity/scan-vision` | Integrity | identify unscanned/disputed photos (bounded, cached) |
| `POST /api/integrity/feedback` | Integrity | confirm (+1) or dispute (−1, optional correction) a vision verdict |
| `POST /api/integrity/model/train` | Integrity | fit the PyTorch fraud-risk network on current ledger verdicts |
| `POST /api/integrity/model/score` | Integrity | risk score for one post from the trained network |
| `GET /api/integrity/queue` | Integrity | rule-flagged posts + unscanned posts triaged by model risk |
| `POST /api/trader/signup` / `/login` / `/me` | Trader | phone + PIN trader accounts, session lookup |
| `POST /api/trader/photo-verify` | Trader | real-photo upload → live-vision verification gate |
| `POST /api/trader/describe-photo` | Trader | photo-to-listing: identify + pre-fill + fair price + pre-check |
| `POST /api/trader/post` | Trader | verified-trader posting behind the full integrity gate |
| `POST /api/trader/regenerate-photo` | Trader | Muse Image studio card from verified goods |
| `POST /api/trader/promo-video` | Trader | Muse Wonder 30-second promo plan |
| `POST /api/showme/codeword` | F4 | `{"listing_id":"l01"}` → `mango-27` |
| `POST /api/showme/verify?listing_id=l01&claimed_count=40&code_word_said=mango-27` | F4 | Match / Partial / Mismatch (+ `trace`, `pipeline_notes`) |
| `POST /api/fraud-check` | F5 | `{"messages":[…],"listing_id":"l05"}` |
| `POST /api/vehicles/compare` | F6 | `{"listing_ids":["l10","l11","l12"]}` |
| `GET /api/netting/demo` / `POST /api/netting/propose` | F7 | cycle cancellation → 480 → 0 |
| `GET /api/netting/debts` / `POST /api/netting/debts` | F7 | the stored ledger; `{"debtor","creditor","amount"}` |
| `GET /api/recommendations?user_id=chikondi` | Discover | personalized feed, verified/Show Me-matched only |
| `POST /api/profile` | Discover | `{"user_id","interests":["fertilizer","seed"],"location":"Lilongwe"}` |
| `POST /api/events` | Discover | `{"user_id","listing_id":"l13","event":"like"}` (view/like/verify/inquiry/skip) |
| `GET /api/recommendations/similar/l01` | Discover | legitimate alternatives to a listing |
| `GET /api/models/status` | — | config, telemetry, media capabilities |
| `POST /api/models/selftest` | — | per-model health check |
| `GET /api/dashboard` | P2 | verified sellers, imports avoided |

## Demo script (3 min)

- **0:00–0:20** Problem: forex shortage, goods nearby but hard to trust.
- **0:20–1:00** Voice note “I need fertilizer” → ranked sellers with Forex Footprint (top: l01, 15/100 estimate).
- **1:00–1:50** Show Me: get code word → wrong count + wrong code → **Mismatch**; exact count + exact code → **Match**.
- **1:50–2:20** “Is l05 legit?” → flags new account + huge offer; compare Hilux vs Navara vs fresh import.
- **2:20–2:45** Debt netting: two rings cancel, cash movement 480 → **0**, parties confirm.
- **2:45–3:00** Impact: share of in-country requests, verified sellers, estimated imports avoided.

## Disclaimers (shown in UI + demo)

- Forex Footprint is a **transparent estimate**, not an economic measurement.
- Show Me proves goods + seller were present at recording time — **not legal ownership**.
- High-value trades end with an in-person handoff; confirm vehicle ownership with the official authority.
- Payment stays normal: kwacha, mobile money, cash, or part goods (mobile money simulated).
