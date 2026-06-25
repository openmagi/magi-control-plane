/**
 * D73. structured report builder.
 *
 * Playwright's native JSON reporter is verbose. the workflow caller
 * wants a flat pass/fail/skip summary it can render in a final-smoke
 * step without parsing the full Playwright AST.
 *
 * This module reads Playwright's report.json (under .report/) and
 * emits two artifacts:
 *   - tests/e2e/.report/report.json. the curated summary in the
 *     shape the brief specifies (scenarios[], totals, started_at,
 *     ended_at, version, meta).
 *   - tests/e2e/.report/report.html. a human-readable table linking
 *     screenshots + traces.
 *
 * Graceful-skip (D73 follow-up): when Playwright never produced a JSON
 * file (e.g. docker missing, dashboard not up, preflight tripped) we
 * synthesize a SKIP report from `.report/preflight.json` so the
 * artifact always exists. `writeReport()` is happy to be called with a
 * missing pwReportPath; it falls back to the preflight sidecar.
 *
 * Invoked as a CLI: `node helpers/report.js` (after tsc), or
 * directly via writeReport() from a test fixture / global teardown.
 */
import {
  existsSync, mkdirSync, readFileSync, writeFileSync,
} from "node:fs"
import { execSync } from "node:child_process"
import { dirname, join, relative } from "node:path"

export type ScenarioStatus = "pass" | "fail" | "skip"

export type ScenarioAttachment = {
  name: string
  content_type?: string
  path?: string
  body_b64?: string
}

export type ScenarioStep = {
  title: string
  duration_ms: number
  status: ScenarioStatus
  error?: string
}

export type ScenarioReport = {
  id: string
  name: string
  spec_file?: string
  status: ScenarioStatus
  duration_ms: number
  reason?: string
  errors: Array<{ message: string; stack?: string; snippet?: string }>
  screenshots: string[]
  trace?: string
  ledger_rows: unknown[]
  attachments: ScenarioAttachment[]
  steps: ScenarioStep[]
}

export type ReportMeta = {
  git_sha?: string
  branch?: string
  node_version: string
  playwright_version?: string
  base_url?: string
  cloud_url?: string
  claude_bin?: string | null
  skipped_docker: boolean
  env_flags: Record<string, string | undefined>
}

export type FullReport = {
  scenarios: ScenarioReport[]
  totals: { pass: number; fail: number; skip: number }
  started_at: string
  ended_at: string
  version: string
  meta: ReportMeta
  missing?: string[]
  error?: string
}

type PwAttachment = {
  name: string
  contentType?: string
  path?: string
  body?: Buffer | string
}
type PwStep = {
  title: string
  duration?: number
  error?: { message?: string; stack?: string; snippet?: string }
}
type PwAnnotation = { type: string; description?: string }
type PwSpec = {
  title: string
  ok: boolean
  file?: string
  tests?: PwTest[]
}
type PwTest = {
  results: PwResult[]
  annotations?: PwAnnotation[]
}
type PwResult = {
  status: "passed" | "failed" | "skipped" | "timedOut" | "interrupted"
  duration: number
  error?: { message?: string; stack?: string; snippet?: string }
  errors?: Array<{ message?: string; stack?: string; snippet?: string }>
  attachments?: PwAttachment[]
  annotations?: PwAnnotation[]
  steps?: PwStep[]
  startTime?: string
}
type PwReport = {
  config?: { version?: string }
  suites?: Array<{
    title: string
    file?: string
    specs?: PwSpec[]
    suites?: Array<{ title: string; file?: string; specs?: PwSpec[] }>
  }>
  stats?: { startTime?: string; duration?: number }
}

/** Static list used to detect "expected scenario silently disappeared". */
const EXPECTED_SCENARIO_FILE_PREFIXES = [
  "01-wizard-happy-path",
  "02-prebuilt-toggle-roundtrip",
  "03-scripts-upload-and-use",
  "04-run-command-roundtrip",
  "05-inject-context-roundtrip",
]

