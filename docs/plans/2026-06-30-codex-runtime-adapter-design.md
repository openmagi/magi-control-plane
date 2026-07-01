# Codex CLI runtime adapter (Option A, thin adapter with HookRuntime trait)

Status: **DESIGN, not yet building. Blocked on pack-centric runtime (`wf_fcb4b457-72f`) landing on main.**
Author: Kevin
Date: 2026-06-30
Related design docs: `docs/plans/2026-06-30-pack-centric-session-scoped-runtime.md` (the runtime layer this adapter plugs into), `/tmp/codex-hook-research.md` (raw research report, snapshotted into Section 2 below so this doc is self-contained).

## 1. Motivation

Today Magi hooks exactly one coding agent: Claude Code. The gate binary
reads CC's hook JSON on stdin, evaluates policy against the pack-centric
runtime, and writes a CC-shaped verdict envelope. Everything downstream
(dashboard chips, evidence ledger, session activation) assumes CC as the
sole runtime.

OpenAI shipped Codex CLI 0.142.x with what third-party writers call
"almost a direct port of Claude Code's hooks." The event names, the
stdin JSON envelope, the exit-code semantics, and the
`hookSpecificOutput` shape are near-identical. Codex ships managed
config layers, `~/.codex/prompts/*.md` slash commands, and a Rust
`hook_runtime.rs` that dispatches on `hook_event_name` (see Section 2).

Two consequences:

- Kevin's belief that a fork is unnecessary is correct. A thin adapter
  can compile the existing Magi Policy IR to Codex's `requirements.toml`
  and reuse the same gate binary with a runtime dispatcher.
- The bigger structural win is what the adapter unlocks. Once the gate
  binary talks two runtimes, a third (Cursor Agent, Gemini CLI, or
  whatever Anthropic ships next) is one driver, not a rewrite. We
  therefore factor the driver behind a `HookRuntime` trait in the same
  commit that introduces the second driver, rather than defer it.

The pack-centric runtime workflow (`wf_fcb4b457-72f`) currently landing
on main is a prerequisite: it moves the gate from per-policy enable
bits to `session_active_packs`. The Codex adapter reuses that table
verbatim, keyed by `session_id + runtime`. Nothing in the adapter
duplicates activation state.

## 2. Research recap (self-contained snapshot of `/tmp/codex-hook-research.md`)

Kept here so this doc stays useful six months from now, when the tmp
report will not survive. The tmp file is the authoritative version
while it exists; this snapshot is a lossy but sufficient summary.

### 2.1 Version and evidence base

- Codex CLI release under study: **0.142.5** (per official changelog,
  2026-07-01). Hooks landed stable roughly at v0.117, `UserPromptSubmit`
  in PR #14626 (2026-03-18).
- Source of truth: `github.com/openai/codex`, Rust core at
  `codex-rs/core/`. Dispatch: `hook_runtime.rs`. Tool integration:
  `tools/registry.rs`. DeepWiki reference: `deepwiki.com/openai/codex/3.11-hooks-system`.
- Third-party parity analyses (Daniel Vaughan, Blake Crosley) call it
  a near-port of the CC surface.

### 2.2 Hook coverage matrix (from `/tmp/codex-hook-research.md` Section 2)

| Claude Code event      | Codex event      | Match       | Notes                                                                                                     |
| ---------------------- | ---------------- | ----------- | --------------------------------------------------------------------------------------------------------- |
| `PreToolUse`           | `PreToolUse`     | **Partial** | Only 4 tool classes emit (`Bash`, `unified_exec`, `apply_patch`, MCP). Read/planning tools silent-fail-open. `additionalContext` rejected. |
| `PostToolUse`          | `PostToolUse`    | Match       | `additionalContext` accepted. Post-hoc `block` supported (side effects already happened).                  |
| `PermissionRequest`    | `PermissionRequest` | Match    | `decision.behavior = allow / deny` nested shape. Different from PreToolUse's `permissionDecision`.        |
| `UserPromptSubmit`     | `UserPromptSubmit` | Match     | `additionalContext` + `decision: "block"` both supported.                                                  |
| `Stop`                 | `Stop`           | Match       | Turn-end event.                                                                                            |
| `SubagentStop`         | `SubagentStop`   | Match       | Fires on spawned agent stop.                                                                               |
| `SessionStart`         | `SessionStart`   | Match       | Startup, resume, clear, compact triggers. Observe-only for verdicts; context injection allowed.           |
| `SubagentStart`        | `SubagentStart`  | Match       | Synthetic/internal subagents do NOT expose user-configured lifecycle hooks per DeepWiki + source comments. |
| `PreCompact`           | `PreCompact`     | Match       | `continue: false` accepted.                                                                                |
| `PostCompact`          | `PostCompact`    | Match       |                                                                                                            |
| `SessionEnd`           | none             | **Gap**     | Codex has `Stop` (turn) but no documented session-end event.                                              |
| `TaskCreated` / `TaskCompleted` | none    | Gap         | CC-specific.                                                                                                |
| `Notification`         | none             | Gap         | No matching event.                                                                                         |
| Plan-mode events       | none             | Gap         | Codex has `/plan-mode` slash cmd but no hook fanout.                                                       |
| n/a                    | `AfterAgent`     | Codex-only  | Legacy, superseded by `SubagentStop`.                                                                     |

**Verdict fields on PreToolUse.** Codex supports
`hookSpecificOutput.permissionDecision ∈ {allow, deny, ask}`,
`permissionDecisionReason`, `updatedInput` (input rewrite), and the
legacy `{decision: "block", reason}`. That is the CC surface minus
`additionalContext`.

**Wire format.** Stdin JSON with `session_id`, `turn_id` (turn-scoped
events only), `cwd`, `hook_event_name`, `model`, `permission_mode`,
`transcript_path`, plus tool-specific `tool_name`, `tool_use_id`,
`tool_input`, `tool_response`, `matcher_aliases`. Universal
`continue: false` + `systemMessage` on every event.

