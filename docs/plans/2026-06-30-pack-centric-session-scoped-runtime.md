# Pack-centric, session-scoped policy runtime

Status: **DESIGN — not yet building**
Author: Kevin
Date: 2026-06-30

## Motivation

Today an operator manages Magi by walking to the dashboard and toggling
individual policies on and off before every kind of work. That reads as
noisy control-plane maintenance rather than "governance-as-code":

- To do a research session, flip six citation / allowlist / injection
  policies on, do work, flip them off.
- To do a coding session, flip a different overlapping set on, do work,
  flip them off.
- Anything you forgot to disable last time still fires.

The mental model is inverted. What the operator actually wants is: "for
THIS session I am doing research; run the research guardrails until I
tell you otherwise." Policies-as-atoms is the wrong grain.

## Proposed model in one sentence

Policies live inside packs. Packs are session-scoped intents that the
operator activates from Claude Code with a slash command
(`/magi:pack:research-mode`); the gate looks up which pack the current
session activated and only evaluates that pack's policies.

## Model shift

|                | Current                                | Proposed                                                  |
| -------------- | -------------------------------------- | --------------------------------------------------------- |
| Toggle grain   | Per-policy `enabled` bit               | Per-session `active_packs`                                |
| Where          | Dashboard `/rules`                     | Claude Code slash command                                 |
| Persistence    | Global, until re-toggled               | Session-scoped, until `/magi:pack:off` or session end     |
| Dashboard role | Toggle switchboard                     | Pack authoring + preview                                  |
| Policy grain   | Free-floating, has an `enabled` bit    | Belongs to at least one pack; carries no enable state     |
| Safety net     | Anything with `enabled=true` fires     | An always-on "floor pack" that never needs activation     |

## Runtime changes

### Gate resolution

Every hook call already carries CC's `sessionId`. Today the gate loads
"all enabled policies for the tenant, evaluate all that match the hook."
The new resolution:

1. Look up the session's active pack set (from a session-state store —
   see below).
2. Union in the tenant's floor pack.
3. Load every policy that belongs to any pack in that set AND matches
   this hook (event + matcher).
4. Evaluate as today.

If the session has no active pack, only the floor pack's policies fire.
That is the safety net: a session with no active pack is not the same as
"no governance"; it means "just the floor."

### Session-state store

New durable table (or hash) on the cloud side:

```
session_active_packs {
  session_id: str          (CC session id, uuidv4)
  tenant_id: str
  pack_ids: list[str]      (active pack ids for this session)
  activated_at: timestamp
  last_seen_at: timestamp  (refreshed on every hook resolution)
  expires_at: timestamp    (default now + 24h; extended on refresh)
}
```

- Keyed by `(tenant_id, session_id)`.
- Read at every hook resolution. Hot path — cache locally in the gate
  binary for the lifetime of a session (5 min TTL) so we do not add a
  cloud round-trip to every tool call.
- Write only on `/magi:pack:*` slash commands + gate-driven refresh.

### Slash-command surface

Claude Code custom slash commands are just markdown files under
`~/.claude/commands/`. The installer already writes managed-settings +
`magi-gate.sh`. Add a `~/.claude/commands/magi/` directory with:

```
~/.claude/commands/magi/
  pack.md         # /magi:pack — usage help
  pack-activate.md
  pack-deactivate.md
  pack-status.md
```

The command bodies shell out to the same gate binary with a
`pack activate <id>` sub-command, which POSTs to the cloud's session-
state endpoint. The cloud stores `(session_id, pack_id)` in
`session_active_packs`.

Endpoints (new):
- `POST /session/{session_id}/packs/activate  {pack_id}`
- `POST /session/{session_id}/packs/deactivate {pack_id}`
- `GET  /session/{session_id}/packs` → currently active list

Auth: same key path the gate already uses. No new key rotation.

### Floor pack

Every tenant gets a `floor` pack seeded on tenant creation. It is:
- Always evaluated regardless of session activation.
- Editable in the dashboard like any other pack.
- Cannot be deleted.

This preserves the "some policies I NEVER want to bypass" behaviour that
today's per-policy `enabled=true` gives, without pinning it to a global
toggle.

## UX changes

### `/rules` tab

Options:
- **A. Remove entirely.** The tab exists today for a workflow that no
  longer exists in the new model.
- **B. Keep as a read-only preview.** "Here are all policies; here is
  which pack each belongs to; here is what fires on which hook." Useful
  for auditing coverage.

Recommendation: B. Auditing which packs cover which hooks is exactly the
kind of question a compliance operator asks; making them derive it from
Pack cards is worse.

### `/packs` tab (becomes primary)

- Pack list (already shipped in D75).
- Per-pack detail: list of member policies + per-pack `enabled_in_pack`
  toggle (this replaces today's per-policy `enabled` bit — the policy
  can be turned off for THIS pack while remaining on for another).
- "Add policy" flow: pack is required; policies without a pack are not
  possible.
- Floor pack rendered first with a subtle "ALWAYS-ON" badge and no
  activation controls.

### `/sessions` tab (new)

- List of active CC sessions for this tenant.
- Per session: session id (truncated), which packs are active, last
  activity, remaining TTL, "force deactivate" button.
- Useful when the operator wants to know "did anyone leave the strict
  block pack turned off yesterday?"

