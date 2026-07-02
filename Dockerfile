# ── stage 1: build ───────────────────────────────────────────────────
FROM python:3.12-slim AS build
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev \
 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock /build/
COPY src /build/src
# Reproducible, hash-pinned install from the committed lock (SUPPLY-1). The old
# `pip install -e .` re-resolved dependencies at build time from the unbounded
# floors in pyproject, so image contents drifted build-to-build and a future
# breaking/compromised transitive release could land silently. Export the
# frozen lock to a hashed requirements file, install those exact versions with
# --require-hashes, then install the project itself with --no-deps.
RUN pip install --no-cache-dir uv \
 && uv export --frozen --no-dev --no-emit-project -o /build/requirements.txt \
 && pip install --no-cache-dir --prefix=/install --require-hashes -r /build/requirements.txt \
 && pip install --no-cache-dir --prefix=/install --no-deps -e .

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
# /home/magi is on the read-only root FS in the compose run — pin the policy
# store onto the /data volume so PUT /policies/{id} can actually persist.
ENV MAGI_CP_POLICY_STORE=/data/policies.json
ENV MAGI_CP_SERVE=1
VOLUME ["/data"]
USER 10001
EXPOSE 8787
# Use _build_production_app (NOT create_app) — it constructs a VerifierRegistry
# and calls register_builtins(), so /presets surfaces the 5 wired verifiers.
# create_app() with no args ships an empty registry → vendor-only catalog.
CMD ["uvicorn", "--factory", "magi_cp.cloud.app:_build_production_app", "--host", "0.0.0.0", "--port", "8787"]
