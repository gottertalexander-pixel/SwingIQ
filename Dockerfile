# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libglib2.0-0 libgl1 wget \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgl1 libgomp1 wget curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY main.py database.py auth.py video_processor.py ./

# Download MediaPipe model (fails gracefully — OpenCV fallback kicks in)
RUN mkdir -p models && \
    wget -q --timeout=30 -O models/pose_landmarker_full.task \
      "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task" \
    && echo "✓ MediaPipe model ready" \
    || echo "⚠ Model unavailable — OpenCV heuristic fallback active"

RUN mkdir -p /tmp/swingiq_uploads

RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
