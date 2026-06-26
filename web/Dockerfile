# magi-cp-dashboard. Next.js 14 standalone image.
# Build: docker build -t ghcr.io/openmagi/magi-cp-dashboard:latest -f web/Dockerfile .
# Run:   docker run --rm -p 3000:3000 -e MAGI_CP_CLOUD_URL=http://host.docker.internal:8787 ghcr.io/openmagi/magi-cp-dashboard:latest

# ── deps ──────────────────────────────────────────────────────────────────
FROM node:20-bookworm-slim AS deps
WORKDIR /app
COPY web/package.json web/package-lock.json* ./
RUN npm ci --legacy-peer-deps

# ── builder ───────────────────────────────────────────────────────────────
FROM node:20-bookworm-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY web/ ./
# Q96 docs renderer reads markdown via path.resolve(process.cwd(), "..",
# "docs"), which inside this builder resolves to /docs. Stage the repo's
# docs/ tree at that absolute path so static prerender of /docs/[slug]
# finds the files. The page is `force-static`, so the runner stage does
# not need the markdown at runtime.
COPY docs/ /docs
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ── runner ────────────────────────────────────────────────────────────────
FROM node:20-bookworm-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# wget for the healthcheck below (curl would also do, but wget is one
# binary lighter and matches the prior alpine recipe).
RUN apt-get update && apt-get install -y --no-install-recommends wget \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1001 nodejs \
    && useradd -u 1001 -g nodejs -m -s /usr/sbin/nologin nextjs
USER nextjs

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD wget -qO- http://127.0.0.1:3000/welcome >/dev/null 2>&1 || exit 1

CMD ["node", "server.js"]