function _flattenSpecs(
  rep: PwReport,
): Array<{ spec: PwSpec; file?: string }> {
  const out: Array<{ spec: PwSpec; file?: string }> = []
  for (const top of rep.suites ?? []) {
    const topFile = top.file
    for (const s of top.specs ?? []) {
      out.push({ spec: s, file: s.file ?? topFile })
    }
    for (const sub of top.suites ?? []) {
      const subFile = sub.file ?? topFile
      for (const s of sub.specs ?? []) {
        out.push({ spec: s, file: s.file ?? subFile })
      }
    }
  }
  return out
}

function _scenarioId(title: string, file?: string): string {
  // Prefer the spec filename (stable across title edits) when present.
  if (file) {
    const base = file.split(/[\\/]/).pop() ?? file
    const m = base.match(/^([0-9a-z][-a-z0-9]+)\.spec\.[mc]?tsx?$/i)
    if (m) return m[1].toLowerCase()
  }
  // Fallback: derive from title. "01 wizard happy path" -> "01-wizard-happy-path"
  return title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
}

function _statusFromResult(r: PwResult): ScenarioStatus {
  if (r.status === "skipped") return "skip"
  if (r.status === "passed") return "pass"
  return "fail"
}

function _skipReason(test: PwTest | undefined, result: PwResult): string | undefined {
  const fromResult = result.annotations?.find((a) => a.type === "skip")?.description
  if (fromResult) return fromResult
  const fromTest = test?.annotations?.find((a) => a.type === "skip")?.description
  if (fromTest) return fromTest
  return undefined
}

function _readJsonAttachments(name: string, atts: PwAttachment[]): unknown[] {
  const out: unknown[] = []
  for (const a of atts) {
    if (a.name !== name) continue
    try {
      let raw: string | null = null
      if (a.body) {
        raw = typeof a.body === "string"
          ? Buffer.from(a.body, "base64").toString("utf8")
          : a.body.toString("utf8")
      } else if (a.path && existsSync(a.path)) {
        raw = readFileSync(a.path, "utf8")
      }
      if (raw == null) continue
      out.push(JSON.parse(raw))
    } catch {
      // ignore malformed attachments. they will still appear under
      // scenario.attachments via the generic path below.
    }
  }
  return out
}

function _mapAttachments(
  atts: PwAttachment[],
  outDir: string,
): ScenarioAttachment[] {
  return atts.map((a) => {
    const ent: ScenarioAttachment = { name: a.name, content_type: a.contentType }
    if (a.path) ent.path = relative(outDir, a.path)
    if (a.body) {
      ent.body_b64 = typeof a.body === "string"
        ? a.body
        : Buffer.from(a.body).toString("base64")
    }
    return ent
  })
}

function _mapSteps(steps: PwStep[] | undefined): ScenarioStep[] {
  if (!steps || steps.length === 0) return []
  return steps.map((s) => ({
    title: s.title,
    duration_ms: Math.round(s.duration ?? 0),
    status: s.error ? "fail" : "pass",
    error: s.error?.message,
  }))
}

function _gitSha(): string | undefined {
  if (process.env.GIT_SHA) return process.env.GIT_SHA
  try {
    return execSync("git rev-parse HEAD", { encoding: "utf8" }).trim()
  } catch {
    return undefined
  }
}

function _gitBranch(): string | undefined {
  if (process.env.GIT_BRANCH) return process.env.GIT_BRANCH
  try {
    return execSync("git rev-parse --abbrev-ref HEAD", { encoding: "utf8" }).trim()
  } catch {
    return undefined
  }
}