### 2.3 Config layering (from Section 3 of the report)

Codex precedence (highest to lowest per docs):

1. **Managed.** `/etc/codex/requirements.toml` + `/etc/codex/managed_config.toml`
   on Linux/macOS. `%ProgramData%\OpenAI\Codex\requirements.toml` on
   Windows. macOS MDM preference domain `com.openai.codex` with
   base64-encoded `config_toml_base64` and `requirements_toml_base64`.
2. `--config key=value` CLI overrides (managed still wins).
3. Project config `.codex/config.toml` (loaded only if project is trusted).
4. Profile overlay `~/.codex/profile-name.config.toml` via `--profile`.
5. User config `~/.codex/config.toml`.

Hook definition site: either `hooks.json` (a CC drop-in shape) or inline
`[[hooks.<EventName>]]` + `[[hooks.<EventName>.hooks]]` TOML tables next
to each config layer. Both formats at one layer emit a warning but load
together.

`managed_hooks` are auto-trusted (bypass the `/hooks` interactive trust
prompt). Enforcement is filesystem or MDM, NOT signature-verified per
session start. This is weaker than Magi's server-signed CC managed
settings.

Runtime bypass: `/hooks` CLI subcommand lets a user disable individual
NON-managed hooks. Managed hooks are excluded. Docs are silent on
whether managed hooks can be de-registered mid-session by a privileged
in-process command. **Uncertain, live test item #1.**

Feature gate: hooks are behind `features.hooks = true` in some doc
references. Changelog treats them as stable. Adapter installs
`features.hooks = true` explicitly for safety.

### 2.4 Slash commands (Section 4)

Two authoring paths:

- **Custom prompts (deprecated per official docs).** `~/.codex/prompts/*.md`,
  top-level only, no subdirectories. YAML frontmatter with `description:`
  and `argument-hint:`. Args positional (`$1..$9`) or named
  (`$FILE`, invoked `KEY=value`). Invocation: `/prompts:name`.
- **Skills (successor).** Docs point to skills as the strategic path.
  Both explicit invocation (like slash commands) and implicit
  invocation (model auto-picks). Can live in the repo, trusted via a
  `/hooks`-adjacent trust flow.

Built-in slash commands present: `/feedback`, `/mcp`, `/plan-mode`,
`/review`, `/status`, `/hooks`, `/personality`, `/raw`, `/vim`, `/undo`.

Managed / org-forced slash commands or skills: **not documented**.
Filesystem ownership at provisioning time is the practical enforcement.

### 2.5 Session identity (Section 5)

- `session_id` on every hook payload, direct 1:1 map to
  `session_active_packs`.
- `turn_id` on turn-scoped events.
- `transcript_path` on every payload.
- `SubagentStart` fires with subagent context. `agents.max_threads`
  default 6, `agents.max_depth` default 1. Multi-agent tools
  (`spawn_agent`, `send_input`, `resume_agent`, `wait_agent`,
  `close_agent`) gated on `features.multi_agent = true`.
- Caveat: DeepWiki + `hook_runtime.rs` note synthetic/internal
  subagents do NOT expose user-configured lifecycle hooks. Magi's
  per-subagent pack activation therefore fires on user-triggered
  `spawn_agent` fan-outs but may not fire on Codex's internal reviewer
  / consolidator flows (`approvals_reviewer = auto_review`).
  **Uncertain, live test item #3.**

### 2.6 Architecture recommendation (Section 6, the pick)

**Option A: thin adapter.** Reuse the existing gate binary with a
runtime dispatcher on `hook_event_name` and the additional payload
shape hints. Emit `hooks.json` next to a `requirements.toml` with
`[hooks]` entries pointing at the gate. Handle four shim behaviors
(the gaps in Section 3 of this doc). Ship `/magi:*` as skills with a
compat prompts file for early adopters.

**Option B: formal `HookRuntime` trait with runtime-cc and
runtime-codex drivers.** Adds one week of scaffolding. Pays off the
moment a third runtime arrives with its own quirks.

**We pick A shipped through B's scaffolding.** The trait split lives
in the same commit that introduces the Codex driver. Neither driver
would exist without the other; refactoring later would touch the same
files anyway.

Option C (Codex-only fork) is off the table. Codex is deliberately
CC-compat by design; forking would waste OpenAI's port work.

### 2.7 Open questions (Section 7)

Eight open questions from the report. They map onto the live-test
checklist (Section 10 of this doc + the standalone
`2026-06-30-codex-live-test-checklist.md`). Which decisions each
question unblocks is tracked in Section 8.

## 3. Architecture

### 3.1 The `HookRuntime` trait

A single trait in `src/magi_cp/runtime/__init__.py` that every runtime
driver implements. This is the seam that decouples "what the runtime
speaks on stdin" from "what Magi enforces."

```python
class HookRuntime(Protocol):
    runtime_id: str  # "claude-code" or "codex"

    def parse_hook_payload(self, raw_stdin: bytes) -> HookEvent:
        """Runtime-specific stdin JSON -> canonical HookEvent."""

    def emit_verdict(self, verdict: Verdict) -> bytes:
        """Canonical Verdict -> runtime-specific stdout JSON."""

    def emit_managed_config(self, ir: list[AnyPolicy]) -> ManagedConfigBundle:
        """Policy IR -> managed config files for this runtime."""

    def coverage_report(self, ir: list[AnyPolicy]) -> CoverageReport:
        """Per-policy: does THIS runtime enforce it, downgrade, or skip?"""

    def default_install_paths(self) -> InstallPaths:
        """Where the installer drops managed config + slash commands."""
```

`HookEvent` and `Verdict` are the canonical shapes today's gate already
speaks internally. The refactor is: extract them from
`src/magi_cp/local/gate.py`, drop the CC-specific stdin parsing into
`runtime/cc.py`, and let `codex.py` do the same. No new invariants.

### 3.2 File layout

New tree under `src/magi_cp/runtime/`:

