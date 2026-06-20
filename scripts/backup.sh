#!/usr/bin/env bash
# v2.0-W8d — magi-control-plane backup script.
#
# What's backed up:
#   1. Ed25519 keypair dir (MAGI_CP_KEY_DIR) — multi-key layout
#   2. Policies JSON store (MAGI_CP_POLICY_STORE)
#   3. Database — SQLite via sqlite3 .backup; Postgres via pg_dump
#
# Encryption: piped through `age` if an MAGI_CP_BACKUP_RECIPIENT is set.
# Otherwise the tarball is plaintext (operator may layer their own KMS).
#
# Output: <out_dir>/magi-cp-backup-<UTC ISO>.tar.gz[.age]
#
# Restore: untar, point MAGI_CP_KEY_DIR / POLICY_STORE / DSN at the restored
# files. The hash chain is preserved (we copy bytes), so verify_chain()
# returns true on first boot after restore.
set -euo pipefail

KEY_DIR="${MAGI_CP_KEY_DIR:-$HOME/.magi-cp/cloud}"
POLICY_STORE="${MAGI_CP_POLICY_STORE:-$HOME/.magi-cp/policies.json}"
DSN="${MAGI_CP_DSN:-sqlite:///./magi-cp.sqlite}"
OUT_DIR="${1:-./backups}"

mkdir -p "$OUT_DIR"
TS=$(date -u +%Y%m%dT%H%M%SZ)
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

# Keys
if [ -d "$KEY_DIR" ]; then
  cp -a "$KEY_DIR" "$STAGE/keys"
fi

# Policies
if [ -f "$POLICY_STORE" ]; then
  cp -a "$POLICY_STORE" "$STAGE/policies.json"
fi

# Database
case "$DSN" in
  sqlite*)
    DB_PATH=$(echo "$DSN" | sed -E 's#sqlite:/+##')
    if [ -f "$DB_PATH" ]; then
      # `.backup` produces a consistent snapshot even while writers are active.
      sqlite3 "$DB_PATH" ".backup '$STAGE/magi-cp.sqlite'"
    fi
    ;;
  postgresql*|postgres*)
    pg_dump --format=custom --file="$STAGE/magi-cp.dump" "$DSN"
    ;;
  *)
    echo "unsupported DSN scheme for backup: $DSN" >&2
    exit 2
    ;;
esac

# Manifest for restore diagnostics
cat > "$STAGE/MANIFEST" <<EOF
created_utc=$TS
key_dir=$KEY_DIR
policy_store=$POLICY_STORE
dsn_scheme=${DSN%%:*}
git_sha=$(git -C "$(dirname "$0")/.." rev-parse HEAD 2>/dev/null || echo unknown)
EOF

OUT="$OUT_DIR/magi-cp-backup-$TS.tar.gz"
tar -czf "$OUT" -C "$STAGE" .

if [ -n "${MAGI_CP_BACKUP_RECIPIENT:-}" ]; then
  if ! command -v age >/dev/null; then
    echo "MAGI_CP_BACKUP_RECIPIENT set but \`age\` is not installed" >&2
    exit 3
  fi
  age -r "$MAGI_CP_BACKUP_RECIPIENT" -o "$OUT.age" "$OUT"
  rm -f "$OUT"
  OUT="$OUT.age"
fi

echo "backup written: $OUT"
