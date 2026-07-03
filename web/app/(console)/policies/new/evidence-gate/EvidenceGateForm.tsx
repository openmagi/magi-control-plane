"use client"

import { useMemo, useState } from "react"

import {
  DEFAULT_EVIDENCE_GATE_DRAFT,
  EVIDENCE_JUDGES,
  GATE_ACTIONS,
  buildEvidenceGatePolicies,
  describeEvidenceGate,
  looksLikeEvidenceGateIntent,
  parseEvidenceGateIntent,
  validateEvidenceGateDraft,
  type EvidenceGateDraft,
} from "@/lib/evidence-gate-builder"
// Sub-path imports ONLY (NOT from "@/components/ui"): the barrel re-exports
// NavBarShell (server-only), which webpack refuses to pull into a client
// bundle -> `next build` fails even though tsc/vitest pass. Same convention
// as the sibling policies/new/_components/*.
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Input, Textarea } from "@/components/ui/Input"

type Props = { action: (formData: FormData) => void }

function fieldError(errs: { field: string; message: string }[], field: string): string | undefined {
  return errs.find((e) => e.field === field)?.message
}

/** Focused authoring form for the session-evidence pair. Produces two coupled
 *  policies (an audit that records evidence + a gate that requires it) and posts
 *  their JSON to the server action for persistence. */
