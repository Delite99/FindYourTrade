"""Meta Model API client — live integration layer.

All reasoning, vision, voice and image calls go through the Meta Model API's
OpenAI-compatible surface (https://api.meta.ai/v1). This module owns:

  • transport      — retries with exponential backoff, timeouts, error surfacing
  • structured out — JSON-mode helpers with robust parsing + schema fallback
  • caching        — TTL cache so repeat demo runs cost nothing
  • spend guard    — hard call cap per process (PRD risk mitigation)
  • telemetry      — per-call model/latency/tokens, exposed for the UI

Every public helper returns a dict containing ``is_mock`` so callers and the UI
can be explicit about whether a real model answered.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any

import httpx

from . import config

# ─────────────────────────────────────────────────────────────────────────────
# Errors & telemetry
# ─────────────────────────────────────────────────────────────────────────────


class MetaError(RuntimeError):
    """Raised when a model call fails in a way the caller should know about."""

    def __init__(self, message: str, *, model: str = "", status: int = 0,
                 detail: str = "", retryable: bool = False, code: str = ""):
        super().__init__(message)
        self.model = model
        self.status = status
        self.detail = detail
        self.retryable = retryable
        self.code = code

    def as_dict(self) -> dict:
        return {"error": str(self), "model": self.model, "status": self.status,
                "detail": self.detail, "code": self.code}


@dataclass
class CallRecord:
    ts: float
    model: str
    op: str
    ok: bool
    latency_ms: int
    cached: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


_TELEMETRY: deque[CallRecord] = deque(maxlen=200)
_CALL_COUNT = 0
_TOKEN_TOTAL = 0


def _record(model: str, op: str, ok: bool, started: float, *,
            cached: bool = False, usage: dict | None = None, error: str = "") -> None:
    global _TOKEN_TOTAL
    usage = usage or {}
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    _TOKEN_TOTAL += pt + ct
    _TELEMETRY.append(CallRecord(
        ts=time.time(), model=model, op=op, ok=ok,
        latency_ms=int((time.perf_counter() - started) * 1000),
        cached=cached, prompt_tokens=pt, completion_tokens=ct, error=error[:300],
    ))


def telemetry(limit: int = 40) -> dict:
    records = list(_TELEMETRY)[-limit:]
    live = [r for r in records if not r.cached]
    ok = [r for r in records if r.ok]
    return {
        "calls": len(records),
        "live_calls": len(live),
        "cached_hits": sum(1 for r in records if r.cached),
        "success_rate": round(len(ok) / len(records), 3) if records else 0.0,
        "avg_latency_ms": int(sum(r.latency_ms for r in live) / len(live)) if live else 0,
        "tokens_total": _TOKEN_TOTAL,
        "spend_cap_calls": config.MAX_CALLS_PER_SESSION,
        "calls_used": _CALL_COUNT,
        "recent": [r.as_dict() for r in reversed(records[-15:])],
    }


def reset_telemetry() -> None:
    global _CALL_COUNT, _TOKEN_TOTAL
    _TELEMETRY.clear()
    _CALL_COUNT = 0
    _TOKEN_TOTAL = 0


# ─────────────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────────────


class _TTLCache:
    def __init__(self, ttl: int, max_items: int = 256):
        self.ttl = ttl
        self.max_items = max_items
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str):
        hit = self._store.get(key)
        if not hit:
            return None
        ts, value = hit
        if time.time() - ts > self.ttl:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        if len(self._store) >= self.max_items:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)
        self._store[key] = (time.time(), value)

    def clear(self) -> None:
        self._store.clear()

    def size(self) -> int:
        return len(self._store)


_CACHE = _TTLCache(config.CACHE_TTL_SECONDS)


def _cache_key(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ─────────────────────────────────────────────────────────────────────────────
# Compatibility shims (existing callers use these names)
# ─────────────────────────────────────────────────────────────────────────────


def has_meta_key() -> bool:
    return config.is_live()


def mode_label() -> str:
    return config.mode_label()


MODEL_SPARK = config.MODEL_SPARK
MODEL_WONDER = config.MODEL_WONDER
MODEL_TRANSCRIBE = config.MODEL_TRANSCRIBE
MODEL_SAM = config.MODEL_SAM
MODEL_IMAGE = config.MODEL_IMAGE

CODE_WORDS = ["mango", "baobab", "chiselu", "nyanja", "mbewa", "chimanga", "nthochi", "mkate"]


def new_code_word() -> str:
    return f"{random.choice(CODE_WORDS)}-{random.randint(10, 99)}"


# ─────────────────────────────────────────────────────────────────────────────
# Transport
# ─────────────────────────────────────────────────────────────────────────────


def _guard() -> None:
    """Spend guard — refuse new live calls past the configured cap."""
    global _CALL_COUNT
    if _CALL_COUNT >= config.MAX_CALLS_PER_SESSION:
        raise MetaError(
            f"Spend cap reached ({config.MAX_CALLS_PER_SESSION} model calls). "
            "Raise META_MAX_CALLS or restart to reset.",
            code="spend_cap",
        )


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.META_API_KEY}"}


async def _request(path: str, *, json_body: dict | None = None,
                   files: dict | None = None, data: dict | None = None,
                   model: str = "", op: str = "", timeout: float | None = None) -> dict:
    """POST with retry/backoff. Returns parsed JSON. Raises MetaError on failure."""
    global _CALL_COUNT
    if not config.is_live():
        raise MetaError("No META_API_KEY configured", model=model, code="no_key")

    url = f"{config.META_API_BASE.rstrip('/')}/{path.lstrip('/')}"
    timeout = timeout or config.REQUEST_TIMEOUT
    last: MetaError | None = None

    for attempt in range(1, config.MAX_RETRIES + 1):
        _guard()
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=json_body, files=files,
                                         data=data, headers=_headers())
            if resp.status_code < 400:
                payload = resp.json()
                _CALL_COUNT += 1
                _record(model, op, True, started, usage=payload.get("usage"))
                return payload

            detail = resp.text[:500]
            retryable = resp.status_code in (408, 409, 425, 429) or resp.status_code >= 500
            last = MetaError(
                f"{model or op} returned HTTP {resp.status_code}",
                model=model, status=resp.status_code, detail=detail, retryable=retryable,
            )
            if not retryable:
                _record(model, op, False, started, error=last.detail)
                raise last
        except MetaError:
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = MetaError(f"{model or op} network error: {exc}", model=model, retryable=True)
        except Exception as exc:  # malformed payload etc.
            last = MetaError(f"{model or op} failed: {exc}", model=model)
            _record(model, op, False, started, error=str(exc))
            raise last

        if attempt < config.MAX_RETRIES:
            await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

    _record(model, op, False, time.perf_counter(), error=str(last) if last else "unknown")
    raise last or MetaError(f"{model or op} failed", model=model)


def _mock(model: str, op: str, note: str = "") -> dict:
    return {"text": "", "is_mock": True, "model": model, "op": op,
            "note": note or "mock mode — set META_API_KEY for live model calls"}


def _extract_json(text: str) -> dict | None:
    """Pull the first balanced JSON object out of a model response."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip().strip("`")
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        for i, ch in enumerate(cleaned[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(cleaned[start:i + 1])
                        return obj if isinstance(obj, dict) else None
                    except json.JSONDecodeError:
                        break
        start = cleaned.find("{", start + 1)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Chat / reasoning
# ─────────────────────────────────────────────────────────────────────────────


async def chat(model: str, messages: list[dict], *, max_tokens: int = 700,
               temperature: float = 0.2, json_mode: bool = False,
               op: str = "chat", cache: bool = True) -> dict:
    """Core chat call. Returns {text, data?, is_mock, model, latency_ms, cached}.

    The cache is namespaced per `op`: a promo_plan must never be served a
    cached promo_plan for a *different* trader's brief, even byte-identical
    prompts from creative endpoints bypass the shared cache below.
    """
    key = _cache_key(op, model, messages, max_tokens, temperature, json_mode)
    if cache:
        hit = _CACHE.get(key)
        if hit is not None:
            return {**hit, "cached": True}

    if not config.is_live():
        return _mock(model, op)

    body: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    started = time.perf_counter()
    try:
        payload = await _request("chat/completions", json_body=body, model=model, op=op)
    except MetaError as exc:
        if json_mode and exc.status == 400:
            # Some deployments reject response_format — retry as plain text.
            body.pop("response_format", None)
            payload = await _request("chat/completions", json_body=body, model=model, op=op)
        else:
            raise

    try:
        choice = payload["choices"][0]["message"]
        text = choice.get("content") or ""
    except (KeyError, IndexError) as exc:
        raise MetaError(f"{model} returned an unexpected payload", model=model,
                        detail=str(exc)) from exc

    result = {
        "text": text.strip(),
        "is_mock": False,
        "model": model,
        "op": op,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "cached": False,
        # How the call ended. Callers treat empty text as a model failure, but
        # finish_reason "length" means OUR budget ended the call — the model
        # never got to speak. That distinction decides between "look closer"
        # (retry with headroom) and "unidentifiable".
        "finish_reason": choice.get("finish_reason"),
    }
    if json_mode:
        parsed = _extract_json(text)
        if parsed is not None:
            result["data"] = parsed
    if cache:
        _CACHE.set(key, result)
    return result


async def spark_chat(system: str, user: str, *, max_tokens: int = 700,
                     json_mode: bool = False) -> dict:
    return await chat(config.MODEL_SPARK,
                      [{"role": "system", "content": system},
                       {"role": "user", "content": user}],
                      max_tokens=max_tokens, json_mode=json_mode, op="spark")


async def wonder_orchestrate(prompt: str) -> dict:
    return await chat(config.MODEL_WONDER,
                      [{"role": "system", "content": "You orchestrate multi-party trade flows in plain language."},
                       {"role": "user", "content": prompt}], op="wonder")


async def spark_rank(prompt: str, *, max_tokens: int = 1024,
                     expect_id: str = "") -> dict:
    """One-line second opinion on a ranking, via Muse Spark on Meta Model API.

    The deterministic ranker stays the source of truth; this note is a short
    agree-or-correct endorsement starting with the listing id, e.g.
    'l01 — best pick because ...'. Contributor models reason heavily, hence
    the 1024-token default budget (reasoning tokens count against the limit).
    """
    res = await chat(config.MODEL_SPARK,
                     [{"role": "system", "content": "You rank trade listings quickly and fairly."},
                      {"role": "user", "content": prompt}],
                     max_tokens=max_tokens, op="spark_rank")
    text = (res.get("text") or "").strip()
    if expect_id and expect_id.lower() not in text.lower():
        # Fall back to the deterministic pick instead of showing junk.
        raise MetaError(f"ranker reply did not endorse {expect_id}",
                        model=config.MODEL_SPARK)
    return {**res, "provider": "meta", "op": "spark_rank"}


# ─────────────────────────────────────────────────────────────────────────────
# F1 — request understanding
# ─────────────────────────────────────────────────────────────────────────────

PARSE_SCHEMA_HINT = """Return ONLY JSON with these keys:
{"item_type": string, "category": one of [fertilizer, seed, maize, vehicle, parts, equipment, agrochem, general],
 "quantity": integer, "budget_mwk": number|null, "location": string, "payment_preference": string}
Malawi locations: Lilongwe, Blantyre, Mzuzu, Dedza, Zomba, Kasungu, Mangochi. Default location Lilongwe.
payment_preference: one of [mobile money, cash, kwacha, barter, goods, credit, any]."""


async def parse_trade_request(text: str) -> dict:
    """Muse Spark turns free text/voice transcript into structured fields."""
    try:
        result = await chat(
            config.MODEL_SPARK,
            [{"role": "system", "content": "You extract structured trade requests for a Malawian marketplace. " + PARSE_SCHEMA_HINT},
             {"role": "user", "content": text}],
            max_tokens=300, temperature=0.0, json_mode=True, op="parse",
        )
    except MetaError as exc:
        return {**_mock(config.MODEL_SPARK, "parse", str(exc)), "data": None,
                "error": exc.as_dict()}
    if result.get("is_mock"):
        return {**result, "data": None}
    data = result.get("data") or {}
    cleaned: dict[str, Any] = {}
    if isinstance(data.get("item_type"), str):
        cleaned["item_type"] = data["item_type"].strip().lower()[:40]
    if isinstance(data.get("category"), str):
        cat = data["category"].strip().lower()
        if cat in {"fertilizer", "seed", "maize", "vehicle", "parts", "equipment", "agrochem", "general"}:
            cleaned["category"] = cat
    for key in ("quantity",):
        try:
            cleaned[key] = max(1, int(data.get(key)))
        except (TypeError, ValueError):
            pass
    if data.get("budget_mwk") is not None:
        try:
            cleaned["budget_mwk"] = float(data["budget_mwk"])
        except (TypeError, ValueError):
            pass
    if isinstance(data.get("location"), str) and data["location"].strip():
        cleaned["location"] = data["location"].strip().title()
    if isinstance(data.get("payment_preference"), str) and data["payment_preference"].strip():
        cleaned["payment_preference"] = data["payment_preference"].strip().lower()
    return {**result, "data": cleaned or None}


# ─────────────────────────────────────────────────────────────────────────────
# F4 — Show Me verification
# ─────────────────────────────────────────────────────────────────────────────


def _wav_info(audio_bytes: bytes) -> dict | None:
    """Parse a RIFF/WAVE header. Returns None when this is not a WAV container.

    Muse Voice Transcribe accepts only mono, 16-bit integer PCM at 16 kHz or
    24 kHz, so the caller needs the real format before uploading rather than
    letting the API reject it with an opaque 400.
    """
    if len(audio_bytes) < 44 or audio_bytes[0:4] != b"RIFF" or audio_bytes[8:12] != b"WAVE":
        return None
    pos, fmt, data_len = 12, None, None
    while pos + 8 <= len(audio_bytes):
        chunk_id = audio_bytes[pos:pos + 4]
        size = int.from_bytes(audio_bytes[pos + 4:pos + 8], "little")
        body = pos + 8
        if chunk_id == b"fmt " and body + 16 <= len(audio_bytes):
            fmt = {
                "audio_format": int.from_bytes(audio_bytes[body:body + 2], "little"),
                "channels": int.from_bytes(audio_bytes[body + 2:body + 4], "little"),
                "sample_rate": int.from_bytes(audio_bytes[body + 4:body + 8], "little"),
                "bits": int.from_bytes(audio_bytes[body + 14:body + 16], "little"),
            }
        elif chunk_id == b"data":
            data_len = size
            break
        pos = body + size + (size & 1)
    if not fmt:
        return None
    return {**fmt, "data_bytes": data_len or 0}


async def transcribe_audio(audio_bytes: bytes, filename: str = "voice.wav") -> dict:
    """Muse Voice Transcribe via POST /v1/asr/transcribe.

    Meta's endpoint is not the OpenAI `/audio/transcriptions` shape: it takes a
    two-part multipart body (`request` JSON + `audio` file) and returns
    `transcript` / `turns` instead of `text`.
    """
    if not config.is_live():
        return {**_mock(config.MODEL_TRANSCRIBE, "transcribe"),
                "text": "", "provider": "mock"}

    info = _wav_info(audio_bytes)
    if not info:
        return {**_mock(config.MODEL_TRANSCRIBE, "transcribe", "not a WAV file"),
                "text": "", "provider": "unavailable",
                "error": {"error": "Audio must be mono 16-bit PCM WAV at 16 kHz or 24 kHz. "
                                   "Convert it first: ffmpeg -i input.m4a -ac 1 -ar 24000 "
                                   "-c:a pcm_s16le out.wav",
                          "code": "unsupported_audio"}}
    if info["audio_format"] not in (1,) or info["bits"] != 16 or info["channels"] != 1 \
            or info["sample_rate"] not in (16000, 24000):
        return {**_mock(config.MODEL_TRANSCRIBE, "transcribe", "unsupported WAV layout"),
                "text": "", "provider": "unavailable",
                "error": {"error": f"Unsupported WAV layout (format={info['audio_format']}, "
                                   f"{info['bits']}-bit, {info['channels']}ch, "
                                   f"{info['sample_rate']} Hz). Need 16-bit mono PCM at "
                                   "16 kHz or 24 kHz.",
                          "code": "unsupported_audio"}}

    started = time.perf_counter()
    try:
        settings = {
            "mode": config.ASR_MODE,
            "model": config.MODEL_TRANSCRIBE,
            "audioEncoding": "WAV",
            "partialMode": "CUMULATIVE",
        }
        if config.ASR_LANGUAGE_BIAS:
            settings["languageBias"] = config.ASR_LANGUAGE_BIAS
        if config.ASR_KEYWORDS:
            settings["keywords"] = config.ASR_KEYWORDS
        payload = await _request(
            "asr/transcribe",
            files={
                "request": (None, json.dumps(settings), "application/json"),
                "audio": (filename, audio_bytes, "audio/wav"),
            },
            model=config.MODEL_TRANSCRIBE, op="transcribe", timeout=120,
        )
        return {
            "text": (payload.get("transcript") or "").strip(),
            "turns": payload.get("turns") or [],
            "audio_duration_ms": payload.get("audioDurationMs"),
            "session_id": payload.get("sessionId"),
            "is_mock": False,
            "model": config.MODEL_TRANSCRIBE,
            "provider": "Meta Model API",
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    except MetaError as exc:
        return {**_mock(config.MODEL_TRANSCRIBE, "transcribe", str(exc)),
                "text": "", "provider": "unavailable", "error": exc.as_dict()}


async def count_items(frames: list[bytes], item_hint: str) -> dict:
    """Count goods across sampled video frames with Muse Spark vision."""
    if not config.is_live():
        return {**_mock(config.MODEL_SPARK, "count_items"), "count": None}
    if not frames:
        return {**_mock(config.MODEL_SPARK, "count_items", "no frames extracted"), "count": None}

    import base64
    content: list[dict] = [{
        "type": "text",
        "text": (f"These are {len(frames)} frames sampled from one video of goods for sale: {item_hint}. "
                 "Segment and count the distinct visible items in each frame. "
                 'Return ONLY JSON: {"per_frame": [ints], "best_count": int, "confidence": 0-1, '
                 '"notes": "short observation"}'),
    }]
    for b in frames[: config.VISION_MAX_FRAMES]:
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b).decode()}})

    try:
        # Counting needs image understanding + a text answer, which is Muse
        # Spark's job. SAM 3.1 is served only on /v1/responses and returns
        # boxes/masks rather than a count, so it cannot answer this prompt.
        result = await chat(config.MODEL_SPARK,
                            [{"role": "user", "content": content}],
                            max_tokens=400, temperature=0.0, json_mode=True, op="count_items")
    except MetaError as exc:
        return {**_mock(config.MODEL_SPARK, "count_items", str(exc)), "count": None,
                "error": exc.as_dict()}

    data = result.get("data") or {}
    count = None
    for candidate in (data.get("best_count"), max(data.get("per_frame") or [0] or [0])):
        try:
            count = int(candidate)
            break
        except (TypeError, ValueError):
            continue
    return {**result, "count": count,
            "per_frame": data.get("per_frame"),
            "confidence": data.get("confidence"),
            "notes": data.get("notes", "")}


async def identify_product(image_bytes: bytes, *, claim: dict | None = None,
                            candidates: list[dict] | None = None) -> dict:
    """Live vision: look at a product photo and say what it actually shows.

    Muse Spark receives the raw image (not a hash) and returns a structured
    identification — brand, model, category, vertical, confidence. When a seller
    claim and/or catalogue candidates are supplied the model also judges whether
    the picture matches the claim. This is the ground truth for the photo/label
    rule: the platform asks the model what the photo *is* instead of trusting a
    stored label.

    Returns {identification?, is_mock, model, ...}. `identification` is None in
    mock mode or when the response cannot be parsed.
    """
    if not config.is_live():
        return {**_mock(config.MODEL_SPARK, "identify"), "identification": None}
    if not image_bytes:
        return {**_mock(config.MODEL_SPARK, "identify", "no image bytes"),
                "identification": None}

    import base64
    b64 = base64.b64encode(image_bytes).decode()

    # Reasoning budget: this contributor model spends most of max_tokens on
    # hidden reasoning and only then emits the answer (verified: reasoning up
    # to ~1500 tokens while the visible JSON is ~70). A small budget ends the
    # call at finish_reason "length" with EMPTY content — which used to read as
    # "vision failed" on plain photos like a football. A generous headroom is
    # not spend: unused tokens are never billed, and _record() only counts
    # what the call actually used.
    lines = [
        "Look at this product photo and identify exactly what it shows.",
        'Return ONLY JSON: {"brand": string, "model": string, '
        '"category": one of vehicle equipment produce food parts building electronics appliance general, '
        '"vertical": one of vehicles farming food parts building electronics, '
        '"confidence": number 0-1, "features": [up to 4 short visible details], '
        '"notes": "one short sentence"}.',
        "Brand and model are the visible make, e.g. brand Toyota model Hilux. "
        "If you cannot tell the brand, return an empty brand string with low "
        "confidence — never guess a brand.",
    ]
    if claim:
        lines.append(
            "The seller claims this photo shows: "
            f"item={claim.get('item', '')!r} brand={claim.get('brand', '')!r} "
            f"model={claim.get('model', '')!r}. "
            'Add "matches_claim": true/false and a "claim_reason" string explaining why.'
        )
    if candidates:
        short = "; ".join(
            f"{c.get('brand', '')} {c.get('model', '')} ({c.get('title', '')})"
            for c in candidates[:40]
        )
        lines.append(
            "Known catalogue products (pick the closest when it genuinely matches, "
            "otherwise identify freely): " + short
        )

    content: list[dict] = [{"type": "text", "text": "\n".join(lines)},
                            {"type": "image_url",
                             "image_url": {"url": "data:image/jpeg;base64," + b64}}]
    try:
        # 8000-token headroom: ~1500 hidden reasoning + ~70 visible JSON.
        # Anything near the model's own thinking length risks a "length"
        # finish with empty content.
        result = await chat(config.MODEL_SPARK,
                            [{"role": "user", "content": content}],
                            max_tokens=8000, temperature=0.0,
                            json_mode=True, op="identify")
    except MetaError as exc:
        return {**_mock(config.MODEL_SPARK, "identify", str(exc)),
                "identification": None, "error": exc.as_dict()}

    if not (result.get("text") or "").strip() and result.get("finish_reason") == "length":
        # The budget ended the call before the model emitted anything: its
        # hidden reasoning ate the whole allowance. Retry once with a larger
        # budget instead of reporting "vision failed" — the model never spoke.
        try:
            result = await chat(
                config.MODEL_SPARK, [{"role": "user", "content": content}],
                max_tokens=8000 * 2, temperature=0.0, json_mode=True,
                op="identify", cache=False)
        except MetaError as exc:
            return {**_mock(config.MODEL_SPARK, "identify", str(exc)),
                    "identification": None, "error": exc.as_dict()}

    raw = (result.get("data") or {}) if isinstance(result.get("data"), dict) else {}
    # The model sometimes answers in its own vocabulary — normalise to ours.
    low = {k: (str(v).strip().lower() if isinstance(v, str) else v)
           for k, v in raw.items()}
    cat = str(low.get("category") or "general")
    cat_map = {"car": "vehicle", "truck": "vehicle", "pickup": "vehicle",
               "pickup truck": "vehicle", "automobile": "vehicle",
               "automotive": "vehicle", "van": "vehicle", "bus": "vehicle",
               "motorbike": "vehicle", "motorcycle": "vehicle", "bike": "vehicle",
               "crop": "produce", "grain": "produce", "vegetable": "produce",
               "fruit": "produce", "food": "food", "meal": "food",
               "tool": "equipment", "machine": "equipment", "machinery": "equipment",
               "farming": "equipment", "appliance": "appliance",
               "electronic": "electronics", "device": "electronics"}
    category = cat_map.get(cat, cat)
    ver = str(low.get("vertical") or "")
    ver_map = {"automotive": "vehicles", "vehicle": "vehicles",
               "agriculture": "farming", "farm": "farming",
               "construction": "building", "food": "food",
               "electronics": "electronics", "parts": "parts"}
    vertical = ver_map.get(ver, ver)
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.0
    if category not in {"vehicle", "equipment", "produce", "food", "parts",
                        "building", "electronics", "appliance", "general"}:
        category = "general"
    if vertical not in {"vehicles", "farming", "food", "parts", "building", "electronics"}:
        vertical = ""
    matches_claim = raw.get("matches_claim")
    if matches_claim is not None:
        matches_claim = bool(matches_claim)
    cleaned = {
        "brand": str(raw.get("brand") or "").strip(),
        "model": str(raw.get("model") or "").strip(),
        "category": category, "vertical": vertical,
        "confidence": round(confidence, 3),
        "features": [str(f) for f in (raw.get("features") or [])][:4],
        "matches_claim": matches_claim,
        "claim_reason": str(raw.get("claim_reason") or ""),
        "notes": str(raw.get("notes") or ""),
    }
    # The model's verdict IS the identification — always. An unbranded answer
    # ({brand: "", model: ""}) is a real statement about the photo ("no visible
    # make"), not a failure, so it must flow through untouched. Throwing it away
    # here is what made honest photos read as "vision failed".
    return {**result, "identification": cleaned}


async def identify_video(frames: list[bytes], *, claim: dict | None = None) -> dict:
    """Live vision over video: Muse Spark watches sampled frames and says what
    the clip shows — same structured answer as a photo identification (brand,
    model, category, vertical, confidence, features). Frames are passed in
    capture order so the model also sees motion/state across time.

    Returns {identification?, is_mock, model, ...}. `identification` is None in
    mock mode or when the response cannot be parsed.
    """
    if not config.is_live():
        return {**_mock(config.MODEL_SPARK, "identify_video"), "identification": None}
    if not frames:
        return {**_mock(config.MODEL_SPARK, "identify_video", "no frames extracted"),
                "identification": None}

    import base64
    lines = [
        "These frames are sampled in order from one video of goods for sale. "
        "Watch them together and identify exactly what the video shows.",
        'Return ONLY JSON: {"brand": string, "model": string, '
        '"category": one of vehicle equipment produce food parts building electronics appliance general, '
        '"vertical": one of vehicles farming food parts building electronics, '
        '"confidence": number 0-1, "features": [up to 4 short visible details], '
        '"notes": "one short sentence"}.',
        "Brand and model are the visible make, e.g. brand Toyota model Hilux. "
        "If you cannot tell the brand, return an empty brand string with low "
        "confidence — never guess a brand.",
    ]
    if claim:
        lines.append(
            "The seller claims this video shows: "
            f"item={claim.get('item', '')!r} brand={claim.get('brand', '')!r} "
            f"model={claim.get('model', '')!r}. "
            'Add "matches_claim": true/false and a "claim_reason" string explaining why.'
        )
    content: list[dict] = [{"type": "text", "text": "\n".join(lines)}]
    for b in frames:
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b).decode()}})
    try:
        result = await chat(config.MODEL_SPARK,
                            [{"role": "user", "content": content}],
                            max_tokens=8000, temperature=0.0,
                            json_mode=True, op="identify_video", cache=False)
    except MetaError as exc:
        return {**_mock(config.MODEL_SPARK, "identify_video", str(exc)),
                "identification": None, "error": exc.as_dict()}

    raw = (result.get("data") or {}) if isinstance(result.get("data"), dict) else {}
    low = {k: (str(v).strip().lower() if isinstance(v, str) else v)
           for k, v in raw.items()}
    # Same normalisation as photo identification so videos rank and filter
    # exactly like photos downstream.
    cat = str(low.get("category") or "general")
    cat_map = {"car": "vehicle", "truck": "vehicle", "pickup": "vehicle",
               "pickup truck": "vehicle", "automobile": "vehicle",
               "automotive": "vehicle", "van": "vehicle", "bus": "vehicle",
               "motorbike": "vehicle", "motorcycle": "vehicle", "bike": "vehicle",
               "crop": "produce", "grain": "produce", "vegetable": "produce",
               "fruit": "produce", "food": "food", "meal": "food",
               "tool": "equipment", "machine": "equipment", "machinery": "equipment",
               "farming": "equipment", "appliance": "appliance",
               "electronic": "electronics", "device": "electronics"}
    category = cat_map.get(cat, cat)
    ver = str(low.get("vertical") or "")
    ver_map = {"automotive": "vehicles", "vehicle": "vehicles",
               "agriculture": "farming", "farm": "farming",
               "construction": "building", "food": "food",
               "electronics": "electronics", "parts": "parts"}
    vertical = ver_map.get(ver, ver)
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.0
    if category not in {"vehicle", "equipment", "produce", "food", "parts",
                        "building", "electronics", "appliance", "general"}:
        category = "general"
    if vertical not in {"vehicles", "farming", "food", "parts", "building", "electronics"}:
        vertical = ""
    matches_claim = raw.get("matches_claim")
    if matches_claim is not None:
        matches_claim = bool(matches_claim)
    cleaned = {
        "brand": str(raw.get("brand") or "").strip(),
        "model": str(raw.get("model") or "").strip(),
        "category": category, "vertical": vertical,
        "confidence": round(confidence, 3),
        "features": [str(f) for f in (raw.get("features") or [])][:4],
        "matches_claim": matches_claim,
        "claim_reason": str(raw.get("claim_reason") or ""),
        "notes": str(raw.get("notes") or ""),
    }
    return {**result, "identification": cleaned}


async def verify_showme(listing: dict, expected_code: str, transcript: str,
                        counted: int | None, *, frames_used: int = 0) -> dict:
    """Muse Spark compares video findings against the listing and issues a verdict."""
    if not config.is_live():
        return {**_mock(config.MODEL_SPARK, "showme_verify"), "data": None}

    prompt = (
        f"LISTING: {listing.get('item')} | claims {listing.get('quantity_available')} {listing.get('unit')} "
        f"| seller verification status: {listing.get('verification_status')}\n"
        f"EXPECTED CODE WORD: {expected_code or '(none issued)'}\n"
        f"VIDEO TRANSCRIPT: {transcript or '(no speech detected)'}\n"
        f"ITEMS COUNTED IN VIDEO: {counted if counted is not None else 'not determined'} ({frames_used} frames sampled)\n\n"
        "Decide whether the video proves the listing. "
        'Return ONLY JSON: {"verdict": "Match"|"Partial match"|"Mismatch", '
        '"reasons": [up to 3 short strings], "count_matches": bool, "code_word_heard": bool}'
    )
    try:
        result = await chat(
            config.MODEL_SPARK,
            [{"role": "system",
              "content": "You are a strict goods-verification auditor. Be conservative: only say Match when "
                         "the counted quantity equals the listing and the code word was heard."},
             {"role": "user", "content": prompt}],
            max_tokens=350, temperature=0.0, json_mode=True, op="showme_verify",
        )
    except MetaError as exc:
        return {**_mock(config.MODEL_SPARK, "showme_verify", str(exc)), "data": None,
                "error": exc.as_dict()}
    data = result.get("data") or {}
    verdict = data.get("verdict")
    if verdict not in ("Match", "Partial match", "Mismatch"):
        verdict = None
    reasons = [str(r) for r in (data.get("reasons") or [])][:3]
    return {**result, "data": {"verdict": verdict, "reasons": reasons,
                               "count_matches": data.get("count_matches"),
                               "code_word_heard": data.get("code_word_heard")} if verdict else None}


# ─────────────────────────────────────────────────────────────────────────────
# F5 / F6 / F7 — reasoning helpers
# ─────────────────────────────────────────────────────────────────────────────


async def fraud_assess(context_lines: list[str], question: str) -> dict:
    """Muse Spark answers 'is this legit?' with explicit reasoning and risk flags."""
    if not config.is_live():
        return {**_mock(config.MODEL_SPARK, "fraud"), "data": None}

    prompt = ("CONTEXT:\n" + "\n".join(context_lines) +
              f"\n\nBUYER QUESTION: {question}\n\n"
              'Return ONLY JSON: {"answer": "2-4 sentence direct answer", '
              '"risk_flags": [short strings, empty if none], "reasoning": [short evidence lines]}')
    try:
        result = await chat(
            config.MODEL_SPARK,
            [{"role": "system",
              "content": "You are FindYourTrade's fraud advisor for Malawi. Flag count mismatches, "
                         "brand-new accounts with large offers, and missing verification. Be practical, "
                         "never alarmist, and never claim a listing is legally owned."},
             {"role": "user", "content": prompt}],
            max_tokens=450, temperature=0.2, json_mode=True, op="fraud",
        )
    except MetaError as exc:
        return {**_mock(config.MODEL_SPARK, "fraud", str(exc)), "data": None,
                "error": exc.as_dict()}
    data = result.get("data") or {}
    answer = (data.get("answer") or "").strip()
    if not answer:
        return {**result, "data": None}
    return {**result, "data": {
        "answer": answer,
        "risk_flags": [str(f) for f in (data.get("risk_flags") or [])][:4],
        "reasoning": [str(r) for r in (data.get("reasoning") or [])][:5],
    }}


async def compare_vehicles(bullets: list[str]) -> dict:
    if not config.is_live():
        return _mock(config.MODEL_SPARK, "compare")
    prompt = ("Compare these vehicles for a Malawian buyer on price, registration, odometer and "
              "forex footprint. End by naming the best value pick.\n" + "\n".join(bullets))
    try:
        return await chat(config.MODEL_SPARK,
                          [{"role": "system", "content": "You compare vehicles honestly and briefly (max 120 words)."},
                           {"role": "user", "content": prompt}],
                          max_tokens=320, op="compare")
    except MetaError as exc:
        return {**_mock(config.MODEL_SPARK, "compare", str(exc)), "error": exc.as_dict()}


async def explain_netting(debts: list[dict], proposal: str) -> dict:
    if not config.is_live():
        return _mock(config.MODEL_WONDER, "netting")
    prompt = (f"DEBTS: {debts}\nDETERMINISTIC PROPOSAL: {proposal}\n\n"
              "Explain this to the traders in plain language: what each party's net position becomes, "
              "why no cash needs to move, and that every party must confirm. Max 110 words.")
    try:
        return await wonder_orchestrate(prompt)
    except MetaError as exc:
        return {**_mock(config.MODEL_WONDER, "netting", str(exc)), "error": exc.as_dict()}


async def generate_trade_card(prompt: str) -> dict:
    """Muse Image (P2 low-literacy trade cards)."""
    if not config.is_live():
        return {**_mock(config.MODEL_IMAGE, "image"), "image": None}
    try:
        payload = await _request("images/generations",
                                 json_body={"model": config.MODEL_IMAGE, "prompt": prompt},
                                 model=config.MODEL_IMAGE, op="image", timeout=90)
        return {"image": payload, "is_mock": False, "model": config.MODEL_IMAGE}
    except MetaError as exc:
        return {**_mock(config.MODEL_IMAGE, "image", str(exc)), "image": None,
                "error": exc.as_dict()}


# ─────────────────────────────────────────────────────────────────────────────
# Diagnostics
# ─────────────────────────────────────────────────────────────────────────────


async def selftest() -> dict:
    """Ping each configured model with a minimal request. Returns per-model status."""
    results: list[dict] = []

    checks: list[tuple[str, str]] = [
        ("spark", config.MODEL_SPARK),
        ("wonder", config.MODEL_WONDER),
    ]
    if not config.is_live():
        results.extend(
            [{"slot": slot, "model": model, "ok": False, "reason": "no META_API_KEY"}
             for slot, model in checks])
        return {
            "mode": config.mode_label(),
            "live": False,
            "results": results,
            "ok_count": 0,
            "hint": "Add META_API_KEY to .env for live model calls.",
        }

    for slot, model in checks:
        started = time.perf_counter()
        try:
            payload = await _request(
                "chat/completions",
                json_body={"model": model,
                           "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
                           "max_tokens": 512},
                model=model, op="selftest", timeout=20,
            )
            text = payload["choices"][0]["message"].get("content", "").strip()
            results.append({"slot": slot, "model": model, "ok": True,
                            "latency_ms": int((time.perf_counter() - started) * 1000),
                            "reply": text[:40]})
        except MetaError as exc:
            results.append({"slot": slot, "model": model, "ok": False,
                            "status": exc.status, "reason": str(exc),
                            "detail": exc.detail[:200]})
        except Exception as exc:  # pragma: no cover - defensive
            results.append({"slot": slot, "model": model, "ok": False, "reason": str(exc)})

    return {
        "mode": config.mode_label(),
        "live": any(r["ok"] for r in results),
        "results": results,
        "ok_count": sum(1 for r in results if r["ok"]),
        "hint": "A failing slot usually means the model id differs from your account's catalog — "
                "override it via MODEL_* in .env.",
    }
