import { describe, it, expect } from "vitest"
import { execSync } from "node:child_process"
import path from "node:path"
import { ENV_REFERENCE, groupEntries } from "./env-reference"

/**
 * D78 + review fix: env-reference is the source of truth for the
 * /docs/env-reference page. This test layer pins the doc against the
 * actual source tree two ways:
 *
 *  1. A small REQUIRED list keeps the names that doc pages explicitly
 *     cite from disappearing in a refactor.
 *  2. A bidirectional grep gate over `src/magi_cp/` + `web/` (minus
 *     the env-reference module and its own test) enforces that:
 *       - every `MAGI_CP_*` name actually referenced in code appears
 *         in ENV_REFERENCE (no silent additions);
 *       - every `MAGI_CP_*` documented here is referenced somewhere in
 *         code (no stale rows).
 *
 * The grep gate is shell-based (`grep -rho`) so we do not need extra
 * dependencies. POSIX-portable across macOS BSD grep and GNU grep.
 */

const REPO_ROOT = path.resolve(__dirname, "..", "..")

function grepMagiCpNames(): Set<string> {
  /* `grep -rhoE 'MAGI_CP_[A-Z0-9_]+' <paths>` — print every match on its own
   * line, recurse, no filename prefix. We exclude the env-reference
   * module and its test so we don't see ourselves in the mirror. */
  const cmd = [
    "grep -rhoE 'MAGI_CP_[A-Z0-9_]+'",
    "--include='*.py' --include='*.ts' --include='*.tsx' --include='*.sh' --include='*.yml' --include='*.yaml' --include='*.json' --include='*.md'",
    "--exclude-dir=node_modules --exclude-dir=__pycache__ --exclude-dir=.next",
    "src/magi_cp web",
    "| grep -v 'env-reference'",
    "| sort -u",
  ].join(" ")
  const out = execSync(`/bin/sh -c "${cmd}"`, {
    cwd: REPO_ROOT,
    encoding: "utf-8",
    maxBuffer: 64 * 1024 * 1024,
  })
  const names = out
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s.startsWith("MAGI_CP_"))
  return new Set(names)
}

describe("D78 env-reference", () => {
  it("includes every MAGI_CP_* entry expected by the docs", () => {
    // Spot-check the names that the doc pages explicitly cite. If any
    // disappear, the doc would link to a missing row.
    const REQUIRED = [
      "MAGI_CP_API_KEY",
      "MAGI_CP_ADMIN_API_KEY",
      "MAGI_CP_HITL_API_KEY",
      "MAGI_CP_CLOUD_URL",
      "MAGI_CP_ALLOW_RUN_COMMAND",
      "MAGI_CP_REQUIRE_SIGNED_RUN_COMMAND_SPEC",
      "MAGI_CP_SCRIPT_STORE_DIR",
      "MAGI_CP_RUN_COMMAND_LEDGER",
      "MAGI_CP_LLM_COMPILER",
      "MAGI_CP_LLM_REVIEWER",
      "MAGI_CP_CONTEXT_TEMPLATES_DIR",
    ]
    const names = ENV_REFERENCE.map((e) => e.name)
    for (const r of REQUIRED) {
      expect(names, `missing required env-reference entry: ${r}`).toContain(r)
    }
  })

  it("every entry has both ko and en one-liners", () => {
    for (const e of ENV_REFERENCE) {
      expect(e.ko, `${e.name} missing ko`).toBeTruthy()
      expect(e.en, `${e.name} missing en`).toBeTruthy()
    }
  })

  it("groupEntries() partitions the full reference into four buckets", () => {
    const g = groupEntries()
    const total = g.cloud.length + g.local.length + g.dashboard.length + g.provider.length
    expect(total).toBe(ENV_REFERENCE.length)
  })

  it("no duplicate names", () => {
    const seen = new Set<string>()
    for (const e of ENV_REFERENCE) {
      expect(seen.has(e.name), `duplicate entry ${e.name}`).toBe(false)
      seen.add(e.name)
    }
  })

  it("bidirectional source-grep gate: ENV_REFERENCE matches MAGI_CP_* usage", () => {
    const sourceNames = grepMagiCpNames()
    const documented = new Set(
      ENV_REFERENCE.map((e) => e.name).filter((n) => n.startsWith("MAGI_CP_")),
    )
    const undocumented: string[] = []
    for (const n of sourceNames) {
      if (!documented.has(n)) undocumented.push(n)
    }
    const stale: string[] = []
    for (const n of documented) {
      if (!sourceNames.has(n)) stale.push(n)
    }
    expect(
      undocumented,
      `MAGI_CP_* present in source but missing from env-reference.ts: ${undocumented.join(", ")}`,
    ).toEqual([])
    expect(
      stale,
      `MAGI_CP_* in env-reference.ts but absent from source: ${stale.join(", ")}`,
    ).toEqual([])
  })
})
