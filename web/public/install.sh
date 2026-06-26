#!/usr/bin/env bash
# magi-control-plane quickstart installer (self-host).
#
# One command. Pulls the public docker images from GHCR, generates a
# .env with random keys, brings up the control plane + dashboard in
# docker, and wires Claude Code's PreToolUse hook to it.
#
#   curl -fsSL https://cp.openmagi.ai/install.sh | bash
#
# What it does:
#   * Check Docker + Docker Compose v2 are installed
#   * Pick free host ports (default 3000 + 8787, auto-bump on conflict)
#   * Download docker-compose.yml + magi-gate.sh from cp.openmagi.ai
#   * Generate ~/.magi/control-plane/.env with random keys (idempotent)
#   * docker compose up -d  (pulls magi-cp + magi-cp-dashboard images)
#   * Wait for /healthz on the cloud
#   * Drop ~/.claude/managed-settings.json + ~/.local/bin/magi-gate.sh
#   * Persist key + URL to ~/.config/magi-cp/env (0600)
#   * Source the env from ~/.zshrc and ~/.bashrc
#
# Re-running is idempotent: existing .env keys are preserved, compose
# just up's, ports already in use stay assigned.

set -euo pipefail

step()   { printf "\033[1;34m→\033[0m %s\n" "$1"; }
ok()     { printf "  \033[1;32m✓\033[0m %s\n" "$1"; }
warn()   { printf "\033[1;33m!\033[0m %s\n" "$1" >&2; }
fail()   { printf "\033[1;31m✗\033[0m %s\n" "$1" >&2; exit 1; }
banner() { printf "\033[1;36m▸\033[0m %s\n" "$1"; }

SITE_URL="${MAGI_CP_SITE_URL:-https://cp.openmagi.ai}"
INSTALL_DIR="${MAGI_CP_INSTALL_DIR:-$HOME/.magi/control-plane}"

echo ""
banner "Open Magi · Control Plane installer (self-host)"
echo ""

# ── docker check ────────────────────────────────────────────────────────
# Platform-aware install hint when Docker is missing. We DON'T auto-install
# Docker (heavy, requires admin, distro-specific). Instead we print the
# one-liner the user can paste, then exit cleanly so they can re-run this
# installer after Docker is up.
detect_docker_install_hint() {
  local os="$(uname -s)"
  local arch="$(uname -m)"
  case "$os" in
    Darwin)
      printf "  Install on macOS (%s):\n" "$arch"
      printf "    brew install --cask docker         # easiest, then launch Docker.app\n"
      printf "    # or download Docker Desktop:        https://www.docker.com/products/docker-desktop\n"
      ;;
    Linux)
      if [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        printf "  Install on Linux (%s %s):\n" "${ID:-linux}" "${VERSION_ID:-}"
      else
        printf "  Install on Linux:\n"
      fi
      printf "    curl -fsSL https://get.docker.com | sh        # official one-liner, works on most distros\n"
      printf "    sudo usermod -aG docker \$USER && newgrp docker\n"
      ;;
    *)
      printf "  Install:\n"
      printf "    https://www.docker.com/products/docker-desktop\n"
      ;;
  esac
  printf "\n  Then re-run:\n"
  printf "    curl -fsSL %s/install.sh | bash\n" "$SITE_URL"
}

step "Checking Docker"
if ! command -v docker >/dev/null 2>&1; then
  printf "\033[1;31m✗\033[0m Docker not found.\n\n" >&2
  detect_docker_install_hint >&2
  printf "\n" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  printf "\033[1;31m✗\033[0m Docker Compose v2 not found.\n" >&2
  printf "  Docker is installed but the compose plugin isn't.\n" >&2
  printf "  On Docker Desktop 20+ it ships built-in (update Docker Desktop).\n" >&2
  printf "  On Linux: sudo apt-get install docker-compose-plugin  (or your distro equivalent).\n\n" >&2
  exit 1
fi
DOCKER_VER=$(docker --version | awk '{print $3}' | tr -d ,)
COMPOSE_VER=$(docker compose version --short)
ok "docker $DOCKER_VER  +  compose $COMPOSE_VER"

