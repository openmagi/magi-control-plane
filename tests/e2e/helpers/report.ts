/**
 * D73 — structured report builder.
 *
 * Playwright's native JSON reporter is verbose; the workflow caller
 * wants a flat pass/fail/skip summary it can render in a final-smoke
 * step without parsing the full Playwright AST.
 *
 * This module reads Playwright's report.json (under .report/) and
 * emits two artifacts:
 *   - tests/e2e/.report/report.json  — the curated summary in the
 *     shape the brief specifies (scenarios[], totals, started_at,
 *     ended_at, version).
 *   - tests/e2e/.report/report.html  — a human-readable table linking
 *     screenshots + traces.
 *
 * Invoked as a CLI: `node helpers/report.js` (after tsc) — or you
 * can directly call buildReport() from a test fixture.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, join, relative } from "node:path"

export type ScenarioStatus = "pass" | "fail" | "skip"

export type ScenarioReport = {
  id: string
  name: string
  status: ScenarioStatus
  duration_ms: number
  reason?: string
  screenshots: string[]
  trace?: string
  ledger_rows: unknown[]
}

export type FullReport = {
  scenarios: ScenarioReport[]
  totals: { pass: number; fail: number; skip: number }
  started_at: string
  ended_at: string
  version: string
}

type PwSpec = {
  title: string
  ok: boolean
  tests?: PwTest[]
}
type PwTest = {
  results: PwResult[]
}
type PwResult = {
  status: "passed" | "failed" | "skipped" | "timedOut" | "interrupted"
  duration: number
  error?: { message?: string }
  attachments?: Array<{ name: string; path?: string }>
  startTime?: string
}
type PwReport = {
  config: { version?: string }
  suites?: Array<{
    title: string
    specs?: PwSpec[]
    suites?: Array<{ title: string; specs?: PwSpec[] }>
  }>
  stats?: { startTime?: string; duration?: number }
}

function _flattenSpecs(rep: PwReport): PwSpec[] {
  const out: PwSpec[] = []
  for (const top of rep.suites ?? []) {
    for (const s of top.specs ?? []) out.push(s)
    for (const sub of top.suites ?? []) {
      for (const s of sub.specs ?? []) out.push(s)
    }
  }
  return out
}

function _scenarioId(title: string): string {
  // "01 wizard happy path" -> "01-wizard-happy-path"
  return title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
}

function _statusFromResult(r: PwResult): ScenarioStatus {
  if (r.status === "skipped") return "skip"
  if (r.status === "passed") return "pass"
  return "fail"
}

export function buildReport(
  pwReportPath: string,
  outDir: string,
  version = "d73-v1",
): FullReport {
  if (!existsSync(pwReportPath)) {
    throw new Error(`buildReport: playwright report not found at ${pwReportPath}`)
  }
  const raw = JSON.parse(readFileSync(pwReportPath, "utf8")) as PwReport
  const specs = _flattenSpecs(raw)
  const scenarios: ScenarioReport[] = []
  for (const sp of specs) {
    const test = sp.tests?.[0]
    const result = test?.results?.[0]
    if (!result) continue
    const attachments = result.attachments ?? []
    const screenshots = attachments
      .filter((a) => a.name === "screenshot" && a.path)
      .map((a) => relative(outDir, a.path!))
    const trace = attachments.find((a) => a.name === "trace" && a.path)?.path
    scenarios.push({
      id: _scenarioId(sp.title),
      name: sp.title,
      status: _statusFromResult(result),
      duration_ms: Math.round(result.duration ?? 0),
      reason: result.error?.message,
      screenshots,
      trace: trace ? relative(outDir, trace) : undefined,
      ledger_rows: [],
    })
  }
  const totals = scenarios.reduce(
    (acc, s) => {
      acc[s.status]++
      return acc
    },
    { pass: 0, fail: 0, skip: 0 } as { pass: number; fail: number; skip: number },
  )
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
      .join("<br/>") || "—"
    const trace = s.trace
      ? `<a href="${_htmlEscape(s.trace)}">trace</a>`
      : "—"
    const reason = s.reason ? _htmlEscape(s.reason).slice(0, 200) : ""
    return `<tr class="row ${s.status}">
      <td>${_htmlEscape(s.id)}</td>
      <td>${_htmlEscape(s.name)}</td>
      <td class="status">${s.status}</td>
      <td>${s.duration_ms} ms</td>
      <td>${reason}</td>
      <td>${screenshots}</td>
      <td>${trace}</td>
    </tr>`
  }).join("\n")
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
    process.exit(rep.totals.fail > 0 ? 1 : 0)
  } catch (e) {
    // eslint-disable-next-line no-console
    console.error((e as Error).message)
    process.exit(2)
  }
}