function _meta(raw?: PwReport): ReportMeta {
  const env = process.env
  return {
    git_sha: _gitSha(),
    branch: _gitBranch(),
    node_version: process.version,
    playwright_version: raw?.config?.version,
    base_url: env.MAGI_CP_E2E_BASE_URL,
    cloud_url: env.MAGI_CP_CLOUD_URL,
    claude_bin: env.MAGI_CP_E2E_CLAUDE_BIN ?? null,
    skipped_docker:
      env.MAGI_CP_E2E_SKIP_DOCKER === "1" ||
      env.MAGI_CP_E2E_SKIP_DOCKER === "true",
    env_flags: {
      MAGI_CP_E2E_SKIP_DOCKER: env.MAGI_CP_E2E_SKIP_DOCKER,
      MAGI_CP_E2E_BASE_URL: env.MAGI_CP_E2E_BASE_URL,
      MAGI_CP_CLOUD_URL: env.MAGI_CP_CLOUD_URL,
      MAGI_CP_E2E_CLAUDE_BIN: env.MAGI_CP_E2E_CLAUDE_BIN,
      MAGI_CP_E2E_CLAUDE_TIMEOUT_MS: env.MAGI_CP_E2E_CLAUDE_TIMEOUT_MS,
    },
  }
}

function _synthesizeSkipReport(
  reason: string,
  version: string,
): FullReport {
  const now = new Date().toISOString()
  const scenarios: ScenarioReport[] = EXPECTED_SCENARIO_FILE_PREFIXES.map((id) => ({
    id,
    name: id.replace(/-/g, " "),
    spec_file: `tests/e2e/scenarios/${id}.spec.ts`,
    status: "skip" as const,
    duration_ms: 0,
    reason,
    errors: [],
    screenshots: [],
    ledger_rows: [],
    attachments: [],
    steps: [],
  }))
  return {
    scenarios,
    totals: { pass: 0, fail: 0, skip: scenarios.length },
    started_at: now,
    ended_at: now,
    version,
    meta: _meta(),
    error: reason,
  }
}

export function buildReport(
  pwReportPath: string,
  outDir: string,
  version = "d73-v1",
): FullReport {
  // Preflight sidecar takes precedence when Playwright produced nothing.
  if (!existsSync(pwReportPath)) {
    const preflight = join(dirname(pwReportPath), "preflight.json")
    if (existsSync(preflight)) {
      try {
        const p = JSON.parse(readFileSync(preflight, "utf8")) as {
          skip?: boolean; reason?: string;
        }
        if (p.skip) {
          return _synthesizeSkipReport(
            p.reason ?? "preflight reported skip without a reason",
            version,
          )
        }
      } catch {
        // fall through to a generic "missing report" synthesis
      }
    }
    return {
      scenarios: [],
      totals: { pass: 0, fail: 0, skip: 0 },
      started_at: new Date().toISOString(),
      ended_at: new Date().toISOString(),
      version,
      meta: _meta(),
      error: `playwright produced no report at ${pwReportPath}. check tests/e2e/.report/test-results/ for stderr.`,
      missing: [...EXPECTED_SCENARIO_FILE_PREFIXES],
    }
  }
  const raw = JSON.parse(readFileSync(pwReportPath, "utf8")) as PwReport
  const specs = _flattenSpecs(raw)
  const scenarios: ScenarioReport[] = []
  for (const { spec: sp, file } of specs) {
    const test = sp.tests?.[0]
    const result = test?.results?.[0]
    const id = _scenarioId(sp.title, file)
    if (!result) {
      // Always emit. silent disappearance is the bug class.
      scenarios.push({
        id,
        name: sp.title,
        spec_file: file,
        status: "fail",
        duration_ms: 0,
        reason:
          "spec did not produce a result (likely fixture / beforeAll failure or file syntax error)",
        errors: [],
        screenshots: [],
        ledger_rows: [],
        attachments: [],
        steps: [],
      })
      continue
    }
    const attachments = result.attachments ?? []
    const screenshots = attachments
      .filter((a) => a.name === "screenshot" && a.path)
      .map((a) => relative(outDir, a.path!))
    const trace = attachments.find((a) => a.name === "trace" && a.path)?.path
    const errors: Array<{ message: string; stack?: string; snippet?: string }> = []
    if (result.error?.message) {
      errors.push({
        message: result.error.message,
        stack: result.error.stack,
        snippet: result.error.snippet,
      })
    }
    for (const e of result.errors ?? []) {
      if (!e.message) continue
      // Skip dupes already captured from `error`.
      if (errors.some((x) => x.message === e.message)) continue
      errors.push({ message: e.message, stack: e.stack, snippet: e.snippet })
    }
    const status = _statusFromResult(result)
    let reason: string | undefined
    if (status === "skip") {
      reason = _skipReason(test, result) ?? "no skip reason emitted"
    } else if (errors.length > 0) {
      reason = errors[0].message
    }
    const ledgerRowsArrays = _readJsonAttachments("ledger-rows", attachments)
    const ledgerOnTimeout = _readJsonAttachments("ledger-rows-on-timeout", attachments)
    const ledger_rows: unknown[] = []
    for (const arr of [...ledgerRowsArrays, ...ledgerOnTimeout]) {
      if (Array.isArray(arr)) ledger_rows.push(...arr)
      else ledger_rows.push(arr)
    }
    scenarios.push({
      id,
      name: sp.title,
      spec_file: file,
      status,
      duration_ms: Math.round(result.duration ?? 0),
      reason,
      errors,
      screenshots,
      trace: trace ? relative(outDir, trace) : undefined,
      ledger_rows,
      attachments: _mapAttachments(attachments, outDir),
      steps: _mapSteps(result.steps),
    })
  }
  const totals = scenarios.reduce(
    (acc, s) => {
      acc[s.status]++
      return acc
    },
    { pass: 0, fail: 0, skip: 0 } as { pass: number; fail: number; skip: number },
  )
  // Detect scenarios that vanished entirely (file removed / filter dropped).
  const seenIds = new Set(scenarios.map((s) => s.id))
  const missing = EXPECTED_SCENARIO_FILE_PREFIXES.filter((id) => !seenIds.has(id))
  const start = raw.stats?.startTime ?? new Date().toISOString()
  const startMs = new Date(start).getTime()
  const dur = raw.stats?.duration ?? 0
  const ended = new Date(startMs + dur).toISOString()
  return {
    scenarios,
    totals,
    started_at: start,
    ended_at: ended,
    version,
    meta: _meta(raw),
    ...(missing.length > 0 ? { missing } : {}),
  }
}