```
src/magi_cp/runtime/
  __init__.py          # re-exports HookRuntime + get_runtime()
  trait.py             # HookRuntime protocol + shared types
  cc.py                # Claude Code driver (factored out of gate.py)
  codex.py             # Codex driver
  detect.py            # runtime detection helper (env sniff)
```

The TOML emitter lives next to the existing managed-settings compiler,
NOT inside the runtime driver. Rationale: the compiler already owns
the policy-IR-to-native-surface translation for CC; Codex is another
target format, not a semantic transform.

```
src/magi_cp/policy/
  compiler.py                    # existing CC managed-settings emitter
  codex_toml_emitter.py          # NEW, sibling target format
```

`codex_toml_emitter.py` exports `compile_to_codex_requirements(policies)`
returning a `(requirements_toml, hooks_json_sidecar,
context_templates)` tuple, matching the shape of
`compile_to_managed_settings(policies)` today.

### 3.3 The CC-driver factoring is a same-commit refactor

The commit that introduces `HookRuntime` and adds `codex.py` ALSO
extracts `cc.py` from `src/magi_cp/local/gate.py`. This is deliberate:

- Without the refactor, `codex.py` would inherit an implicit
  dependency on `gate.py` internals and cement a coupling we do not
  want.
- With the refactor, both drivers stand at the same distance from the
  gate entry point, and adding a third runtime is a copy-of-cc.py
  starting shape rather than a copy-of-gate.py archaeology dig.
- The existing CC path stays byte-equivalent under a passthrough
  wrapper. All existing tests pass unchanged. This is the compat gate
  on the P1 phase.

### 3.4 Dispatcher

The gate entrypoint (`src/magi_cp/local/gate.py::main`) becomes:

```python
def main() -> int:
    raw = sys.stdin.buffer.read()
    runtime = detect_runtime(raw, env=os.environ)  # "cc" or "codex"
    driver = get_runtime(runtime)
    event = driver.parse_hook_payload(raw)
    verdict = evaluate(event)  # existing policy path, unchanged
    sys.stdout.buffer.write(driver.emit_verdict(verdict))
    return 0
```

Detection order:

1. Explicit `MAGI_CP_RUNTIME` env var. Set by managed configs so a
   sandbox never guesses wrong.
2. Presence of Codex-specific fields in the JSON envelope (e.g. Codex
   sends `matcher_aliases` and Codex-specific `hook_event_name`
   values; the report calls out several).
3. Presence of `CLAUDE_CODE_SESSION_ID` env var (CC sets this).
4. Fallback: CC. Preserves current behavior for any caller we did
   not think of.

The dispatcher is default-off gated on `MAGI_CP_CODEX_RUNTIME_ENABLED`
for P1/P2 phases (see Section 8). With the flag off, `detect_runtime`
always returns `"cc"` and the codex path is dead code.

## 4. Gap shims

Four gaps identified in the coverage matrix. Each shim is
implemented in `codex.py` (or its neighbor helpers) and is invisible
to the caller.

### 4.1 Shim A: PreToolUse tool-coverage silent-skip

**Gap.** Codex only fires `PreToolUse` for `Bash`, `unified_exec`,
`apply_patch`, and MCP tools. `list_dir`, `view_image`, `update_plan`,
`spawn_agents_on_csv`, `tool_search`, `tool_suggest`, planning tools,
and web search silently skip. Any Magi policy whose `matcher.tool_name`
targets one of these silent-skip tools would be a policy authored to
enforce something Codex never asks about, i.e. it would fire zero times
and give the operator a false sense of coverage. Ref: OpenAI issue
#20204 (open).

**Shim behavior.**

1. `codex.py::coverage_report(ir)` marks any policy whose PreToolUse
   matcher targets a Codex silent-skip tool with
   `coverage_status = "codex_silent_skip"` and
   `compat_downgrade = "PermissionRequest + PostToolUse audit"`.
2. The compiler adds a `PermissionRequest` fallback for each such
   policy: a permission hook on the same tool class that answers
   `ask` (Codex user is prompted), plus a `PostToolUse` audit hook on
   the same tool that emits evidence after the fact.
3. The audit is best-effort. Side effects have already happened by the
   time `PostToolUse` fires. Coverage report flags this policy with
   an amber "post-hoc only" chip so the dashboard can surface it.

**Pack-centric integration.** The pack-centric runtime is what makes
this survivable. Because policies live in packs and packs are
session-scoped, an operator running a research-mode pack does not
carry Codex silent-skip risk into a coding session. The coverage chip
lives on the PACK card in the dashboard, not scattered across every
policy row.

### 4.2 Shim B: PreToolUse `additionalContext` rejection

**Gap.** Codex hard-rejects `hookSpecificOutput.additionalContext` on
`PreToolUse`. Ref: OpenAI issue #19385 (open since 2026-04-24). CC
accepts it; Magi's `inject_context` verdict on `PreToolUse` produces it.
On Codex the hook fails hard rather than silently downgrading.

**Shim behavior.**

1. `codex.py::emit_verdict` intercepts any verdict whose
   `additionalContext` field is set on a `PreToolUse` event.
2. If the operator's policy declared `context_scope = "turn"`, the
   verdict is rewritten to emit `systemMessage` on the same event.
   Codex accepts `systemMessage` on every event; it is a strictly
   weaker channel (system message vs. structured tool-input context)
   but preserves the operator's intent.
3. If the operator's policy declared `context_scope = "session"`, the
   context is deferred to the next `UserPromptSubmit` in this session
   (Codex accepts `additionalContext` there). A tiny per-session
   deferred-context queue lives in `~/.magi-cp/state/<session_id>/
   pending_context.jsonl`. Drained on the next `UserPromptSubmit`
   hook call.
4. The coverage report marks the policy with
   `compat_downgrade = "system_message" | "deferred_to_prompt"`.

