#!/usr/bin/env bash
# Paste-and-go alpha deploy.
#
# Run from the repo root after `fly auth login` + `vercel login`. Idempotent:
# re-running picks up where you left off (existing fly app, existing Vercel
# project, existing secrets).
#
# Production split (locked in v2.1-D4.4):
#   - cloud.openmagi.ai → Vercel (web/)
#   - api.openmagi.ai   → fly.io (INTERIM — migrate to K8s when reachable)
#
# K8s migration: see docs/operator.md §1a; current fly.io deploy is interim
# until the prod cluster is reachable + helm is installed locally.

set -euo pipefail

# ── config ──────────────────────────────────────────────────────────
FLY_APP="${FLY_APP:-magi-cp}"
FLY_REGION="${FLY_REGION:-nrt}"
VERCEL_PROJECT="${VERCEL_PROJECT:-magi-cp-dashboard}"
API_HOST="${API_HOST:-api.openmagi.ai}"
DASH_HOST="${DASH_HOST:-cloud.openmagi.ai}"

# ── helpers ─────────────────────────────────────────────────────────
step()  { printf "\n\033[1;34m━━━ %s\033[0m\n" "$1"; }
ok()    { printf "\033[1;32m✓\033[0m %s\n" "$1"; }
warn()  { printf "\033[1;33m!\033[0m %s\n" "$1" >&2; }
fail()  { printf "\033[1;31m✗\033[0m %s\n" "$1" >&2; exit 1; }
ask()   { printf "\033[1;36m?\033[0m %s " "$1"; read -r ans; printf '%s' "$ans"; }

[ -f pyproject.toml ] && [ -d web ] || \
  fail "run me from the magi-control-plane repo root"

# Phase 0 — auth sanity check
step "Phase 0 — auth sanity"
fly auth whoami >/dev/null 2>&1 || fail "fly not authed — run: fly auth login"
vercel whoami   >/dev/null 2>&1 || fail "vercel not authed — run: vercel login"
ok "fly: $(fly auth whoami)"
ok "vercel: $(vercel whoami)"

# Phase 1 — generate or load secrets
step "Phase 1 — generate secrets (once; cached at .deploy/secrets.env)"
SECRETS_FILE=".deploy/secrets.env"
mkdir -p .deploy
chmod 0700 .deploy
if [ -s "$SECRETS_FILE" ]; then
  ok "reusing cached $SECRETS_FILE"
else
  cat > "$SECRETS_FILE" <<EOF
MAGI_CP_API_KEY=$(uuidgen | tr 'A-Z' 'a-z')
MAGI_CP_HITL_API_KEY=$(uuidgen | tr 'A-Z' 'a-z')
MAGI_CP_ADMIN_API_KEY=$(uuidgen | tr 'A-Z' 'a-z')
MAGI_CP_ADMIN_HMAC_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
EOF
  chmod 0600 "$SECRETS_FILE"
  ok "wrote $SECRETS_FILE (0600); add LLM keys manually below"
  warn "ANTHROPIC_API_KEY / OPENAI_API_KEY NOT auto-generated — append them:"
  echo "    \$EDITOR $SECRETS_FILE   # add ANTHROPIC_API_KEY=… and OPENAI_API_KEY=…"
  ans=$(ask "Have you appended both keys to $SECRETS_FILE? [yes/no]:")
  [ "$ans" = "yes" ] || fail "stopping — re-run when LLM keys added"
fi
# shellcheck disable=SC1090
set -a; . "$SECRETS_FILE"; set +a

# Phase 2 — fly.io backend
step "Phase 2 — fly.io backend ($FLY_APP, region=$FLY_REGION)"
cd deploy
if ! fly apps list 2>/dev/null | grep -q "^$FLY_APP\b"; then
  fly launch --copy-config --no-deploy --name "$FLY_APP" --region "$FLY_REGION" --yes
  ok "created fly app $FLY_APP"
else
  ok "fly app $FLY_APP already exists"
fi

# Sync ALL secrets in one batch (idempotent — fly secrets set overwrites).
fly secrets set --app "$FLY_APP" --stage \
  MAGI_CP_API_KEY="$MAGI_CP_API_KEY" \
  MAGI_CP_HITL_API_KEY="$MAGI_CP_HITL_API_KEY" \
  MAGI_CP_ADMIN_API_KEY="$MAGI_CP_ADMIN_API_KEY" \
  MAGI_CP_ADMIN_HMAC_SECRET="$MAGI_CP_ADMIN_HMAC_SECRET" \
  ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY missing in $SECRETS_FILE}" \
  OPENAI_API_KEY="${OPENAI_API_KEY:?OPENAI_API_KEY missing in $SECRETS_FILE}" \
  >/dev/null
ok "staged secrets (applied on next deploy)"

# Volume for keypair + SQLite store.
if ! fly volumes list --app "$FLY_APP" 2>/dev/null | grep -q magi_data; then
  fly volumes create magi_data --app "$FLY_APP" --region "$FLY_REGION" --size 3 --yes
  ok "created volume magi_data (3GiB, $FLY_REGION)"
