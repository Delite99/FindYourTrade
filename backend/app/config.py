"""Central configuration — loads .env before any module reads os.environ.

Order matters: python-dotenv must run before meta_client captures settings.
Import this module first in app startup (main.py does).
"""
from __future__ import annotations
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv optional at runtime
    load_dotenv = None

# Project root is two levels up from backend/app/
_ROOT = Path(__file__).resolve().parents[2]
for candidate in (_ROOT / ".env", _ROOT / "backend" / ".env", Path.cwd() / ".env"):
    if load_dotenv and candidate.exists():
        load_dotenv(candidate, override=False)


def _get(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


# ── Meta Model API ────────────────────────────────────────────────────────────
META_API_BASE = _get("META_API_BASE", "https://api.meta.ai/v1")
META_API_KEY = _get("META_API_KEY")

MODEL_SPARK = _get("MODEL_SPARK", "muse-spark-1.3-contributor")
MODEL_WONDER = _get("MODEL_WONDER", "muse-wonder-1.0")
MODEL_TRANSCRIBE = _get("MODEL_TRANSCRIBE", "muse-voice-transcribe-1.0")
MODEL_SAM = _get("MODEL_SAM", "sam-3.1")
MODEL_IMAGE = _get("MODEL_IMAGE", "muse-image-1.0")

# ── Runtime tuning ────────────────────────────────────────────────────────────
REQUEST_TIMEOUT = float(_get("META_TIMEOUT", "45"))
MAX_RETRIES = int(_get("META_MAX_RETRIES", "3"))
CACHE_TTL_SECONDS = int(_get("META_CACHE_TTL", "900"))
MAX_CALLS_PER_SESSION = int(_get("META_MAX_CALLS", "400"))
VISION_MAX_FRAMES = int(_get("VISION_MAX_FRAMES", "6"))
FRAME_MAX_EDGE = int(_get("FRAME_MAX_EDGE", "768"))

# ── Voice (Muse Voice Transcribe) ─────────────────────────────────────────────
# Turn-detection mode: ENDPOINTING (model-detected turns, default),
# DIARIZATION (adds speaker labels), PUSH_TO_TALK (single client-delimited turn).
_ASR_MODES = {"ENDPOINTING", "DIARIZATION", "PUSH_TO_TALK"}
ASR_MODE = _get("ASR_MODE", "ENDPOINTING").upper()
if ASR_MODE not in _ASR_MODES:
    ASR_MODE = "ENDPOINTING"

# Language hints steer recognition; omit the env var to let the model detect.
# Supported: Arabic, Bengali, Dutch, English, French, German, Hebrew, Hindi,
# Indonesian, Italian, Japanese, Kannada, Korean, Malay, Mandarin Chinese,
# Marathi, Polish, Portuguese, Spanish, Tagalog, Tamil, Telugu, Thai, Turkish,
# Vietnamese. Chichewa is not on that list, so English is the safe default.
ASR_LANGUAGE_BIAS = [x.strip() for x in _get("ASR_LANGUAGE_BIAS", "English").split(",")
                     if x.strip()]

# Domain vocabulary the model tends to mishear (product names, places, acronyms).
ASR_KEYWORDS = [x.strip() for x in _get("ASR_KEYWORDS", "").split(",") if x.strip()]

# ── HTTP ──────────────────────────────────────────────────────────────────────
# Comma-separated allowed origins. "*" suits local development; set explicit
# origins before exposing the API publicly.
CORS_ORIGINS = [x.strip() for x in _get("CORS_ORIGINS", "*").split(",") if x.strip()]


def model_map() -> dict[str, str]:
    return {
        "spark": MODEL_SPARK,
        "wonder": MODEL_WONDER,
        "transcribe": MODEL_TRANSCRIBE,
        "sam": MODEL_SAM,
        "image": MODEL_IMAGE,
    }


def is_live() -> bool:
    return bool(META_API_KEY)


def mode_label() -> str:
    return "live · Meta Model API" if is_live() else "mock · no META_API_KEY"


def public_config() -> dict:
    """Safe-to-expose config snapshot (never includes key material)."""
    return {
        "mode": mode_label(),
        "live": is_live(),
        "api_base": META_API_BASE,
        "models": model_map(),
        "timeout_seconds": REQUEST_TIMEOUT,
        "max_retries": MAX_RETRIES,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "spend_cap_calls": MAX_CALLS_PER_SESSION,
        "asr": {
            "mode": ASR_MODE,
            "language_bias": ASR_LANGUAGE_BIAS,
            "keywords": ASR_KEYWORDS,
        },
    }