**Pack-centric integration.** The deferred queue is keyed by
`(session_id, runtime)`. Because pack activation is also
session-keyed, the queue's TTL matches the pack's session lifetime
without a second GC path.

### 4.3 Shim C: `SessionEnd` absence

**Gap.** Codex has `Stop` (turn-end) but no session-end event. Some
Magi policies use `SessionEnd` for evidence flush, ledger commit, and
sticky-pack deactivation. Ref: `/tmp/codex-hook-research.md` Section 2.

**Shim behavior.**

1. `codex.py::coverage_report` marks any `SessionEnd`-hosted policy
   with `coverage_status = "codex_no_session_end"`.
2. The shim registers a `Stop` hook that inspects `stop_hook_active`
   (docs suggest this signals "session is ending, not just this
   turn"). If true, treat as `SessionEnd`. If false, no-op.
3. A tenant-level cloud-side sweeper falls back for the case where
   `stop_hook_active` never fires: the cloud times out sessions whose
   `last_seen_at` is older than 30 minutes and dispatches a synthetic
   `SessionEnd` fanout to any evidence-emit policy tagged
   `require_session_end = true`.
4. The 30-minute value is tenant-configurable via a
   `codex_synthetic_session_end_ttl_seconds` policy-pack meta.

**Pack-centric integration.** The pack-centric `session_active_packs`
row already tracks `last_seen_at`, gets refreshed on every hook
resolution, and expires at 30d for GC. The synthetic `SessionEnd`
fanout is the same sweeper reused with a tighter TTL.

**Uncertainty.** Whether `stop_hook_active` is a reliable session-end
signal is not confirmed in docs. Marked live test item #2.

### 4.4 Shim D: subagent hook fanout gap on internal reviewers

**Gap.** DeepWiki + source note synthetic/internal subagents do not
expose user-configured lifecycle hooks. Codex `approvals_reviewer =
auto_review` fires an internal reviewer that would evade any
`SubagentStart`/`SubagentStop` policy targeting it. User-triggered
`spawn_agent` fanouts are covered; internal ones may not be.

**Shim behavior.**

1. `codex.py::coverage_report` marks any policy targeting subagents
   with `coverage_status = "codex_internal_subagent_gap"`.
2. Belt-and-suspenders: the compiler adds a parent-side `PreToolUse`
   hook on `spawn_agent` (which IS covered) that captures the intent
   and mirrors the policy check at spawn time. Post-hoc, a
   parent-side `PostToolUse` hook on the same tool captures the
   result. Together these cover the user-triggered path fully and
   partially observe the internal path (whichever tool the internal
   reviewer invokes will still hit `PreToolUse` on that tool, so
   coverage degrades but does not zero out).
3. If the operator explicitly opts in via
   `codex_synthetic_subagent_probe = true` on the pack, Magi also
   ships a tiny probe skill loaded at every session start that fires
   a synthetic `spawn_agent` on session boot to sanity-check the
   internal reviewer hook path is (or is not) firing. Off by default;
   noisy in the transcript.

**Uncertainty.** Whether ANY internal reviewer fires SubagentStart is
the exact question in live test item #3.

## 5. Slash-command shipping

Both authoring paths ship, prioritizing the forward-compat surface.

### 5.1 Skills (forward-compat, primary)

`~/.codex/skills/magi/` under a Magi namespace. Each skill is a
markdown file with the same body shape the CC slash-command adapter
uses today: front-matter declaring the CLI command to shell out to,
and the CLI writes the verdict-shaped stdout.

```
~/.codex/skills/magi/
  pack-activate.md
  pack-deactivate.md
  pack-status.md
  pack-sticky.md
```

Each body invokes `magi-cp session pack activate <arg>` (or the
corresponding subcommand from the pack-centric P3 CLI). No new CLI
logic; the pack-centric workflow already ships it.

**Trust boundary.** Codex trusts skills via a `/hooks`-adjacent flow
(see Section 2). The installer drops these skills into
`~/.codex/skills/magi/` at install time, matching how the CC installer
drops `~/.claude/commands/magi/`. Trust prompt fires on first use
unless the tenant is provisioned with a managed config that whitelists
the Magi skill namespace (see Section 6).

### 5.2 Prompts (deprecated but works today)

`~/.codex/prompts/magi:pack:activate.md` etc. Ship these in parallel
to the skills. The prompts path is deprecated per official Codex docs
but is what users on 0.142.x actually invoke today, so we ship both.

Both paths route to the same `magi-cp session pack activate` CLI the
pack-centric workflow adds. No CLI branching by invocation source.

### 5.3 Managed slash-command distribution

Codex docs are silent on org-forced skills or slash commands.
Filesystem ownership at install time is the enforcement mechanism.
The installer drops the skill and prompt files under
`~/.codex/skills/magi/` and `~/.codex/prompts/` with root-owned
permissions when installed as root on macOS/Linux, or under the
`%ProgramData%` sibling on Windows. This matches the CC posture. See
Section 6 for managed config coverage.

## 6. Managed enforcement

Codex has a managed-config concept (`/etc/codex/requirements.toml`,
`/etc/codex/managed_config.toml`, macOS MDM `com.openai.codex` plist,
`%ProgramData%\OpenAI\Codex\` on Windows) and this is where Magi
lives. Trust is filesystem-level, not signature-verified per session
start. Magi's CC managed-settings today are server-signed; the Codex
adapter downgrades that to root-owned files plus MDM.

### 6.1 What the installer writes

```
/etc/codex/requirements.toml       # policy hooks + features.hooks=true
/etc/codex/managed_config.toml     # magi runtime env + default model
/etc/codex/magi-cp/context-templates/*.txt  # sha256 sidecar files
```

macOS install path adds MDM plist provisioning as an optional
follow-up (documented but not automatic; MDM push is a customer-side
action).

Windows install path:

```
%ProgramData%\OpenAI\Codex\requirements.toml
%ProgramData%\OpenAI\Codex\managed_config.toml
%ProgramData%\OpenAI\Codex\magi-cp\context-templates\*.txt
```

### 6.2 `requirements.toml` shape

The compiler's `compile_to_codex_requirements(policies)` produces:

```toml
[features]
hooks = true
multi_agent = true  # only when at least one subagent policy exists

[[hooks.PreToolUse]]
matcher = "Bash"
[[hooks.PreToolUse.hooks]]
type = "command"
command = "/usr/local/bin/magi-cp gate --runtime codex"
timeout = 5000

# ... one entry per (event, matcher) pair, matching the shape
# compile_to_managed_settings produces today for CC
```

The gate binary itself is the same executable that services CC. The
`--runtime codex` flag is the CLI shortcut for setting
`MAGI_CP_RUNTIME=codex`; the dispatcher (Section 3.4) still sniffs
in case the env var is set instead.

### 6.3 Signature parity gap

Magi CC managed settings are server-signed. Codex managed settings
are trusted by filesystem ownership + MDM. There is no documented
signature verification in Codex managed config load. Practical
consequence: the trust boundary on Codex is "root owns the file" or
"MDM enforces the plist." An operator with root can mint hooks; on CC
they cannot without also holding a Magi signing key.

Live test item #4 asks whether Codex verifies any signature on the
`config_toml_base64` field in the MDM plist. If yes, we ship a signed
plist bundle. If no, we document the weaker trust boundary in the
dashboard's runtime picker.

## 7. Dashboard changes

Small surface. Runtime is a per-tenant property, defaulted to
`claude-code`, with per-policy coverage annotations.

### 7.1 Runtime picker chip on tenant settings

New chip on `/settings` (existing tab, already ships):

```
Runtime:  [ Claude Code  v ]
          Coverage: 100% (24 policies)

          Alternatives:
          ( ) Codex CLI
              Coverage preview: 22 policies enforced, 2 downgraded.
              Requires MAGI_CP_CODEX_RUNTIME_ENABLED=1.
```

- Default: `claude-code`.
- Persisted in `tenants.runtime_id` (new column, default
  `claude-code`).
- Selecting Codex writes the column, triggers a coverage-report
  render, does NOT flip enforcement until the operator confirms in a
  second step. This prevents an accidental click from breaking a live
  tenant.

### 7.2 Per-runtime coverage indicator on policy cards and pack cards

Each policy card gets a small coverage strip:

```
Enforcement:  CC  green    Codex  amber (post-hoc audit)
```

Each pack card gets a rollup:

```
Codex coverage: 12 policies enforced, 3 downgraded, 0 unsupported
```

Rendered by the coverage-report from `HookRuntime.coverage_report(ir)`.

### 7.3 Per-tenant runtime preference is a scalar, not a list

Kevin's decision: one runtime per tenant at a time, no dual-runtime
tenants in v1. Rationale: pack activation is session-scoped, and CC
and Codex do not share a session id. Supporting dual runtimes would
require pack-activation state on both runtime layers at once, which
doubles the surface for no clear win in beta.

If a customer needs both, they run two tenants. Deferred.

## 8. Session identity mapping

`session_id`, `turn_id`, and subagent inheritance all map onto the
pack-centric primitives with no schema change.

### 8.1 `session_active_packs` reuse

Same table as the pack-centric design doc. Add one column:

```
session_active_packs {
  session_id: str          -- CC session id OR Codex session id
  tenant_id: str
  runtime_id: str          -- NEW, default "claude-code",
                              part of the primary key.
  pack_ids: list[str]
  ...
}
```

The PK becomes `(tenant_id, runtime_id, session_id)`. CC and Codex
session ids do not collide in practice (both uuidv4) but the runtime
prefix makes it explicit and audit-legible.

### 8.2 Subagent inheritance

Per pack-centric decision 2 (subagent inherits parent packs). Codex
`SubagentStart` fires with subagent context on the payload. The gate
POSTs `/session/{child_session_id}/packs/activate` for each parent
pack, same code path as CC.

Caveat from live test #3: internal reviewers may not fire
`SubagentStart`. If they do not, they inherit nothing, and Shim D's
belt-and-suspenders parent-side hooks are the only coverage.

### 8.3 Transcript path

Codex provides `transcript_path` on every payload. Live test item #5
asks whether it is JSONL of the same shape CC uses. If yes, the
transcript reader in `src/magi_cp/evidence/` is shared. If no, we
ship a Codex-specific reader in `runtime/codex.py`.

## 9. Migration

### 9.1 Zero existing Codex users

Codex adapter ships behind `MAGI_CP_CODEX_RUNTIME_ENABLED` (default
off). No existing tenants have Codex as their runtime. Adapter is
purely additive:

- Zero-tenant migration required.
- Existing CC tenants unaffected. `tenants.runtime_id` defaults to
  `claude-code` on the column-add migration.
- Flag flip to on happens per-tenant via the dashboard runtime
  picker, not fleet-wide.

### 9.2 Column add is the only schema change

```sql
ALTER TABLE tenants ADD COLUMN runtime_id TEXT NOT NULL DEFAULT 'claude-code';
ALTER TABLE session_active_packs ADD COLUMN runtime_id TEXT NOT NULL DEFAULT 'claude-code';
-- rebuild PK to include runtime_id
```

Reversible. No data destroyed on rollback.

### 9.3 Feature flag ladder

- `MAGI_CP_CODEX_RUNTIME_ENABLED` (env var, default off): global kill
  switch. With this off, the dispatcher never emits the Codex path;
  the runtime picker chip in the dashboard shows Codex as "disabled
  in this build."
- `tenants.runtime_id` (per-tenant): actual selection.
- P4 does not flip a default. Flip is manual, per-tenant, forever.

## 10. Phased plan

Mirror of pack-centric-p1-p5 shape, but four phases.

### P1: `HookRuntime` trait + CC driver refactor + Codex TOML emitter

- Introduce `src/magi_cp/runtime/{trait.py, cc.py, codex.py, __init__.py}`.
- Extract CC-specific stdin parsing + verdict emission from
  `src/magi_cp/local/gate.py` into `runtime/cc.py`. Gate becomes a
  dispatcher.
- Add `codex.py` with a first-pass driver that parses the Codex
  stdin envelope and emits the Codex verdict envelope. No gap shims
  yet (P2 delivers those).
- Add `src/magi_cp/policy/codex_toml_emitter.py` with
  `compile_to_codex_requirements(policies)`. Output byte-stable for a
  given input.
- Add `tenants.runtime_id` + `session_active_packs.runtime_id`
  columns. Migration reversible.
- Feature flag `MAGI_CP_CODEX_RUNTIME_ENABLED`, default off.
- Tests: byte-equivalence for the CC path with flag off. Codex
  driver round-trip test with a canned Codex hook JSON fixture from
  DeepWiki. Byte-stability golden for the TOML emitter.
- Commit: "P1: HookRuntime trait + CC driver refactor + Codex TOML
  emitter (codex adapter)".

### P2: gap shims A + B + C + D

- Shim A: PreToolUse tool-coverage silent-skip. Compiler adds
  `PermissionRequest` + `PostToolUse` fallbacks. Coverage report
  marks the policies.
- Shim B: `additionalContext` rejection. Runtime intercepts,
  downgrades to `systemMessage` or defers to next `UserPromptSubmit`.
  Deferred queue at `~/.magi-cp/state/<session_id>/pending_context.jsonl`.
- Shim C: `SessionEnd` absence. `Stop` + `stop_hook_active`
  inspection. Cloud-side synthetic-fanout sweeper as fallback.
- Shim D: subagent hook gap. Parent-side belt-and-suspenders hook on
  `spawn_agent`. Optional synthetic probe skill (default off).
- Tests: each shim has a unit test covering the fixture from the
  research report. Coverage report golden.
- Commit: "P2: gap shims A-D (codex adapter)".

### P3: slash commands + CLI + installer distribution

- Ship `~/.codex/skills/magi/pack-{activate,deactivate,status,sticky}.md`
  as the forward-compat surface.
- Also ship `~/.codex/prompts/magi:pack:{activate,deactivate,status,sticky}.md`
  as the works-today fallback.
- Installer drops both sets, root-owned. macOS/Linux path
  `/etc/codex/requirements.toml` + `/etc/codex/managed_config.toml`.
  Windows path `%ProgramData%\OpenAI\Codex\`.
- CLI: no new subcommands. The pack-centric P3 CLI is reused as-is
  (`magi-cp session pack activate <id>`). The Codex skills and
  prompts shell out to the exact same command.
- Tests: installer test with a scratch dir asserts the six files land
  with the right permissions. Skill body test asserts the CLI
  invocation is byte-equal to the CC one.
- Commit: "P3: skills + prompts + installer for codex adapter".

### P4: dashboard runtime picker + per-tenant preference

- Add runtime picker chip on `/settings` per Section 7.1.
- Persist to `tenants.runtime_id`. Two-step confirm on switch.
- Add per-policy coverage strip (Section 7.2).
- Add per-pack coverage rollup (Section 7.2).
- Sessions tab (from pack-centric P4): show runtime column.
- i18n: KO + EN keys for runtime name, coverage labels, downgrade
  annotations.
- Tests: page test asserts the picker is present, the coverage strip
  renders, the two-step confirm blocks accidental clicks. E2E test
  asserts a tenant switch flips `session_active_packs.runtime_id` on
  next activation.
- Commit: "P4: dashboard runtime picker + per-tenant preference (codex
  adapter)".

## 11. Decisions locked and pending

### 11.1 Locked before P1

These are answered from the research report and this design doc, no
Codex install required.

- **L1. Option A + `HookRuntime` trait scaffolding shipped together.**
  Not deferred. Section 3.
- **L2. Runtime is a per-tenant scalar, not a list.** No dual-runtime
  tenants in v1. Section 7.3.
- **L3. Adapter is default off.** `MAGI_CP_CODEX_RUNTIME_ENABLED`.
  Section 9.3.
- **L4. Skills are the primary shipping path, prompts ship in
  parallel.** Section 5.
- **L5. Pack-centric `session_active_packs` reused with a
  `runtime_id` column.** No parallel table. Section 8.1.
- **L6. Compiler produces both CC managed-settings and Codex
  requirements.toml from the same IR.** No IR fork. Section 3.2.
- **L7. Runtime detection is env-first, then payload sniff, then
  session-id env, then CC fallback.** Section 3.4.
- **L8. Subagent inheritance mirrors CC: `SubagentStart` triggers a
  child-session activate POST for each parent pack.** Section 8.2.

### 11.2 Decisions pending, blocking specific phases

> AUTONOMOUS-BUILD DIRECTIVE (2026-07-01, Kevin): live tests are NOT
> run yet, and the pending decisions below do NOT block the build.
> Because the entire adapter ships behind `MAGI_CP_CODEX_RUNTIME_ENABLED`
> (default OFF), additive, reversible, with the CC path byte-equivalent
> when the flag is off, every phase implements against the RECOMMENDED
> DEFAULT stated in each pending decision (drawn from the research
> report). Mark each assumption at its call site with a
> `# TODO(live-test D<N>): <one-line assumption>` comment so a later
> validation pass is a single grep. Do NOT stall a phase waiting for a
> live-test answer. If a live test later contradicts an assumption, it
> becomes a small follow-up fix on dormant, flag-off code. Proceed
> through P1 to P4 without pausing.

Each pending decision maps to a live-test item (see Section 12 and the
standalone `2026-06-30-codex-live-test-checklist.md`).

- **D1 (blocks P2 shim C).** Does `Stop` with `stop_hook_active=true`
  reliably signal end-of-session? Live test item #2. If no, Shim C
  relies purely on the cloud-side synthetic sweeper.
- **D2 (blocks P2 shim D).** Does `approvals_reviewer = auto_review`
  fire `SubagentStart` on its internal reviewer? Live test item #3.
  If no, we know Shim D's belt-and-suspenders parent hook is the only
  coverage for internal subagents.
- **D3 (blocks P2 shim A follow-up).** Is the PreToolUse
  tool-coverage POC branch on OpenAI issue #20204 landing, and when?
  Live test item #4. If yes and soon, we may defer some Shim A
  fallbacks. If no, we keep them permanent.
- **D4 (blocks P3 managed skill enforcement).** Does Codex
  `requirements.toml` accept a `[skills]` block that force-installs
  Magi skills, or is skill discovery user-write only? Live test item
  #6. If no, we ship the skill files at install time and accept the
  user-write trust boundary.
- **D5 (blocks P3 transcript reader factoring).** Is
  `transcript_path` JSONL of the same shape CC uses? Live test item
  #5. If yes, the evidence reader is shared. If no, ship a
  Codex-specific reader in `runtime/codex.py`.

### 11.3 Decisions that can defer to P3 or P4

- **D6 (P3 or later).** Can a privileged in-process action de-register
  managed hooks without restart? Live test item #1. Docs say next
  start reapplies. We ship without an in-process disable path either
  way.
- **D7 (P3 or later).** Does the macOS MDM plist verify a signature
  on `config_toml_base64`? Live test item #7. If yes, ship signed. If
  no, document the weaker trust boundary.
- **D8 (P4 or later).** Does the `/hooks` menu show managed hooks as
  read-only, and can a user disable a non-managed hook that a managed
  one delegates to? Live test item #8. Affects the dashboard's
  "operator override" story more than the runtime itself.

### 11.4 Live-test findings (2026-07-01, Codex 0.137.0, macOS, ChatGPT-auth)

Ran the probe checklist against Kevin's real Codex install. Method: appended
`[[hooks.*]]` blocks to `~/.codex/config.toml` pointing at a logging shell
script, ran `codex exec ... --dangerously-bypass-hook-trust "Run bash: echo
MAGIHOOKTEST"`, and grepped the session rollout + the hook log. Config was
reverted from backup afterward (byte-identical diff confirmed).

- **F1. `hooks` feature is stable/true in 0.137.** `multi_agent` stable,
  `multi_agent_v2` under development, `plugin_hooks` REMOVED (folded into
  `hooks`). Event set from the native binary: `PreToolUse`,
  `PreToolUsePermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`,
  `SessionStart`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop`.
  No `Notification`/`SessionEnd`. This trims the emitter's event map.

- **F2. CRITICAL: user `config.toml [[hooks.*]]` blocks do NOT fire under
  `codex exec` (headless).** Proven: the rollout shows `function_call
  exec_command {cmd:"echo MAGIHOOKTEST"}` ran to exit 0, while the hook log
  stayed at 0 lines. Tested BOTH the nested CC-style TOML shape
  (`[[hooks.X]]` + `[[hooks.X.hooks]]` with `type`/`command`) AND a flat
  shape (`command` directly on `[[hooks.X]]`), with matcher-less
  `SessionStart`/`Stop`/`UserPromptSubmit` included, even with
  `--dangerously-bypass-hook-trust`. Zero fires in every case.
  **This means the current `codex_toml_emitter` target (config.toml
  `[[hooks]]`) is a dead surface for headless exec.** `codex exec` in CI is a
  gate-bypass path. Do NOT rely on config-level hooks for enforcement.

- **F3. The ONLY working hook registration on the machine is a PLUGIN
  `hooks.json`.** Canonical shape (from the installed `superpowers` plugin,
  `~/.codex/superpowers/hooks/hooks.json`) is the nested CC-style JSON:
  `{"hooks": {"SessionStart": [{"matcher": "startup|clear|compact", "hooks":
  [{"type": "command", "command": "...", "async": false}]}]}}`, referenced by
  the plugin manifest (`"hooks": "./hooks.json"`). So the shape Magi should
  emit is the nested JSON, delivered as a plugin sidecar, NOT inline TOML.

- **F4. Codex's shell tool is named `exec_command`, not `Bash`.** The
  `function_call.name` is `exec_command` with args `{cmd, workdir,
  yield_time_ms}`. Magi's matcher-to-tool map must translate CC's `Bash`/etc.
  to Codex tool names (`exec_command`, `apply_patch`, `shell`, MCP tool
  names). A `matcher = "Bash"` block would never match even if hooks fired.
  Resolves part of Shim A: the tool-name namespace differs.

- **F5. `requirements.toml` is the enforced (managed/MDM) layer, and is
  DENY-ONLY.** Binary error string: a rule with decision `allow` is "not
  permitted in requirements.toml: Codex merges these rules with other config
  and uses the most restrictive result (use 'prompt' or 'forbidden')".
  Managed hook sources have precedence `mdm > system > project >
  session_flags > plugin` (`ManagedHooksRequirements`, `cloud_requirements`,
  `cloud_managed_config`, `legacy_managed_config_*`). **Enforcement that a
  user cannot untrust or bypass must go through requirements.toml / the
  managed layer, expressed as `forbidden`/`prompt` only, never `allow`.**
  This is the true analog of CC's managed-settings.json. Confirms L6's target
  but constrains the compiler: the Codex IR->requirements.toml lowering can
  only express deny/prompt, so "allow" = absence of a deny rule.

- **F6. Trust model confirmed.** `startup_hooks_review.rs` + persisted
  `hooks.state`: hooks need interactive TUI trust ("Trust all and continue" /
  "Continue without trusting (hooks won't run)"). Headless exec has no TUI to
  grant trust; `--dangerously-bypass-hook-trust` is the automation escape but
  did NOT make config-level hooks fire (see F2), consistent with config
  `[[hooks]]` not being a live registration surface for exec.

- **F7. D5 RESOLVED (NO). `transcript_path` is Codex's own rollout JSONL, NOT
  CC's shape.** Records are `{timestamp, type, payload}` with
  `type ∈ {session_meta, event_msg, response_item, turn_context}`; tool calls
  appear as `response_item` `function_call`/`function_call_output`. Ship a
  Codex-specific evidence reader in `runtime/codex.py`; do NOT share the CC
  JSONL reader. Updates D5 from "pending" to "answered: separate reader".

**Actions this creates (all on flag-off, dormant code).** After auditing
the code the F2/F5 concerns turned out to be MOSTLY already-correct by
design; only F4 was a real bug. Status after the 2026-07-01 follow-up:

1. Registration surface: ALREADY CORRECT, no retarget needed. The
   installer (`local/codex_install.py`) writes the compiled hooks to
   `/etc/codex/requirements.toml` (the MANAGED enforcement layer, F5) and
   NEVER touches user `~/.codex/config.toml`. F2's dead-surface finding was
   about USER config.toml, which the product never used for enforcement, so
   the enforced path was right all along. The emitter also generates a
   `hooks_json_sidecar` (F3 nested plugin shape) that is currently spare
   (not installed); an interactive-plugin install path can adopt it later.
   Added a regression test locking "installer writes managed requirements,
   not user config.toml."
2. DONE. Matcher-name translation table (CC -> Codex: Bash->exec_command,
   Edit/Write/MultiEdit->apply_patch, Task->spawn_agent; read-family +
   regex pass through) (F4). This was the one genuine bug: without it every
   emitted `matcher` named a nonexistent Codex tool and fired zero times.
3. Event-map trim to the F1 set: the emitter emits only events that
   actually appear on authored policies, so no stale events leak; the F1
   set is recorded at the block-channel marker for when SessionEnd-hosted
   logic is added. No code change required now.
4. Codex-specific transcript reader (F7): DEFERRED (YAGNI). The gate does
   NOT read transcripts for evidence today (`verifier/descriptors.py`: the
   cloud never pulls `transcript_path`); the only transcript reader is the
   CC-only run-share path. When a transcript-consuming feature is built for
   Codex it MUST use a Codex-rollout-JSONL reader, not the CC reader
   (recorded at the codex.py breadcrumb + F7).
5. DONE (docs). `codex exec` documented as a gate-bypass surface unless
   enforcement rides the managed requirements layer (F2/F6); the managed
   installer is exactly that layer, so a correctly-installed tenant is not
   bypassable via `codex exec`.

Net: the live test found ONE real bug (F4, fixed) and otherwise CONFIRMED
the existing architecture (managed requirements = enforced surface). The
deny-only constraint (F5) is a forward-constraint on the not-yet-built
PermissionPolicy->requirements lowering (`TODO(live-test P2)` in codex.py),
recorded at the emitter docstring. D1/D2/D3/D4/D6/D7/D8 remain unrun (need
interactive TUI or an MDM harness).

## 12. Live test checklist (pointer)

See `docs/plans/2026-06-30-codex-live-test-checklist.md` for the
ordered checklist Kevin runs on a real Codex install. Each item is
mapped back to a D-decision above.

Short summary of what needs testing:

1. Managed hook mid-session mutability (D6).
2. `Stop` + `stop_hook_active` as a session-end proxy (D1).
3. `auto_review` internal reviewer fires `SubagentStart` yes / no (D2).
4. OpenAI issue #20204 PoC status + read-tool coverage (D3).
5. `transcript_path` JSONL schema (D5).
6. `requirements.toml [skills]` block acceptance (D4).
7. macOS MDM plist signature verification (D7).
8. `/hooks` menu behavior for managed hooks (D8).

## 13. Rollback runbook

Additive adapter. No data loss on rollback. Copy-pastable:

```
# 1. Kill switch: flip the env flag off on the cloud pods.
export MAGI_CP_CODEX_RUNTIME_ENABLED=0
# restart cloud pods; the runtime picker in dashboard reverts to CC-only.

# 2. Tenants currently on Codex: force back to CC.
psql -c "UPDATE tenants SET runtime_id = 'claude-code' WHERE runtime_id = 'codex';"

# 3. Session-active-packs cleanup (optional): drop Codex rows.
psql -c "DELETE FROM session_active_packs WHERE runtime_id = 'codex';"

# 4. Installed Codex managed configs on customer machines:
#    leave in place. With MAGI_CP_CODEX_RUNTIME_ENABLED=0 the gate
#    binary refuses to run in Codex mode; managed config is inert.
#    Uninstall only if the customer explicitly asks.

# 5. Dashboard: no revert required. Runtime picker chip renders as
#    disabled with the env flag off.

# 6. DB rollback: not required. runtime_id columns default to
#    'claude-code' and are ignored on the CC path.
```

If a customer install has a hard failure mode (Codex refusing to boot
because managed config is malformed), the CC installer's
`--force-remove-codex` flag deletes the two managed files. Ship this
flag in P3.

## 14. Trade-offs summary

**Wins.**
- Second runtime unlocks second market (OpenAI-shop shops that never
  installed CC).
- `HookRuntime` trait means the third runtime is one week, not
  another 8-week port.
- Pack-centric primitives are reused verbatim; no parallel activation
  layer.
- Additive migration. Zero risk to existing CC tenants.

**Costs.**
- Four gap shims to maintain. Each has a live-test dependency for its
  edges. Section 11.2.
- Trust boundary on Codex managed config is weaker than on CC. We
  document this rather than fix it. Section 6.3.
- Coverage is not 100% on the Codex path. Silent-skip tools + internal
  reviewers leave holes we surface but do not close.

## 15. What to decide before writing code

- Yes/no on Option B (formal trait) shipped in same commit as Codex
  driver. Kevin: yes (L1).
- Yes/no on per-tenant scalar runtime vs. dual-runtime tenants.
  Kevin: scalar (L2).
- Which skills-vs-prompts path ships. Kevin: both, skills primary (L4).
- Whether the runtime detection order sniffs the payload or trusts the
  env var. Kevin: env first, sniff second, fallback CC (L7).
