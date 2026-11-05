# ── Backend Dockerfile ────────────────────────────────────────────────────────
# Python 3.12 slim base — small, production-grade
FROM python:3.12-slim AS base

# Keeps Python from generating .pyc files and enables unbuffered stdout
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ffmpeg → audio track extraction for Show Me video verification.
# libgl/libglib keep OpenCV headless happy on slim images.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ── Dependencies layer (cached unless requirements.txt changes) ────────────────
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Application source ────────────────────────────────────────────────────────
COPY . .

# Expose the port uvicorn will bind to
EXPOSE 9000

# ── Runtime command ───────────────────────────────────────────────────────────
# --host 0.0.0.0  → accept connections from outside the container
# --port 9000     → match EXPOSE and docker-compose mapping
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]
