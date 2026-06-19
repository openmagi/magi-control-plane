"use client"
import { useMemo, useState, useTransition } from "react"
import {
  DEFAULT_DRAFT, validateDraft, previewManagedSettings,
  type PolicyDraft, type EventKind, type Decision,
} from "@/lib/policy-builder"

type Props = {
  /** Server action that POSTs the draft to the cloud. */
  submitAction: (formData: FormData) => Promise<void> | void
  /** Pre-fill (for editing); when null/undefined, use DEFAULT_DRAFT. */
  initial?: PolicyDraft | null
}

export default function PolicyBuilder({ submitAction, initial }: Props) {
  const [draft, setDraft] = useState<PolicyDraft>(initial ?? DEFAULT_DRAFT)
  const [pending, startTransition] = useTransition()

  const errors = useMemo(() => validateDraft(draft), [draft])
  const errorByField = useMemo(() => {
    const m = new Map<string, string>()
    for (const e of errors) m.set(e.field, e.message)
    return m
  }, [errors])

  const preview = useMemo(() => {
    try { return JSON.stringify(previewManagedSettings(draft), null, 2) }
    catch { return "(preview error)" }
  }, [draft])

  function update<K extends keyof PolicyDraft>(k: K, v: PolicyDraft[K]) {
    setDraft(d => ({ ...d, [k]: v }))
  }
  function updateTrigger<K extends keyof PolicyDraft["trigger"]>(
    k: K, v: PolicyDraft["trigger"][K],
  ) {
    setDraft(d => ({ ...d, trigger: { ...d.trigger, [k]: v } }))
  }

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (errors.length > 0) return
    // Build FormData from the actual form so all named inputs flow through
    // (including <select name="source">). Then add the serialized draft.
    const fd = new FormData(e.currentTarget)
    fd.set("draft_json", JSON.stringify(draft))
    startTransition(() => { submitAction(fd) })
  }

  const labelStyle: React.CSSProperties = {
    display: "flex", flexDirection: "column", gap: 4, marginBottom: 12,
    fontSize: 12,
  }
  const errLine = (field: string) => errorByField.get(field) ? (
    <span role="alert" style={{ color: "#e07979", fontSize: 11 }}>
      {errorByField.get(field)}
    </span>
  ) : null

  return (
    <form onSubmit={onSubmit} style={{
      display: "grid",
      gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
      gap: 18,
    }}>
      <section className="card">
        <h2>IR fields</h2>

        <label style={labelStyle}>
          <span className="muted">id</span>
          <input type="text" value={draft.id}
                 onChange={e => update("id", e.target.value)}
                 placeholder="legal-filing/v1" required maxLength={128}
                 aria-invalid={!!errorByField.get("id")}
                 aria-describedby="err-id" />
          <span id="err-id">{errLine("id")}</span>
        </label>

        <label style={labelStyle}>
          <span className="muted">description</span>
          <input type="text" value={draft.description}
                 onChange={e => update("description", e.target.value)}
                 maxLength={2000} />
        </label>

        <label style={labelStyle}>
          <span className="muted">trigger.event</span>
          <select value={draft.trigger.event}
                  onChange={e => updateTrigger("event", e.target.value as EventKind)}>
            <option value="PreToolUse">PreToolUse</option>
            <option value="PostToolUse">PostToolUse</option>
            <option value="Stop">Stop</option>
          </select>
        </label>

        <label style={labelStyle}>
          <span className="muted">trigger.matcher</span>
          <input type="text" value={draft.trigger.matcher}
                 onChange={e => updateTrigger("matcher", e.target.value)}
                 placeholder="Bash | mcp__court__file | *" required />
        </label>

        <label style={labelStyle}>
          <span className="muted">on_missing (decision)</span>
          <select value={draft.on_missing}
                  onChange={e => update("on_missing", e.target.value as Decision)}>
            <option value="deny">deny</option>
            <option value="ask">ask</option>
            <option value="log">log</option>
            <option value="allow">allow</option>
          </select>
          {errLine("matrix")}
        </label>

        <label style={labelStyle}>
          <span className="muted">sentinel_re (must contain ?P&lt;matter&gt; and ?P&lt;doc_id&gt;)</span>
          <input type="text" value={draft.sentinel_re}
                 onChange={e => update("sentinel_re", e.target.value)}
                 required maxLength={2000}
                 aria-invalid={!!errorByField.get("sentinel_re")}
                 aria-describedby="err-sentinel"
                 style={{ fontFamily: "ui-monospace, monospace" }} />
          <span id="err-sentinel">{errLine("sentinel_re")}</span>
        </label>

        <fieldset style={{ border: "1px solid #20232a", borderRadius: 6, padding: 8, marginBottom: 12 }}>
          <legend className="muted" style={{ fontSize: 11 }}>requires (evidence)</legend>
          {draft.requires.map((r, i) => (
            <div key={i} style={{ display: "flex", gap: 8, marginBottom: 6, alignItems: "center" }}>
              <input type="text" value={r.step} placeholder="step"
                     onChange={e => {
                       const next = [...draft.requires]
                       next[i] = { ...r, step: e.target.value }
                       update("requires", next)
                     }} />
              <input type="text" value={r.verdict} placeholder="verdict"
                     onChange={e => {
                       const next = [...draft.requires]
                       next[i] = { ...r, verdict: e.target.value }
                       update("requires", next)
                     }} />
              {draft.requires.length > 1 && (
                <button type="button" className="danger"
                        aria-label={`Remove requires row ${i + 1}`}
                        onClick={() => {
                          const next = draft.requires.filter((_, j) => j !== i)
                          update("requires", next)
                        }}>×</button>
              )}
            </div>
          ))}
          <button type="button"
                  onClick={() => update("requires",
                    [...draft.requires, { step: "", verdict: "pass" }])}>
            + add requirement
          </button>
          {errLine("requires")}
        </fieldset>

        <label style={labelStyle}>
          <span className="muted">source</span>
          <select name="source" defaultValue="org">
            <option value="platform">platform</option>
            <option value="org">org</option>
            <option value="bot">bot</option>
            <option value="user">user</option>
            <option value="session">session</option>
          </select>
        </label>

        <button type="submit" className="primary" disabled={errors.length > 0 || pending}
                aria-disabled={errors.length > 0 || pending}>
          {pending ? "Saving…" : "Save policy"}
        </button>
        {errors.length > 0 && (
          <p role="status" className="muted" style={{ marginTop: 6 }}>
            Fix {errors.length} validation issue{errors.length === 1 ? "" : "s"} above to enable Save.
          </p>
        )}
      </section>

      <section className="card">
        <h2>Compiled preview</h2>
        <p className="muted" style={{ fontSize: 11 }}>
          Live mirror of what the cloud compiler will emit. The cloud is
          authoritative; this is for authoring UX only.
        </p>
        <pre style={{
          background: "#0c0d10", border: "1px solid #20232a", borderRadius: 6,
          padding: 12, overflow: "auto", fontSize: 12, lineHeight: 1.4,
          maxHeight: "60vh",
        }}>{preview}</pre>
      </section>
    </form>
  )
}
