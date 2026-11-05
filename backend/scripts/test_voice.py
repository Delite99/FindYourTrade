"""Test Muse Voice Transcribe end to end through the backend.

    python scripts/test_voice.py                  # uses the bundled sample URL
    python scripts/test_voice.py my_clip.wav      # your own recording
    python scripts/test_voice.py clip.wav http://localhost:9000

Audio must be mono 16-bit PCM WAV at 16 kHz or 24 kHz — that is the only thing
Meta's /v1/asr/transcribe accepts. Convert anything else first:
    ffmpeg -i input.m4a -ac 1 -ar 16000 -c:a pcm_s16le out.wav
"""

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
import wave

DEFAULT_SAMPLE = "https://github.com/alphacep/vosk-api/raw/master/python/example/test.wav"

path = sys.argv[1] if len(sys.argv) > 1 else None
BASE = (sys.argv[2] if len(sys.argv) > 2 else "http://localhost").rstrip("/")

if path is None:
    print(f"No file given — downloading a public speech sample…")
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    urllib.request.urlretrieve(DEFAULT_SAMPLE, path)

with wave.open(path, "rb") as w:
    channels, rate, bits = w.getnchannels(), w.getframerate(), w.getsampwidth() * 8
    seconds = w.getnframes() / rate
print(f"Audio: {path}")
print(f"  {channels} ch · {bits}-bit · {rate} Hz · {seconds:.2f}s")

if channels != 1 or bits != 16 or rate not in (16000, 24000):
    print("\nFAIL  Unsupported format. Meta needs mono 16-bit PCM WAV at 16/24 kHz.")
    print("      ffmpeg -i input -ac 1 -ar 16000 -c:a pcm_s16le out.wav")
    sys.exit(1)

with open(path, "rb") as f:
    audio = f.read()

boundary = "----voiceboundary"
body = b"".join([
    f"--{boundary}\r\n".encode(),
    b'Content-Disposition: form-data; name="file"; filename="voice.wav"\r\n',
    b"Content-Type: audio/wav\r\n\r\n",
    audio, b"\r\n",
    f"--{boundary}--\r\n".encode(),
])

req = urllib.request.Request(
    f"{BASE}/api/transcribe", data=body, method="POST",
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
)

try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        result = json.loads(resp.read().decode())
except urllib.error.HTTPError as e:
    print(f"\nFAIL  HTTP {e.code}: {e.read().decode()[:300]}")
    sys.exit(1)

print("\nResult")
print(f"  provider : {result.get('provider')}")
print(f"  model    : {result.get('model')}")
print(f"  is_mock  : {result.get('is_mock')}")
print(f"  duration : {result.get('audio_duration_ms')} ms")
print(f"  latency  : {result.get('latency_ms')} ms")
print(f"  session  : {result.get('session_id')}")

turns = result.get("turns") or []
print(f"  turns    : {len(turns)}")
for t in turns:
    speaker = f"speaker {t['speaker']}" if t.get("speaker") else "—"
    print(f"    · turn {t.get('turnId')} [{speaker}] {t.get('startMs')}–{t.get('endMs')}ms"
          f"  {t.get('transcript', '')[:60]!r}")

print(f"\n  TRANSCRIPT: {result.get('text')!r}")

if result.get("error"):
    print(f"\nFAIL  {result['error']}")
    sys.exit(1)
if result.get("is_mock"):
    print("\nFAIL  Response is mock — META_API_KEY missing or the call was rejected.")
    sys.exit(1)
if not (result.get("text") or "").strip():
    print("\nFAIL  Empty transcript — silence, wrong format, or the model heard nothing.")
    sys.exit(1)

print("\nPASS  Live Meta Voice Transcribe returned text.")
