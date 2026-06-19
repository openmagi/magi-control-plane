# ── stage 1: build ───────────────────────────────────────────────────
FROM python:3.12-slim AS build
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev \
 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml /build/
COPY src /build/src
RUN pip install --no-cache-dir --prefix=/install -e .

# ── stage 2: runtime (minimal, non-root, read-only rootfs friendly) ──
FROM python:3.12-slim
RUN useradd -u 10001 -r -s /sbin/nologin -m -d /home/magi magi \
 && mkdir -p /data \
 && chown magi:magi /data
COPY --from=build /install /usr/local
COPY --from=build /build/src /app/src
ENV PYTHONPATH=/app/src
ENV MAGI_CP_KEY_DIR=/data/keys
ENV MAGI_CP_DSN=sqlite:////data/magi-cp.sqlite
ENV MAGI_CP_SERVE=1
VOLUME ["/data"]
USER 10001
EXPOSE 8787
CMD ["uvicorn", "--factory", "magi_cp.cloud.app:create_app", "--host", "0.0.0.0", "--port", "8787"]
