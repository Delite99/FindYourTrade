"""Video/audio handling for Show Me verification (PRD section 10).

Samples a handful of frames from a short clip with OpenCV and extracts the audio
track with ffmpeg. Both dependencies are optional: when missing, the pipeline
degrades to "no frames / no audio" and the caller falls back to self-report,
clearly labelled in the response.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config

try:
    import cv2  # type: ignore
    _HAS_CV2 = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_CV2 = False


def capabilities() -> dict:
    return {
        "opencv": _HAS_CV2,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "max_frames": config.VISION_MAX_FRAMES,
        "frame_max_edge": config.FRAME_MAX_EDGE,
    }


def _suffix(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    return ext if ext in {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"} else ".mp4"


def extract_frames(video_bytes: bytes, filename: str = "showme.mp4",
                   max_frames: int | None = None) -> dict:
    """Sample evenly-spaced JPEG frames from the clip.

    Returns {"frames": [bytes], "count": int, "duration_s": float, "note": str}
    """
    max_frames = max_frames or config.VISION_MAX_FRAMES
    if not video_bytes:
        return {"frames": [], "count": 0, "duration_s": 0.0, "note": "empty upload"}
    if not _HAS_CV2:
        return {"frames": [], "count": 0, "duration_s": 0.0,
                "note": "opencv not installed (pip install opencv-python-headless)"}

    tmp_dir = Path(tempfile.mkdtemp(prefix="fyt_frames_"))
    path = tmp_dir / f"clip{_suffix(filename)}"
    path.write_bytes(video_bytes)
    frames: list[bytes] = []
    duration = 0.0
    note = ""
    try:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            return {"frames": [], "count": 0, "duration_s": 0.0, "note": "could not open video"}

        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = total / fps if fps else 0.0

        # Evenly spaced sampling across the clip, skipping the first/last 10%.
        if total > 0:
            lo, hi = int(total * 0.1), int(total * 0.9)
            span = max(1, hi - lo)
            targets = [lo + int(span * i / max(1, max_frames - 1)) for i in range(max_frames)]
        else:
            targets = [i for i in range(max_frames)]

        for target in targets:
            cap.set(cv2.CAP_PROP_POS_FRAMES, min(target, max(0, total - 1)))
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            edge = max(h, w)
            if edge > config.FRAME_MAX_EDGE:
                scale = config.FRAME_MAX_EDGE / edge
                frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
            if ok:
                frames.append(buf.tobytes())
        cap.release()
        if not frames:
            note = "no decodable frames"
    except Exception as exc:  # pragma: no cover - defensive
        note = f"frame extraction failed: {exc}"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return {"frames": frames, "count": len(frames),
            "duration_s": round(duration, 1), "note": note}


def extract_audio(video_bytes: bytes, filename: str = "showme.mp4") -> dict:
    """Extract a mono 16 kHz WAV track with ffmpeg (best effort)."""
    if not video_bytes:
        return {"audio": b"", "ok": False, "note": "empty upload"}
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"audio": b"", "ok": False, "note": "ffmpeg not installed"}

    tmp_dir = Path(tempfile.mkdtemp(prefix="fyt_audio_"))
    src = tmp_dir / f"clip{_suffix(filename)}"
    dst = tmp_dir / "audio.wav"
    src.write_bytes(video_bytes)
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
             "-f", "wav", str(dst)],
            capture_output=True, timeout=90,
        )
        if proc.returncode != 0 or not dst.exists():
            return {"audio": b"", "ok": False,
                    "note": (proc.stderr.decode("utf-8", "ignore")[-200:] or "ffmpeg failed")}
        return {"audio": dst.read_bytes(), "ok": True, "note": ""}
    except Exception as exc:  # pragma: no cover - defensive
        return {"audio": b"", "ok": False, "note": f"audio extraction failed: {exc}"}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def decode_image(image_bytes: bytes):
    """Decode JPEG/PNG/WebP bytes to a Pillow image (RGB), or None."""
    if not image_bytes:
        return None
    try:
        from PIL import Image
    except Exception:
        return None
    import io
    try:
        return Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return None


def save_jpeg(image, path: str, quality: int = 86) -> bool:
    """Save a Pillow image as JPEG. Returns True on success."""
    try:
        from PIL import Image  # noqa: F401  (ensures Pillow present)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "JPEG", quality=quality)
        return True
    except Exception:
        return False


def studio_card_from_b64(b64_png: str, path: str) -> bool:
    """Decode a Muse Image payload (b64_json, usually WebP) and save as JPEG."""
    if not b64_png:
        return False
    import base64
    import io
    try:
        raw = base64.b64decode(b64_png)
    except Exception:
        return False
    img = decode_image(raw)
    if img is None:
        return False
    return save_jpeg(img, path)


def promo_video_from_image(image_path: str, path: str, *,
                           duration_s: int = 10, fps: int = 24,
                           caption: str = "", price_line: str = "") -> dict:
    """Assemble a genuine ≤10-second MP4 from the trader's real card photo.

    The video IS the verified goods — no stock footage, no AI actors. ffmpeg
    animates the real photo with a slow zoom (Ken Burns) and burn-in caption
    lines for small screens. Returns {ok, path, note}.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"ok": False, "path": "", "note": "ffmpeg not installed"}
    if not Path(image_path).exists():
        return {"ok": False, "path": "", "note": "source image missing"}

    tmp_dir = Path(tempfile.mkdtemp(prefix="fyt_promo_"))
    try:
        # Plain-text SLATE_PATH file keeps commas/colons in captions from
        # breaking ffmpeg's drawtext parser.
        lines = [ln.strip() for ln in (caption or "").split("\n") if ln.strip()]
        if price_line:
            lines.append(price_line.strip())
        slate = tmp_dir / "slate.txt"
        slate.write_text("\n".join(f"[{i + 1}/4] {ln}" for i, ln in enumerate(lines[:4])),
                         encoding="utf-8")
        frames = duration_s * fps
        cmd = [
            ffmpeg, "-y", "-loop", "1", "-i", str(image_path),
            "-vf",
            ("scale=1280:720,"
             f"zoompan=z='min(zoom+0.0015,1.35)':d={frames}:s=1280x720:fps={fps},"
             "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
             "textfile='" + str(slate) + "':"
             "fontsize=30:fontcolor=white:borderw=2:bordercolor=black@0.8:"
             "x=(w-text_w)/2:y=h-140"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-t", str(duration_s), str(Path(path).resolve()),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=180)
        if proc.returncode != 0 or not Path(path).exists():
            err = proc.stderr.decode("utf-8", "ignore")[-300:]
            return {"ok": False, "path": "", "note": err or "ffmpeg failed"}
        return {"ok": True, "path": str(path), "note": ""}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "path": "", "note": f"promo render failed: {exc}"}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

