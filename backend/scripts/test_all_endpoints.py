"""End-to-end smoke test for every FindYourTrade backend route.

Run against the running stack (through nginx by default):
    python scripts/test_all_endpoints.py
    python scripts/test_all_endpoints.py http://localhost:9000
"""

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave
import io
import struct
import math

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost").rstrip("/")

results = []


def call(name, method, path, *, json_body=None, raw_body=None, headers=None, timeout=180):
    url = f"{BASE}{path}"
    data = None
    hdrs = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode()
        hdrs["Content-Type"] = "application/json"
    elif raw_body is not None:
        data = raw_body
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            code = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        code = e.code
    except Exception as exc:
        body = f"EXCEPTION: {exc}"
        code = 0
    ms = int((time.perf_counter() - started) * 1000)
    ok = 200 <= code < 300
    results.append((name, method, path, code, ms, ok, body))
    print(f"{'PASS' if ok else 'FAIL'}  {code:>3}  {ms:>6}ms  {method:<4} {path:<45} {name}")
    return code, body


def as_json(body):
    try:
        return json.loads(body)
    except Exception:
        return None


def make_wav(seconds=0.5, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = int(rate * seconds)
        w.writeframes(b"".join(
            struct.pack("<h", int(2000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(frames)))
    return buf.getvalue()


def multipart(field, filename, content, content_type):
    boundary = "----boundary" + uuid.uuid4().hex
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        content, b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


print(f"Testing {BASE}\n" + "=" * 78)

# ── Core / meta ──────────────────────────────────────────────────────────────
call("health", "GET", "/api/health")
call("models status", "GET", "/api/models/status")
call("db status", "GET", "/api/db/status")

# ── Catalog ──────────────────────────────────────────────────────────────────
code, body = call("listings", "GET", "/api/listings")
listing_id = None
parsed = as_json(body)
if isinstance(parsed, list) and parsed:
    listing_id = parsed[0].get("id")
elif isinstance(parsed, dict):
    items = parsed.get("listings") or parsed.get("results") or []
    if items:
        listing_id = items[0].get("id")

if listing_id:
    call("listing detail", "GET", f"/api/listings/{listing_id}")
else:
    print("SKIP  listing detail — no listing id returned")

# ── NLU / matching ───────────────────────────────────────────────────────────
call("parse", "POST", "/api/parse", json_body={"text": "I need 50 bags of fertilizer in Blantyre"})
call("match", "POST", "/api/match", json_body={"text": "I need 20 bags of urea in Lilongwe"})
call("vector search", "POST", "/api/db/vector-search",
     json_body={"query": "fertilizer", "limit": 5})

# ── Chat / fraud ─────────────────────────────────────────────────────────────
call("chat greeting", "POST", "/api/chat",
     json_body={"messages": [{"role": "user", "content": "Hello"}], "listing_id": None})
call("chat search", "POST", "/api/chat",
     json_body={"messages": [{"role": "user", "content": "I need 4 bags of fertilizer near Lilongwe"}],
                "listing_id": None})
call("fraud check", "POST", "/api/fraud-check",
     json_body={"messages": [{"role": "user",
                              "content": "The seller wants full payment upfront by mobile money before viewing."}],
                "listing_id": listing_id})

# ── Voice ────────────────────────────────────────────────────────────────────
wav = make_wav()
raw, hdrs = multipart("file", "voice.wav", wav, "audio/wav")
call("transcribe wav", "POST", "/api/transcribe", raw_body=raw, headers=hdrs)

# ── Show Me ──────────────────────────────────────────────────────────────────
if listing_id:
    code, body = call("showme codeword", "POST", "/api/showme/codeword",
                      json_body={"listing_id": listing_id})
    codeword = (as_json(body) or {}).get("code_word") or ""
    call("showme verify (no video)", "POST",
         f"/api/showme/verify?listing_id={listing_id}&claimed_count=4&code_word_said={codeword}")
else:
    print("SKIP  showme — no listing id")

# ── Vehicles ─────────────────────────────────────────────────────────────────
code, body = call("listings for compare", "GET", "/api/listings")
ids = []
p = as_json(body)
if isinstance(p, list):
    ids = [x.get("id") for x in p if x.get("category") == "vehicle"][:2]
elif isinstance(p, dict):
    items = p.get("listings") or p.get("results") or []
    ids = [x.get("id") for x in items if x.get("category") == "vehicle"][:2]
if len(ids) >= 2:
    call("vehicle compare", "POST", "/api/vehicles/compare", json_body={"listing_ids": ids})
else:
    print(f"SKIP  vehicle compare — need 2 vehicle listings, found {len(ids)}")

# ── Netting ──────────────────────────────────────────────────────────────────
call("netting demo", "GET", "/api/netting/demo")
call("netting propose", "POST", "/api/netting/propose",
     json_body={"debts": [{"debtor": "s01", "creditor": "s02", "amount": 1000},
                          {"debtor": "s02", "creditor": "s03", "amount": 1000},
                          {"debtor": "s03", "creditor": "s01", "amount": 1000}]})

# ── Dashboard / recommender ──────────────────────────────────────────────────
call("dashboard", "GET", "/api/dashboard")
call("recommendations", "GET", "/api/recommendations?user_id=guest&limit=5")
uid = f"test-{uuid.uuid4().hex[:8]}"
call("save profile", "POST", "/api/profile",
     json_body={"user_id": uid, "interests": ["fertilizer"], "location": "Lilongwe"})
call("get profile", "GET", f"/api/profile/{uid}")
if listing_id:
    call("log event", "POST", "/api/events",
         json_body={"user_id": uid, "listing_id": listing_id, "event": "view"})
    call("similar", "GET", f"/api/recommendations/similar/{listing_id}")

# ── Admin-ish ────────────────────────────────────────────────────────────────
call("telemetry", "GET", "/api/models/telemetry?limit=10")
call("models selftest", "POST", "/api/models/selftest")

# ── Summary ──────────────────────────────────────────────────────────────────
print("=" * 78)
passed = sum(1 for r in results if r[5])
failed = [r for r in results if not r[5]]
print(f"{passed}/{len(results)} passed")
if failed:
    print("\nFailures:")
    for name, method, path, code, ms, ok, body in failed:
        print(f"  - {name} [{method} {path}] HTTP {code}: {body[:220]}")
sys.exit(1 if failed else 0)