export default function EvidenceGateForm({ action }: Props) {
  const [d, setD] = useState<EvidenceGateDraft>(DEFAULT_EVIDENCE_GATE_DRAFT)
  const [nl, setNl] = useState("")
  const errs = useMemo(() => validateEvidenceGateDraft(d), [d])
  const [audit, gate] = useMemo(() => buildEvidenceGatePolicies(d), [d])
  const summary = useMemo(() => describeEvidenceGate(d), [d])

  const set = (patch: Partial<EvidenceGateDraft>) => setD({ ...d, ...patch })
  const setAudit = (p: Partial<EvidenceGateDraft["audit"]>) => setD({ ...d, audit: { ...d.audit, ...p } })
  const setGate = (p: Partial<EvidenceGateDraft["gate"]>) => setD({ ...d, gate: { ...d.gate, ...p } })

  const applyNl = () => setD(parseEvidenceGateIntent(nl))
  const nlHint = nl.trim() && !looksLikeEvidenceGateIntent(nl)

  const label = "block text-xs uppercase tracking-wide text-[var(--color-text-tertiary)] mb-1"

  return (
    <form action={action} className="space-y-4">
      {/* conversational seed: describe it -> fill the form */}
      <Card className="space-y-2">
        <div className="text-sm font-semibold">Describe it (optional)</div>
        <Textarea
          value={nl}
          onChange={(e) => setNl(e.target.value)}
          rows={2}
          placeholder="e.g. In ~/trading-mcp, before mcp__trading__execute_trade runs, require that a WebFetch or Bash verified a credible source; ask for approval if missing."
        />
        <div className="flex items-center gap-3">
          <Button type="button" variant="secondary" size="sm" onClick={applyNl} disabled={!nl.trim()}>Fill the form</Button>
          {nlHint ? <span className="text-xs text-[var(--color-text-tertiary)]">Tip: name the tool to gate and the fetch tools to check.</span> : null}
        </div>
      </Card>

      {/* plain-english summary */}
      <Card className="text-sm">
        <span className="text-[var(--color-text-tertiary)]">This enforces: </span>
        <span className="font-medium">{summary}</span>
      </Card>

      {/* the evidence kind (join key) */}
      <Card className="space-y-3">
        <div className="text-sm font-semibold">Evidence</div>
        <div>
          <label className={label}>Evidence name (join key)</label>
          <Input value={d.kind} onChange={(e) => set({ kind: e.target.value })} className="font-mono w-72" />
          {fieldError(errs, "kind") ? <p className="text-xs text-[var(--color-danger)] mt-1">{fieldError(errs, "kind")}</p> : null}
        </div>
        <div>
          <label className={label}>Policy id stem</label>
          <Input value={d.idStem} onChange={(e) => set({ idStem: e.target.value })} className="font-mono w-72" />
          <p className="text-xs text-[var(--color-text-tertiary)] mt-1">creates <code>{d.idStem}-audit</code> and <code>{d.idStem}-gate</code></p>
          {fieldError(errs, "idStem") ? <p className="text-xs text-[var(--color-danger)] mt-1">{fieldError(errs, "idStem")}</p> : null}
        </div>
      </Card>

      {/* audit: what records the evidence */}
      <Card className="space-y-3">
        <div className="text-sm font-semibold">1. Record evidence (audit)</div>
        <p className="text-xs text-[var(--color-text-tertiary)]">After each matched tool call, judge its source and record the verdict to this run.</p>
        <div className="flex flex-wrap gap-3">
          <div>
            <label className={label}>On tools (matcher)</label>
            <Input value={d.audit.matcher} onChange={(e) => setAudit({ matcher: e.target.value })} className="font-mono w-72" />
            {fieldError(errs, "audit.matcher") ? <p className="text-xs text-[var(--color-danger)] mt-1">{fieldError(errs, "audit.matcher")}</p> : null}
          </div>
          <div>
            <label className={label}>Judge</label>
            <select value={d.audit.judge} onChange={(e) => setAudit({ judge: e.target.value })}
                    className="border border-[var(--color-border)] rounded px-2 py-1.5 bg-transparent font-mono text-sm">
              {EVIDENCE_JUDGES.map((j) => <option key={j} value={j}>{j}</option>)}
            </select>
          </div>
        </div>
      </Card>

      {/* gate: what requires the evidence */}
      <Card className="space-y-3">
        <div className="text-sm font-semibold">2. Require it (gate)</div>
        <p className="text-xs text-[var(--color-text-tertiary)]">Before the gated tool runs, require a passing record of the evidence above.</p>
        <div className="flex flex-wrap gap-3">
          <div>
            <label className={label}>Gate tool (matcher)</label>
            <Input value={d.gate.matcher} onChange={(e) => setGate({ matcher: e.target.value })} className="font-mono w-72" />
            {fieldError(errs, "gate.matcher") ? <p className="text-xs text-[var(--color-danger)] mt-1">{fieldError(errs, "gate.matcher")}</p> : null}
          </div>
          <div>
            <label className={label}>When missing</label>
            <select value={d.gate.action} onChange={(e) => setGate({ action: e.target.value as "block" | "ask" })}
                    className="border border-[var(--color-border)] rounded px-2 py-1.5 bg-transparent font-mono text-sm">
              {GATE_ACTIONS.map((a) => <option key={a} value={a}>{a === "block" ? "block (deny)" : "ask (human approval)"}</option>)}
            </select>
          </div>
        </div>
        <div>
          <label className={label}>Deny reason (shown to the agent)</label>
          <Textarea value={d.gate.reason} onChange={(e) => setGate({ reason: e.target.value })} rows={2} />
          {fieldError(errs, "gate.reason") ? <p className="text-xs text-[var(--color-danger)] mt-1">{fieldError(errs, "gate.reason")}</p> : null}
        </div>
      </Card>

      {/* project scope */}
      <Card className="space-y-2">
        <div className="text-sm font-semibold">Scope (optional)</div>
        <div>
          <label className={label}>Only in project directory</label>
          <Input value={d.projectScope} onChange={(e) => set({ projectScope: e.target.value })}
                 className="font-mono w-full" placeholder="e.g. ~/trading-mcp  (blank = every session)" />
          <p className="text-xs text-[var(--color-text-tertiary)] mt-1">When set, both rules only apply to Claude Code sessions whose working directory is inside this path.</p>
          {fieldError(errs, "projectScope") ? <p className="text-xs text-[var(--color-danger)] mt-1">{fieldError(errs, "projectScope")}</p> : null}
        </div>
      </Card>

      {/* IR preview + submit */}
      <details className="text-xs">
        <summary className="cursor-pointer text-[var(--color-text-tertiary)]">Preview the two policies</summary>
        <pre className="mt-2 p-3 bg-[var(--color-surface-2)] rounded overflow-x-auto">{JSON.stringify([audit, gate], null, 2)}</pre>
      </details>

      <input type="hidden" name="draft_json" value={JSON.stringify(d)} />
      <Button type="submit" disabled={errs.length > 0}>Create evidence gate</Button>
    </form>
  )
}
