# SessionStart auto pack-activation (design)

Status: **DESIGN.** New feature. Behind an opt-in env, additive.
Author: Kevin
Date: 2026-07-03

## 1. Why

The gate (PreToolUse hook) already enforces policy on every Claude Code
tool call today (confirmed live: a "requires an official primary source"
policy fired and forced a SEC-filing fetch). But the dashboard **Sessions**
list stays empty because it is populated ONLY from `session_active_packs`,
which is written ONLY by an explicit `POST /session/{id}/packs/activate`
(the `/magi:pack-activate` slash command). A local operator who never runs
that slash command sees "No active sessions" and thinks nothing works.

Goal: on a fresh Claude Code session, automatically activate a configured
set of packs so (a) each session shows up in the dashboard immediately and
(b) that pack's session-scoped policies apply without a manual slash command.

## 2. What exists (the plumbing is already there)

- `POST /session/{session_id}/packs/activate {pack_id}`: tenant-auth only
  (X-Api-Key), keyed on (session_id, tenant_id). `src/magi_cp/cloud/app.py`
  ~4448. Idempotent activate returns 200. The floor pack is always on.
- `gate.py` already reads `payload.get("session_id")` (line ~360) and
  already POSTs to the cloud with the API key (urlopen pattern, ~588). It
  routes on `payload.get("hook_event_name")` (~362), defaulting PreToolUse.
- managed-settings.json template (`web/app/api/downloads/managed-settings/
  route.ts`) currently defines ONLY a `PreToolUse` hook block.
- Session pack activation only matters under `MAGI_CP_PACK_CENTRIC_RUNTIME`
  (default-ON since P5). The dashboard Sessions page is itself gated on
  `isPackCentricEnabled()`.

## 3. Design

Three additive pieces:

### 3.1 SessionStart hook in managed-settings

Add a `SessionStart` hook block to the managed-settings template that runs
the SAME `magi-gate.sh` command (the installer already rewrites the command
path + `MAGI_CP_CLOUD_URL` env for every hook block, so no installer change
needed beyond the template gaining the block). Claude Code fires SessionStart
once per new/resumed/cleared session with a payload carrying `session_id`.

### 3.2 gate.py SessionStart handler

`gate.py` routes on `hook_event_name`. Add a branch: when
`hook_event_name == "SessionStart"`, read the configured auto-activate pack
list and POST each to `/session/{session_id}/packs/activate`. It is
best-effort and NON-BLOCKING (SessionStart must never fail a session):
- unknown/empty config -> no-op (just allow).
- each activate wrapped in try/except; a failure logs (stderr) and continues.
- always emits an allow verdict (SessionStart is observe/setup, never denies).
- de-dupe by session_id via the existing `_session_cache` so a resumed
  session does not re-POST every activate (activate is idempotent anyway,
  but skip the network cost when the session id is unchanged within a short
  window).

### 3.3 Config: which packs auto-activate

New env `MAGI_CP_AUTO_ACTIVATE_PACKS` (comma-separated pack ids), read by
gate.py from env / `~/.config/magi-cp/env` (same source as
`MAGI_CP_ENDPOINT_ID`). Empty/unset -> no auto-activation (pure opt-in;
the floor pack is already always-on regardless).

Installer: the setup wizard / install.sh can offer to seed
`MAGI_CP_AUTO_ACTIVATE_PACKS` with a sensible default (e.g. the "coding
safety" pack) into `~/.config/magi-cp/env`, OR leave it unset and document
the slash-command path. Default: **leave unset**, but the dashboard Setup
page surfaces a one-click "auto-activate this pack on every session" toggle
per pack that writes the env (a later PR; PR1 ships the mechanism).

## 4. Sequencing (PRs)

- **PR1 (mechanism):** managed-settings SessionStart block + gate.py
  SessionStart handler + `MAGI_CP_AUTO_ACTIVATE_PACKS` reader + tests
  (SessionStart with configured packs POSTs activate; unset = no-op; a
  failing activate does not fail the session). Default unset = zero behavior
  change for existing installs.
- **PR2 (setup UX):** dashboard Setup page per-pack "auto-activate on every
  session" toggle that writes the env; install.sh optional default seed.
- **PR3 (docs):** operator.md + install copy explaining auto-activation vs
  the manual `/magi:pack-activate` path.

## 5. Risks + guards

- **SessionStart must never break a session.** Handler is fail-open: any
  error -> allow, never deny, never non-zero exit that CC would surface.
- **No behavior change when unset.** `MAGI_CP_AUTO_ACTIVATE_PACKS` empty ->
  the SessionStart hook fires but does nothing (allow). Existing installs
  that only re-fetch managed-settings gain an inert SessionStart block.
- **Idempotent + de-duped.** activate is idempotent cloud-side; gate-side
  de-dupe by session id avoids re-POST on every SessionStart of a resumed
  session.
- **pack-centric flag.** Auto-activation is only meaningful under
  `MAGI_CP_PACK_CENTRIC_RUNTIME` (default-ON). With it off the activate
  endpoints still record the row but the runtime ignores it; harmless.

## 6. Non-goals

- Not changing the PreToolUse enforcement path (already works).
- Not auto-activating without explicit config (opt-in only; floor pack
  already covers always-on policy).
- Not a hosted/multi-tenant feature; this is the local single-operator
  "install and it just shows up" path.