function _htmlEscape(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
}

export function renderHtml(rep: FullReport): string {
  const rows = rep.scenarios.map((s) => {
    const screenshots = s.screenshots
      .map((p) => `<a href="${_htmlEscape(p)}">${_htmlEscape(p)}</a>`)
      .join("<br/>") || "."
    const trace = s.trace
      ? `<a href="${_htmlEscape(s.trace)}">trace</a>`
      : "."
    let reason = ""
    if (s.reason) {
      const full = s.reason
      if (full.length > 200) {
        reason = `${_htmlEscape(full.slice(0, 200))}... <em>(see report.json for full message)</em>`
      } else {
        reason = _htmlEscape(full)
      }
    }
    const steps = s.steps.length > 0
      ? `<details><summary>${s.steps.length} steps</summary><ul>${s.steps.map((st) => `<li>${_htmlEscape(st.title)} (${st.duration_ms}ms, ${st.status})${st.error ? ` - ${_htmlEscape(st.error.slice(0, 120))}` : ""}</li>`).join("")}</ul></details>`
      : ""
    return `<tr class="row ${s.status}">
      <td>${_htmlEscape(s.id)}</td>
      <td>${_htmlEscape(s.name)}${steps}</td>
      <td class="status">${s.status}</td>
      <td>${s.duration_ms} ms</td>
      <td>${reason}</td>
      <td>${screenshots}</td>
      <td>${trace}</td>
    </tr>`
  }).join("\n")
  const missing = (rep.missing && rep.missing.length > 0)
    ? `<p class="missing"><strong>Missing scenarios:</strong> ${rep.missing.map(_htmlEscape).join(", ")}</p>`
    : ""
  const meta = rep.meta
  const metaRow = `<p class="meta">
    <span>node ${_htmlEscape(meta.node_version)}</span>
    ${meta.playwright_version ? `<span>playwright ${_htmlEscape(meta.playwright_version)}</span>` : ""}
    ${meta.git_sha ? `<span>git ${_htmlEscape(meta.git_sha.slice(0, 8))}</span>` : ""}
    ${meta.branch ? `<span>branch ${_htmlEscape(meta.branch)}</span>` : ""}
    <span>skipped_docker=${meta.skipped_docker}</span>
  </p>`
  return `<!doctype html>
<html><head><meta charset="utf-8"/><title>magi-cp e2e report</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;color:#1a1a1a}
 table{border-collapse:collapse;width:100%}
 th,td{padding:8px 10px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}
 .pass{background:#ecfdf5}
 .fail{background:#fef2f2}
 .skip{background:#f8fafc}
 .status{font-weight:600;text-transform:uppercase}
 .totals span{margin-right:14px;font-weight:600}
 .meta span{margin-right:14px;color:#475569;font-size:13px}
 .missing{padding:8px;background:#fef3c7;border-left:4px solid #f59e0b}
</style></head>
<body>
<h1>magi-cp e2e report</h1>
<p class="totals">
  <span style="color:#16a34a">PASS ${rep.totals.pass}</span>
  <span style="color:#dc2626">FAIL ${rep.totals.fail}</span>
  <span style="color:#475569">SKIP ${rep.totals.skip}</span>
  <span>started ${_htmlEscape(rep.started_at)}</span>
  <span>ended ${_htmlEscape(rep.ended_at)}</span>
  <span>version ${_htmlEscape(rep.version)}</span>
</p>
${metaRow}
${missing}
${rep.error ? `<p class="missing"><strong>Report error:</strong> ${_htmlEscape(rep.error)}</p>` : ""}
<table>
  <thead><tr>
    <th>id</th><th>name</th><th>status</th><th>duration</th>
    <th>reason</th><th>screenshots</th><th>trace</th>
  </tr></thead>
  <tbody>
${rows}
  </tbody>
</table>
</body></html>`
}