else
  ok "volume magi_data already exists"
fi

# Allocate dedicated IPs so DNS can A-record to them (shared IP needs SNI which
# breaks curl --resolve probing during cert issuance for some clients).
if ! fly ips list --app "$FLY_APP" 2>/dev/null | grep -q v4; then
  fly ips allocate-v4 --app "$FLY_APP"
  fly ips allocate-v6 --app "$FLY_APP"
fi
FLY_IPV4=$(fly ips list --app "$FLY_APP" -j 2>/dev/null \
  | python3 -c 'import json,sys;print([i["Address"] for i in json.load(sys.stdin) if i.get("Type")=="v4"][0])' || echo "")
FLY_IPV6=$(fly ips list --app "$FLY_APP" -j 2>/dev/null \
  | python3 -c 'import json,sys;print([i["Address"] for i in json.load(sys.stdin) if i.get("Type")=="v6"][0])' || echo "")
ok "fly ips: v4=$FLY_IPV4  v6=$FLY_IPV6"

fly deploy --app "$FLY_APP" --remote-only --yes
ok "fly deploy complete"
cd ..

# Phase 3 — DNS handoff (manual)
step "Phase 3 — DNS (manual, at your domain registrar)"
cat <<EOF
Add at your registrar (gabia/Cloudflare/Route53 — whichever you use):

  Type    Host    Value                          TTL
  A       api     $FLY_IPV4                      300
  AAAA    api     $FLY_IPV6                      300
  CNAME   cloud   cname.vercel-dns.com           300

Then wait ~5min for propagation. Verify:

  dig +short api.openmagi.ai          # should return $FLY_IPV4
  dig +short cloud.openmagi.ai        # should return a vercel-dns CNAME

EOF
ans=$(ask "DNS records added + propagated? [yes/no]:")
[ "$ans" = "yes" ] || { warn "stopping — re-run once DNS is live"; exit 0; }

# Phase 4 — fly.io cert
step "Phase 4 — Let's Encrypt for $API_HOST"
fly certs add --app "$FLY_APP" "$API_HOST" || true
fly certs show --app "$FLY_APP" "$API_HOST" | head -20
ok "cert add issued — fly polls LE every few seconds"

# Phase 5 — Vercel dashboard
step "Phase 5 — Vercel dashboard ($VERCEL_PROJECT)"
cd web
if [ ! -d .vercel ]; then
  vercel link --project "$VERCEL_PROJECT" --yes
  ok "linked Vercel project $VERCEL_PROJECT"
else
  ok ".vercel already linked"
fi

# Push env vars (idempotent — vercel env add fails on dup, ignore).
declare -A V_ENV
V_ENV[MAGI_CP_PUBLIC_SITE_URL]="https://$DASH_HOST"
V_ENV[MAGI_CP_PUBLIC_CLOUD_URL]="https://$API_HOST"
V_ENV[MAGI_CP_CLOUD_URL]="https://$API_HOST"
V_ENV[MAGI_CP_API_KEY]="$MAGI_CP_API_KEY"
V_ENV[MAGI_CP_HITL_API_KEY]="$MAGI_CP_HITL_API_KEY"
V_ENV[MAGI_CP_ADMIN_API_KEY]="$MAGI_CP_ADMIN_API_KEY"
V_ENV[MAGI_CP_ADMIN_HMAC_SECRET]="$MAGI_CP_ADMIN_HMAC_SECRET"
for k in "${!V_ENV[@]}"; do
  if vercel env ls production 2>/dev/null | grep -q "^$k\b"; then
    ok "env $k already set"
  else
    printf '%s' "${V_ENV[$k]}" | vercel env add "$k" production --yes
    ok "set env $k"
  fi
done

vercel --prod --yes
vercel domains add "$DASH_HOST" || true
cd ..

# Phase 6 — Smoke + sanity
step "Phase 6 — Smoke"
sleep 30
/usr/bin/curl -fsS "https://$API_HOST/healthz" && ok "$API_HOST/healthz 200" \
  || warn "$API_HOST not yet healthy (cert may still be propagating)"
/usr/bin/curl -fsSI "https://$DASH_HOST/welcome" 2>/dev/null | head -1 \
  && ok "$DASH_HOST/welcome reachable" \
  || warn "$DASH_HOST not yet reachable (DNS / Vercel domain may still be propagating)"

cat <<EOF

\033[1;32m✓ Alpha deploy complete.\033[0m

Next steps:
  1) Open https://$DASH_HOST/admin/signups   (use $MAGI_CP_ADMIN_API_KEY → MAGI_CP_ADMIN_API_KEY env)
  2) Submit a self-signup at https://$DASH_HOST/signup to test the triage flow
  3) Run the install one-liner against your own machine:
       curl -fsSL https://$DASH_HOST/install.sh | bash -s -- <your-mcp-key>
  4) When K8s reachable, migrate the backend per docs/operator.md §1a.
     Until then $API_HOST stays on fly.io interim.

Cached secrets at .deploy/secrets.env — do NOT commit this file.

EOF