### `/policies/new`

- Add a required `pack` selector on save.
- Prebuilts become pack templates: enabling a prebuilt = adding its
  target policies to one of your packs.

## Slash command UX inside Claude Code

```
> /magi:pack:activate research-mode
✓ research-mode active for this session. 12 policies will fire on
  matching hooks. Deactivate with /magi:pack:deactivate research-mode.

> /magi:pack:status
Active packs (session sess_9a3f):
  - floor           (always on, 3 policies)
  - research-mode   (activated 2m ago, 12 policies)

> /magi:pack:deactivate research-mode
✓ research-mode deactivated. Floor pack remains.
```

Slash commands are declarative — they invoke the gate binary and the
gate posts the state change to the cloud. The gate does NOT need to
short-circuit any tool call; the next hook resolution reads the fresh
state.

## Migration

For an existing tenant:

1. Seed the floor pack empty on migration.
2. Move every policy currently `enabled=true` into the floor pack.
   - This preserves current behaviour: everything that fired yesterday
     still fires today.
3. Drop the per-policy `enabled` column (or ignore it — leaves migration
   reversible).
4. Show the operator a one-time migration banner: "We moved your enabled
   policies into a floor pack. Consider splitting them into
   session-scoped packs (research-mode, coding-safety, etc.) so you can
   activate them per session."

## Decisions locked (2026-06-30)

Kevin walked the open-question list; decisions below.

1. **Multiple active packs per session — YES.** Union of policies.
   Ordering: floor first, then activation order.
2. **Subagent inheritance — YES.** Spawned subagent inherits the
   parent session's active packs.
3. **CC-restart persistence — YES.** Activation survives CC restart
   as long as the session id survives. If CC drops the session id on
   restart, the activation is effectively lost (fresh session id has
   no state); we add a "sticky pack" (per-user, per-project default)
   that auto-reactivates on the next session boot to close the gap.
4. **Slash command distribution — A (installer files) for beta.**
   Migrate to MCP-exposed commands if we iterate content.
5. **Activation lifetime.** ONE-SHOT activate; persists **until the
   session ends OR the operator runs `/magi:pack:deactivate`.**
   No auto-expire, no TTL. The `expires_at` field in the store is
   there only for garbage collection of orphaned sessions (a session
   that stopped talking to us for N days).
   Distinct from **gate cache refresh** (an implementation detail):
   the gate binary invalidates its local cache whenever the operator
   runs `/magi:pack:*` and refetches once per session boot.
6. **Floor pack ships empty.** Migration populates it for existing
   tenants (moves everything currently `enabled=true` into floor).
7. **Floor pack cannot be deactivated.** The pack is editable
   (add/remove policies) but the "always-on" bit is server-locked.
8. **Air-gapped self-host — deferred.** Not addressed in the beta.
   For context: an "air-gapped" install runs the gate without a
   cloud (no `magi-cp cloud` process), which today's stack does not
   support anyway (the gate always talks to a cloud). Everyone in
   the beta runs cloud + gate on the same machine via docker
   compose, so the cloud is always reachable. Revisit only if a
   real air-gapped deploy request lands.

All eight decisions unblock Phase 1. Phases 3-5 will surface
implementation questions of their own — those get their own
decision blocks below when we get there.

## Phased rollout

- **Phase 1 — data model + endpoints.** Add `packs.floor` seed, add
  `session_active_packs` table, add the three session endpoints.
  Keep the old `enabled` bit working. No UX change yet.
- **Phase 2 — gate resolution shift.** Hook resolution reads
  `session_active_packs` + floor. Existing `enabled` bit ignored on
  new evaluation path. Feature-flagged
  `MAGI_CP_PACK_CENTRIC_RUNTIME`; both modes coexist.
- **Phase 3 — slash commands + gate CLI.** Install
  `~/.claude/commands/magi/`; add `magi-cp session pack activate/...`
  sub-commands. Manual test on our own CC session.
- **Phase 4 — dashboard restructure.** `/packs` becomes primary,
  `/rules` becomes read-only preview, `/sessions` ships. Per-policy
  toggle removed.
- **Phase 5 — migration + flip default.** Auto-migrate existing
  enabled policies into the floor pack. Set
  `MAGI_CP_PACK_CENTRIC_RUNTIME=1` as default. Deprecate the
  per-policy `enabled` column in a later release.

## Trade-offs summary

**Wins**
- Session-scoped activation matches how operators actually think about
  work sessions.
- Policies stop being global switches; they belong to intents.
- The floor pack gives a clean answer to "what always fires?"
- Slash commands make activation part of the conversation, not a
  detour to the dashboard.

**Costs**
- Runtime model change touches gate resolution, cloud store, and
  dashboard.
- Session identity edges (CC restart, subagent spawn, terminal close)
  need explicit answers before Phase 3.
- Dashboard IA changes (Rules tab retirement, Sessions tab birth) —
  every existing operator has to re-learn where to go.
- Adds a cloud round-trip if the local cache is stale. Have to profile.

## What to decide before writing code

- Yes/no on multiple active packs per session.
- Yes/no on subagent inheritance.
- Slash command distribution: installer files vs MCP server.
- What the floor pack ships with (empty vs curated defaults).
- Whether to keep `/rules` as read-only preview or delete it.