# ── port pickup ─────────────────────────────────────────────────────────
# Returns the first free port at or after $1, scanning up to $1+50.
pick_port() {
  local default="$1"
  local p="$default"
  local max=$(( default + 50 ))
  while [ "$p" -le "$max" ]; do
    if command -v lsof >/dev/null 2>&1; then
      lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1 || { echo "$p"; return 0; }
    else
      # bash builtin /dev/tcp fallback
      (echo > "/dev/tcp/127.0.0.1/$p") >/dev/null 2>&1 || { echo "$p"; return 0; }
    fi
    p=$(( p + 1 ))
  done
  return 1
}

mkdir -p "$INSTALL_DIR"
ENV_FILE="$INSTALL_DIR/.env"

# Reuse previously-assigned ports if present.
PREV_DASH=""; PREV_CLOUD=""
if [ -f "$ENV_FILE" ]; then
  PREV_DASH=$(grep '^DASHBOARD_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)
  PREV_CLOUD=$(grep '^CLOUD_PORT='     "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)
fi

step "Picking host ports"
DASH_PORT="${MAGI_CP_DASH_PORT:-${PREV_DASH:-$(pick_port 3000 || echo "")}}"
CLOUD_PORT="${MAGI_CP_CLOUD_PORT:-${PREV_CLOUD:-$(pick_port 8787 || echo "")}}"
[ -n "$DASH_PORT" ]  || fail "no free port near 3000. Set MAGI_CP_DASH_PORT=… explicitly."
[ -n "$CLOUD_PORT" ] || fail "no free port near 8787. Set MAGI_CP_CLOUD_PORT=… explicitly."
ok "dashboard → :$DASH_PORT  ·  cloud → :$CLOUD_PORT"

# ── docker-compose.yml: never overwrite once it exists ────────────────────
# Re-runs of this installer preserve a user-customized compose file. The
# compose template uses `env_file: .env` so adding KEY=VALUE pairs to .env
# is enough for any new env to reach the containers; users do not need to
# edit this file. If they DO edit it (port pinning, host network mode,
# extra services), the installer respects their changes.
if [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
  step "Preserving existing $INSTALL_DIR/docker-compose.yml"
  ok "compose file kept (delete it to fetch the latest template on next run)"
else
  step "Downloading docker-compose.yml from $SITE_URL"
  curl -fsSL "$SITE_URL/self-host/docker-compose.yml" \
    -o "$INSTALL_DIR/docker-compose.yml" \
    || fail "could not fetch docker-compose.yml from $SITE_URL"
  ok "wrote $INSTALL_DIR/docker-compose.yml"
fi

# ── .env generation (idempotent) ────────────────────────────────────────
if [ -f "$ENV_FILE" ] && grep -q '^MAGI_CP_API_KEY=' "$ENV_FILE"; then
  step "Reusing existing $ENV_FILE"
  LOCAL_KEY=$(grep '^MAGI_CP_API_KEY=' "$ENV_FILE" | cut -d= -f2-)
  # Make sure the ports we picked are written even on re-run.
  TMP=$(mktemp)
  grep -vE '^(DASHBOARD_PORT|CLOUD_PORT)=' "$ENV_FILE" > "$TMP" || true
  {
    cat "$TMP"
    echo "DASHBOARD_PORT=$DASH_PORT"
    echo "CLOUD_PORT=$CLOUD_PORT"
  } > "$ENV_FILE"
  rm -f "$TMP"
  chmod 0600 "$ENV_FILE"
  ok "key preserved, ports updated"
else
  step "Generating $ENV_FILE with random keys"
  command -v openssl >/dev/null 2>&1 \
    || fail "openssl not found (needed for key generation). Install via 'brew install openssl' or your package manager."
  LOCAL_KEY="mcp_$(openssl rand -hex 24)"
  HITL_KEY="hitl_$(openssl rand -hex 24)"
  ADMIN_KEY="adm_$(openssl rand -hex 24)"
  HMAC_SECRET=$(openssl rand -hex 32)
  cat > "$ENV_FILE" <<EOF
# Auto-generated by magi-cp installer.
# Edit this file to add any KEY=VALUE pair; it auto-flows into the cloud +
# dashboard containers via env_file in docker-compose.yml. The installer
# never overwrites this file on re-run.
MAGI_CP_HITL_API_KEY=$HITL_KEY
MAGI_CP_API_KEY=$LOCAL_KEY
MAGI_CP_ADMIN_API_KEY=$ADMIN_KEY
MAGI_CP_ADMIN_HMAC_SECRET=$HMAC_SECRET
DASHBOARD_PORT=$DASH_PORT
CLOUD_PORT=$CLOUD_PORT

# ── Optional: LLM provider keys ───────────────────────────────────────
# Uncomment and fill in to enable conversational compile + LLM-critic
# review on /policies/compile. Drop in just ANTHROPIC_API_KEY and the
# anthropic compiler activates; add OPENAI_API_KEY to also activate the
# openai reviewer (recommended over same-model self-review).
#
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
#
# Override the provider factory paths if you want a different pair:
# MAGI_CP_LLM_COMPILER=magi_cp.llm.anthropic_provider:anthropic_default
# MAGI_CP_LLM_REVIEWER=magi_cp.llm.openai_provider:openai_default
EOF
  chmod 0600 "$ENV_FILE"
  ok "wrote .env (0600)"
fi

# ── compose up + health wait ────────────────────────────────────────────
step "Pulling latest images (magi-cp + magi-cp-dashboard)"
(cd "$INSTALL_DIR" && docker compose pull >/tmp/magi-pull.log 2>&1) \
  || { tail -30 /tmp/magi-pull.log >&2; fail "docker compose pull failed. See /tmp/magi-pull.log"; }
ok "pulled"

step "docker compose up -d  (recreates containers on image change)"
# --force-recreate so a re-run after `docker compose pull` actually
# swaps the running containers over to the new image. Without it,
# compose sees the old containers as already-up and leaves them alone
# even when the image digest changed.
(cd "$INSTALL_DIR" && docker compose up -d --force-recreate >/tmp/magi-compose.log 2>&1) \
  || { tail -30 /tmp/magi-compose.log >&2; fail "docker compose up failed. See /tmp/magi-compose.log"; }
ok "compose up"

step "Waiting for /healthz at http://localhost:$CLOUD_PORT"
WAIT_T0=$(date +%s)
HEALTHY=0
for i in $(seq 1 90); do
  if curl -fsS "http://localhost:$CLOUD_PORT/healthz" >/dev/null 2>&1; then
    WAIT_DT=$(( $(date +%s) - WAIT_T0 ))
    ok "healthy after ${WAIT_DT}s"
    HEALTHY=1
    break
  fi
  sleep 1
done
[ "$HEALTHY" = "1" ] || fail "control plane did not become healthy in 90s. Check 'cd $INSTALL_DIR && docker compose logs cloud'."

# ── wait for dashboard too ─────────────────────────────────────────────
# The dashboard (Next.js) container serves /api/downloads/managed-settings
# and /api/downloads/gate-binary. We fetched the dashboard image already
# via compose; wait until it's accepting requests too. Healthcheck runs
# every 15s with start_period=30s, so 60s total is comfortable.
step "Waiting for dashboard at http://localhost:$DASH_PORT"
DASH_T0=$(date +%s)
DASH_OK=0
for i in $(seq 1 60); do
  if curl -fsS "http://localhost:$DASH_PORT/welcome" >/dev/null 2>&1; then
    DASH_DT=$(( $(date +%s) - DASH_T0 ))
    ok "dashboard ready after ${DASH_DT}s"
    DASH_OK=1
    break
  fi
  sleep 1
done
[ "$DASH_OK" = "1" ] || fail "dashboard did not become ready in 60s. Check 'cd $INSTALL_DIR && docker compose logs dashboard'."

# ── claude code wiring ─────────────────────────────────────────────────
API_URL="http://localhost:$CLOUD_PORT"
# Downloads (managed-settings.json + magi-gate.sh) are dashboard routes
# (Next.js /api/downloads/*), not cloud routes — fetch from the
# dashboard port. The gate's runtime traffic still hits the cloud.
DOWNLOADS_URL="http://localhost:$DASH_PORT"

step "Wiring Claude Code (managed-settings.json + magi-gate.sh)"
CLAUDE_DIR="$HOME/.claude"
LBIN="$HOME/.local/bin"
mkdir -p "$CLAUDE_DIR" "$LBIN"

curl -fsSL "$DOWNLOADS_URL/api/downloads/managed-settings" \
  -o "$CLAUDE_DIR/managed-settings.json" \
  || fail "could not fetch managed-settings.json from $DOWNLOADS_URL"
curl -fsSL "$DOWNLOADS_URL/api/downloads/gate-binary" \
  -o "$LBIN/magi-gate.sh" \
  || fail "could not fetch magi-gate.sh from $DOWNLOADS_URL"
chmod 0755 "$LBIN/magi-gate.sh"

case ":$PATH:" in
  *":$LBIN:"*) ;;
  *) warn "$LBIN is not on PATH. Add 'export PATH=\$HOME/.local/bin:\$PATH' to your shell rc." ;;
esac

# Rewrite managed-settings to use per-user path + local cloud URL.
PY=""
for cand in python3.13 python3.12 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    PY="$cand"; break
  fi
done
if [ -n "$PY" ]; then
  "$PY" - "$CLAUDE_DIR/managed-settings.json" "$LBIN/magi-gate.sh" "$API_URL" <<'PY'
import json, sys
p = sys.argv[1]
data = json.load(open(p))
for hooks in data.get("hooks", {}).values():
    for block in hooks:
        for h in block.get("hooks", []):
            if h.get("type") == "command":
                h["command"] = sys.argv[2]
                env = h.setdefault("env", {})
                env["MAGI_CP_CLOUD_URL"] = sys.argv[3]
open(p, "w").write(json.dumps(data, indent=2, sort_keys=True) + "\n")
PY
else
  warn "python3 not found. managed-settings.json was downloaded as-is; you may need to set the command path by hand."
fi
ok "rewrote managed-settings → cloud=$API_URL"

# ── persist key + url ───────────────────────────────────────────────────
step "Persisting key + cloud URL → ~/.config/magi-cp/env"
ENV_DIR="$HOME/.config/magi-cp"
mkdir -p "$ENV_DIR"
chmod 0700 "$ENV_DIR"
cat > "$ENV_DIR/env" <<EOF
# Auto-generated by magi-cp installer. Do not edit unless you know what you're doing.
MAGI_CP_API_KEY=$LOCAL_KEY
MAGI_CP_CLOUD_URL=$API_URL
EOF
chmod 0600 "$ENV_DIR/env"
ok "saved $ENV_DIR/env (0600)"

for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
  [ -f "$rc" ] || continue
  if ! grep -q "magi-cp/env" "$rc" 2>/dev/null; then
    printf '\n# magi-cp\n[ -f "$HOME/.config/magi-cp/env" ] && set -a && . "$HOME/.config/magi-cp/env" && set +a\n' >> "$rc"
    ok "appended sourcing line to $rc"
  fi
done

# ── done ────────────────────────────────────────────────────────────────
printf "\n\033[1;32m✓ Install complete.\033[0m\n\n"

cat <<EOF
  Dashboard:  http://localhost:$DASH_PORT
  API:        http://localhost:$CLOUD_PORT
  Repo:       $INSTALL_DIR
  Env:        ~/.config/magi-cp/env (0600)

  Open the dashboard URL in your browser to start.

  Useful:
    Stop:   cd $INSTALL_DIR && docker compose down
    Logs:   cd $INSTALL_DIR && docker compose logs -f
    Pull:   cd $INSTALL_DIR && docker compose pull && docker compose up -d

EOF

# ── LLM activation banner ───────────────────────────────────────────────
# /policies/compile (conversational compile) returns "not turned on yet"
# until both ANTHROPIC_API_KEY and OPENAI_API_KEY are set in .env. Most
# operators want this on day one, so call out the activation path here.
if ! grep -qE '^ANTHROPIC_API_KEY=.+' "$ENV_FILE" 2>/dev/null; then
  printf "\033[1;33m! Conversational compile is off.\033[0m\n"
  printf "  To enable: edit %s and uncomment the LLM provider lines\n" "$ENV_FILE"
  printf "  (ANTHROPIC_API_KEY + OPENAI_API_KEY), then:\n"
  printf "    cd %s && docker compose up -d --force-recreate\n\n" "$INSTALL_DIR"
fi
