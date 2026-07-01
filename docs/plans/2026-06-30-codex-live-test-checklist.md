# Codex adapter live-test checklist

Status: **actionable, run on a real Codex install**
Author: Kevin
Date: 2026-06-30
Companion to: `docs/plans/2026-06-30-codex-runtime-adapter-design.md` (Section 11.2 lists which D-decision each item unblocks).

## Setup (one time)

```
# Install Codex CLI 0.142.x (or newer, but confirm hook shape is unchanged).
brew install codex   # or the official installer per developers.openai.com/codex

# Confirm version + features gate.
codex --version                     # expect 0.142.x or newer
codex config get features.hooks     # expect true; if missing, set:
codex config set features.hooks true

# Prepare a scratch project.
mkdir -p ~/scratch/codex-magi-probe && cd ~/scratch/codex-magi-probe
git init && echo "# probe" > README.md && git add -A && git commit -m init

# Prepare a tiny probe hook binary that logs every event to a file.
cat > /tmp/probe-hook.sh <<'EOF'
#!/bin/bash
{
  echo "===== $(date -u +%FT%TZ) ====="
  cat -
  echo
} >> /tmp/codex-hook-log.jsonl
echo '{}'
EOF
chmod +x /tmp/probe-hook.sh
```

Register the probe as a user-level hook for every event we want to
observe. Snippet for `~/.codex/config.toml`:

```toml
[features]
hooks = true
multi_agent = true

[[hooks.PreToolUse]]
matcher = "Bash"
[[hooks.PreToolUse.hooks]]
type = "command"
command = "/tmp/probe-hook.sh"

# repeat for PreToolUse (apply_patch, unified_exec, MCP), PostToolUse,
# PermissionRequest, UserPromptSubmit, Stop, SubagentStart, SubagentStop,
# SessionStart, PreCompact, PostCompact.
```

Truncate `/tmp/codex-hook-log.jsonl` between checklist items so each
observation reads clean.

## Item 1: managed hook mid-session mutability (unblocks D6)

**What we need to know.** Can a privileged in-process action
de-register a managed hook without restarting Codex?

**Steps.**

1. Write `/etc/codex/requirements.toml` (or MDM plist on macOS) with a
   managed hook whose `command` is `/tmp/probe-hook.sh` on
   `PreToolUse`, matcher `Bash`.
2. `sudo chown root:wheel /etc/codex/requirements.toml && sudo chmod 644 /etc/codex/requirements.toml`.
3. Start `codex` in the scratch project. Run a Bash tool call.
   Confirm the probe log fires.
4. Without exiting: try `codex --config hooks.PreToolUse=[]` and
   `/hooks` subcommand, and any documented reload mechanism. Attempt
   to delete the managed hook.
5. Run another Bash tool call. Did the probe log still fire?

**Expected observation.** Docs suggest managed hooks reapply on next
start; if step 5 still fires the probe, mid-session immutability is
confirmed. If step 5 does NOT fire, an in-session de-registration
path exists and we need to document it (or defend against it).

**Unblocks.** D6 (P3+): whether we ship an in-process disable path.

## Item 2: `Stop` + `stop_hook_active` as session-end proxy (unblocks D1, blocks P2 shim C)

**What we need to know.** When Codex is about to exit the session (not
just a turn), does `Stop` fire with `stop_hook_active = true` (or some
other field) that lets us disambiguate turn-end from session-end?

**Steps.**

1. Ensure the `Stop` probe is registered in `~/.codex/config.toml`.
2. Start `codex`. Run one prompt, wait for the turn to end (Stop
   should fire, `stop_hook_active` value?). Copy the payload from the
   probe log.
3. Run another prompt. Copy the second Stop payload.
4. Type `/exit` (or Ctrl+D) to end the session. Watch for a final
   Stop event. Copy the payload.

**Expected observation.** Compare the three payloads. Field diffs are
what we need. Look specifically at:

- `stop_hook_active`: bool
- Any field named `session_ending`, `is_final`, or similar.
- Any absence-of-field pattern that separates turn-Stop from
  session-Stop.

**Unblocks.** D1 (blocks P2 Shim C). If a reliable disambiguator
exists, Shim C uses it. If not, Shim C falls back purely to the
cloud-side synthetic sweeper.

## Item 3: `auto_review` internal reviewer + SubagentStart (unblocks D2, blocks P2 shim D)

**What we need to know.** Does Codex's internal `approvals_reviewer =
auto_review` flow fire `SubagentStart` for its own reviewer subagent,
or only for user-triggered `spawn_agent` calls?

**Steps.**

1. Set `~/.codex/config.toml`:
   ```toml
   [features]
   hooks = true
   multi_agent = true

   [approvals]
   reviewer = "auto_review"
   ```
2. Ensure `SubagentStart` and `SubagentStop` probes are registered.
3. In the scratch project, ask Codex to apply a patch that requires
   review (e.g. `> please add a hello() function to hello.py`).
4. Watch the probe log for `SubagentStart` firings during the review
   pass. Note the payload's `subagent_type` or equivalent.
5. Separately, run a prompt that explicitly triggers `spawn_agent`
   (e.g. `> spawn an agent to summarize the README`). Compare the
   `SubagentStart` payloads.

**Expected observation.** If step 4 fires SubagentStart with a
distinct subagent identifier for the reviewer, internal subagents ARE
observable. If step 4 fires nothing while step 5 fires normally,
internal reviewers are hidden as DeepWiki suggests.

**Unblocks.** D2 (blocks P2 Shim D). If observable, Shim D covers the
internal reviewer directly. If not, Shim D's belt-and-suspenders
parent-side hook on `spawn_agent` is the sole coverage.

## Item 4: OpenAI issue #20204 PoC read-tool coverage (unblocks D3)

**What we need to know.** Is the PoC that adds PreToolUse coverage
for `list_dir`, `view_image`, `update_plan`, `tool_search`,
`tool_suggest`, planning tools, and web search landing, and on what
timeline?

**Steps.**

1. Check the current state of `github.com/openai/codex/issues/20204`.
   Note whether it's merged, has a target milestone, or is closed as
   won't-fix.
2. Check `codex-rs/core/src/hook_runtime.rs` on the tip of `main`
   for changes to the tool-covered list since 0.142.5.
3. Register PreToolUse probes for each of the silent-skip tools and
   confirm empirically: run a prompt that reads a directory (should
   trigger `list_dir`), one that views an image, one that triggers
   `update_plan` (invoke `/plan-mode`). Watch the probe log.

**Expected observation.** For each tool: probe fires (covered) or
does not (still silent-skip). Compare against the coverage matrix in
the design doc.

**Unblocks.** D3 (P2 Shim A follow-up). If coverage lands soon, we
mark those Shim A fallbacks as time-limited. If not, permanent.

## Item 5: `transcript_path` JSONL schema (unblocks D5, blocks P3 evidence-reader factoring)

**What we need to know.** Is Codex's transcript at `transcript_path`
the same JSONL shape as CC's, or Codex-specific?

**Steps.**

1. Take any recent `SessionStart` or `Stop` payload from the probe
   log. Copy the `transcript_path` value.
2. `cat` the file. First 20 lines. Note whether it's JSON, JSONL,
   binary, or something else.
3. If JSONL, compare the top-level keys against a CC transcript
   sample from `src/magi_cp/evidence/wal.py`'s test fixtures.

**Expected observation.**
- Same top-level keys and `type` values across CC and Codex: shared
  reader.
- Different keys or JSON-not-JSONL: Codex-specific reader in
  `runtime/codex.py`.
- Binary or missing: `transcript_path` is a stub; Magi's evidence
  reader relies on the `PostToolUse` payload's `tool_response`
  instead.

**Unblocks.** D5 (P3 or P2). Decides whether
`src/magi_cp/evidence/` grows a runtime-agnostic reader or gains a
Codex-specific one.

## Item 6: `requirements.toml [skills]` block acceptance (unblocks D4)

**What we need to know.** Can Codex managed config force-install a
skill, or is skill discovery strictly user-write?

**Steps.**

1. Write to `/etc/codex/requirements.toml`:
   ```toml
   [skills]
   force_install = ["magi:pack-activate", "magi:pack-status"]
   ```
   (This is a speculative shape; docs are silent, so we test whether
   Codex accepts or errors.)
2. Also try, in a second run, dropping a full skill file at
   `/etc/codex/skills/magi/pack-activate.md` and see whether Codex
   picks it up.
3. Start `codex` in a fresh project. Run `/hooks` and `/skills`
   subcommands to see the trust menu. Does the Magi skill appear as
   auto-trusted (equivalent to managed hooks), require operator
   trust, or fail to appear at all?

**Expected observation.** Codex either accepts a managed skill
enforcement path (rare per docs) or ignores it (filesystem
distribution + user trust is the only path).

**Unblocks.** D4 (P3). If managed enforcement works, ship it. If not,
ship files with root ownership and document the trust boundary.

## Item 7: macOS MDM plist signature verification (unblocks D7)

**What we need to know.** Does Codex verify a signature on
`config_toml_base64` in the `com.openai.codex` MDM domain plist?

**Steps.**

1. On a macOS device without MDM: `defaults write com.openai.codex
   requirements_toml_base64 <b64-string>` where the toml declares a
   managed hook.
2. Start `codex`. Does it load the managed hook? Check with the
   probe.
3. Try modifying the b64 with an obviously-invalid signature-shaped
   suffix (`::sig=bogus`). Reload. Does it still load?
4. Check `codex-rs/core/` source for any `verify_signature` or
   `crypto::verify` calls in the MDM load path.

**Expected observation.** If Codex loads the plist unconditionally,
no signature verification; trust boundary is MDM push authority + root.
If step 3 fails to load, signature verification exists (unexpected;
worth understanding).

**Unblocks.** D7 (P3 or later). Determines whether Magi ships a
signed MDM bundle or documents the weaker trust boundary.

## Item 8: `/hooks` menu behavior for managed hooks (unblocks D8)

**What we need to know.** Are managed hooks listed in the `/hooks`
menu as read-only, and can a user disable a non-managed hook that a
managed one delegates to?

**Steps.**

1. Register the probe as a user-level `PreToolUse` hook AND drop the
   same probe (root-owned) in `/etc/codex/requirements.toml` as a
   managed hook.
2. Start `codex`. Run `/hooks` inside the session.
3. Note which of the two entries are listed. Note whether each is
   marked as user-configurable or managed / read-only.
4. Try to disable the user-level hook. Then try to disable the
   managed hook. Note which succeed.
5. Run a Bash tool call. Confirm the probe log fires the expected
   number of times (0, 1, or 2 depending on which are enabled).

**Expected observation.** Managed hooks should show as read-only.
User hooks should be disableable. The `/hooks` menu is the operator's
runtime bypass.

**Unblocks.** D8 (P4 or later). Determines what the dashboard's
"operator override" surface looks like: strict (managed = frozen) vs.
permissive (managed = default that user can override).

## Final housekeeping

- Save the resulting probe log at
  `docs/plans/2026-06-30-codex-live-test-observations.md` as a
  followup file for future audit.
- Update `docs/plans/2026-06-30-codex-runtime-adapter-design.md`
  Section 11.2 with each answered decision, moving items from pending
  to locked.
- Kick off the codex-adapter workflow (`codex-adapter-p1-p4.js`) once
  D1, D2, D3, D4, D5 are answered. D6, D7, D8 can trail into P3/P4.