export function writeReport(
  pwReportPath: string,
  outDir: string,
  version = "d73-v1",
): FullReport {
  if (!existsSync(outDir)) mkdirSync(outDir, { recursive: true })
  const rep = buildReport(pwReportPath, outDir, version)
  writeFileSync(join(outDir, "report.json"), JSON.stringify(rep, null, 2))
  writeFileSync(join(outDir, "report.html"), renderHtml(rep))
  return rep
}

// CLI entry: `node helpers/report.js [pwReportPath] [outDir]`
if (require.main === module) {
  const pwPath = process.argv[2] ?? join(process.cwd(), ".report/playwright.json")
  const outDir = process.argv[3] ?? dirname(pwPath)
  try {
    const rep = writeReport(pwPath, outDir)
    // eslint-disable-next-line no-console
    console.log(JSON.stringify(rep.totals))
    // Skip-only is exit 0. fail-only or fail-with-others is exit 1.
    process.exit(rep.totals.fail > 0 ? 1 : 0)
  } catch (e) {
    // Last-ditch: even if buildReport itself blew up, write a minimal
    // skeleton so the workflow sees a JSON file rather than nothing.
    try {
      if (!existsSync(outDir)) mkdirSync(outDir, { recursive: true })
      const skeleton: FullReport = {
        scenarios: [],
        totals: { pass: 0, fail: 0, skip: 0 },
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        version: "d73-v1",
        meta: _meta(),
        error: (e as Error).message,
      }
      writeFileSync(join(outDir, "report.json"), JSON.stringify(skeleton, null, 2))
      writeFileSync(join(outDir, "report.html"), renderHtml(skeleton))
    } catch {
      // give up. nothing left to do.
    }
    // eslint-disable-next-line no-console
    console.error((e as Error).message)
    process.exit(2)
  }
}
