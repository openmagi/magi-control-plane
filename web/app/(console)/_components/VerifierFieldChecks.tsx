import {
  fieldChecksFlat,
  getVerifierDescriptor,
  lifecycleGroupsFor,
  type FieldCheck,
  type FieldChecksByLifecycle,
} from "@/lib/verifier-descriptors"
import { Code } from "@/components/ui"

/**
 * D52d: per-field check tree, rendered in two surfaces:
 *
 *   1. The Rules → Verifiers catalog expander (rules/_components/
 *      VerifierExpander.tsx), where it replaces the prior flat input
 *      payload chip row with structured `path -> check description`
 *      pairs.
 *   2. The policies/new wizard verifier picker (policies/new/_components/
 *      WizardEvidenceTree.tsx → this component), inline below the
 *      author's verifier checkbox so they can see what the verifier
 *      actually inspects before they save the policy.
 *
 * Same data, same render. The only difference is whether the parent
 * also surfaces `verdicts:` / `emits:` rows underneath. The wizard
 * inline form opts in via `showFooter`; the catalog expander already
 * has dedicated Verdicts + Output Evidence panels and turns the footer
 * off to avoid duplicating those rows.
 *
 * Semantic HTML:
 *   - <dl><dt><dd> markup for the path → description mapping so screen
 *     readers announce "term, description" instead of two anonymous
 *     spans.
 *   - The visual tree characters (└─ etc.) are purely decorative and
 *     marked aria-hidden so the SR experience reads the data, not the
 *     ASCII art.
 *
 * Preview branch: a verifier without a registered descriptor (custom
 * verifiers authored via /verifiers/new, third-party preview prefix)
 * renders a one-line note instead of a tree. The catalog expander
 * already shows a "no descriptor" panel for the same case; the wizard
 * picker DOES need the inline note so the author understands why no
 * tree expanded after they picked.
 *
 * D57e: field_checks is grouped by lifecycle CC event on the
 * descriptor. The renderer:
 *
 *   - Renders ONE <section> per lifecycle group, headed with the CC
 *     event name plus a plain-language tooltip ("PreToolUse" = "Before
 *     any tool runs").
 *   - When the parent passes `lifecycle="<event>"` (the policy's
 *     current lifecycle), THAT group is expanded by default and the
 *     others appear collapsed + grayed-out (visual de-emphasis, not
 *     hidden, so the operator still sees the cross-lifecycle context).
 *   - The legacy `fieldChecksOverride` prop stays a FLAT list for
 *     custom verifiers (custom rows are NOT grouped: they have no
 *     descriptor authoring tooling for lifecycle groups yet). The
 *     renderer treats a flat override as a single unlabeled group
 *     so the visual contract holds.
 */

type T = (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string

export function VerifierFieldChecks({
  step,
  t,
  showFooter = false,
  className,
  fieldChecksOverride,
  lifecycle,
}: {
  step: string
  t: T
  /** When true, surface the verdicts + emits rows under the tree (used
   * by the policy wizard picker; the catalog expander already has
   * dedicated Verdicts + Output Evidence panels). */
  showFooter?: boolean
  className?: string
  /** D52d follow-up: explicit field_checks override for catalog rows
   * whose `step` does not map to a built-in descriptor. Custom
   * verifiers authored at /verifiers/new carry their author-supplied
   * field_checks on the EvidenceTypeEntry. Without this, the tree
   * would render the "preview mode" placeholder for the very rows
   * the operator just authored. When provided AND non-empty, the
   * override replaces the descriptor's field_checks; when omitted,
   * the descriptor mirror is used (built-in path). The footer rows
   * (verdicts + emits) are descriptor-only; custom rows render the
   * tree without a footer because they have no registered descriptor
   * to source verdicts/evidence from.
   *
   * D57e: the override is a FLAT list (custom rows are not grouped
   * by lifecycle yet). The renderer wraps a flat override into a
   * single unlabeled group so the visual contract holds. */
  fieldChecksOverride?: FieldCheck[]
  /** D57e: the policy's current lifecycle CC event (PreToolUse /
   * PostToolUse / Stop / ...). When set, the matching group's
   * section is open by default and the other groups render collapsed
   * + grayed-out so the operator sees the cross-lifecycle context
   * without losing the focus group. When undefined (catalog surface
   * with no policy context), every group renders open. */
  lifecycle?: string
}) {
  const descriptor = getVerifierDescriptor(step)

  // D57e: build the groups dict the renderer walks. Custom override
  // takes precedence and is treated as a single unlabeled group (no
  // lifecycle keying yet on custom rows). Built-in path reads the
  // grouped descriptor; falls back to the flat helper for backward
  // compat with a mirror copy that still ships the old shape.
  let groups: FieldChecksByLifecycle | { _: FieldCheck[] } | null = null
  if (fieldChecksOverride !== undefined) {
    if (fieldChecksOverride.length > 0) {
      groups = { _: fieldChecksOverride }
    }
  } else if (descriptor !== null) {
    const grouped = descriptor.field_checks
    if (grouped && Object.keys(grouped).length > 0) {
      groups = grouped
    } else {
      // Older mirror copy that still ships the flat shape: flatten
      // helper returns [] for a fresh-shape descriptor with no
      // groups, so this branch is a true legacy fallback.
      const flat = fieldChecksFlat(descriptor)
      if (flat.length > 0) groups = { _: flat }
    }
  }

  if (groups === null) {
    return (
      <div
        data-testid="verifier-field-checks-preview"
        className={
          (className ?? "") +
          " text-[11.5px] italic text-[var(--color-text-tertiary)] leading-relaxed"
        }
      >
        {t("rules.verifier.fieldChecks.preview")}
      </div>
    )
  }

  const groupKeys = Object.keys(groups)
  // Single unlabeled group (custom override or legacy flat) renders
  // without the per-group <section> wrapper so the visual baseline
  // matches the pre-D57e tree. Multi-group descriptors render one
  // <section> per lifecycle with a heading.
  const isSingleUnlabeled = groupKeys.length === 1 && groupKeys[0] === "_"

  // Build the verdicts + emits footer once so the wizard picker can show
  // the same shape that the catalog expander shows in its dedicated
  // panels. Always rendered when `showFooter` is on AND we have a
  // registered descriptor; custom-source rows have no descriptor so the
  // footer is omitted there even when showFooter is requested.
  const verdicts = descriptor?.verdict_set ?? []
  const evidencePaths = (descriptor?.output_evidence ?? []).map((e) => e.path)
  const renderFooter = showFooter && descriptor !== null

  if (isSingleUnlabeled) {
    const rows = (groups as { _: FieldCheck[] })._
    return (
      <div
        data-testid="verifier-field-checks-tree"
        className={(className ?? "") + " text-xs"}
      >
        <FieldCheckRows rows={rows} />
        {renderFooter && (
          <FieldChecksFooter
            verdicts={verdicts}
            evidencePaths={evidencePaths}
            t={t}
          />
        )}
      </div>
    )
  }

  return (
    <div
      data-testid="verifier-field-checks-tree"
      className={(className ?? "") + " text-xs space-y-2"}
    >
      {(groupKeys as Array<string>).map((event) => {
        const isActive = lifecycle !== undefined && event === lifecycle
        const isDimmed = lifecycle !== undefined && event !== lifecycle
        // D57e: when a parent passes a policy lifecycle, the matching
        // group renders open and the others render collapsed (still
        // visible inside a native <details> the operator can expand).
        // When no lifecycle is passed (catalog surface), every group
        // is open by default.
        const defaultOpen = lifecycle === undefined || isActive
        return (
          <details
            key={event}
            open={defaultOpen}
            data-testid={`verifier-field-checks-group-${event}`}
            data-lifecycle-event={event}
            data-lifecycle-active={isActive ? "true" : "false"}
            data-lifecycle-dimmed={isDimmed ? "true" : "false"}
            className={
              "rounded-md border border-black/[0.05] bg-white px-2 py-1.5 " +
              (isDimmed
                ? "opacity-60 grayscale"
                : "")
            }
          >
            <summary
              className="flex cursor-pointer items-baseline gap-2 text-[11px] uppercase tracking-wider font-semibold text-[var(--color-text-secondary)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]"
              aria-label={`${event} ${lifecycleTooltip(event)}`}
            >
              <Code className="text-[11.5px] normal-case tracking-normal">{event}</Code>
              <span
                className="text-[10.5px] font-normal normal-case tracking-normal text-[var(--color-text-tertiary)]"
                title={lifecycleTooltip(event)}
              >
                {lifecycleTooltip(event)}
              </span>
              {isActive && (
                <span
                  data-testid={`verifier-field-checks-group-${event}-active-pill`}
                  className="ml-auto inline-flex items-center rounded-full bg-[var(--color-accent)]/10 px-1.5 py-0 text-[9.5px] font-semibold uppercase tracking-wider text-[var(--color-accent)]"
                >
                  {t("rules.verifier.fieldChecks.lifecycleActive")}
                </span>
              )}
            </summary>
            <div className="mt-1.5">
              <FieldCheckRows rows={(groups as FieldChecksByLifecycle)[event]} />
            </div>
          </details>
        )
      })}
      {renderFooter && (
        <FieldChecksFooter
          verdicts={verdicts}
          evidencePaths={evidencePaths}
          t={t}
        />
      )}
    </div>
  )
}

/** D57e: plain-language tooltip for each CC hook event. Surfaced on
 * the <summary> via title= and aria-label= so a SR scan of the
 * section headings hears "PreToolUse, Before any tool runs" instead
 * of bare camel-case event names. Falls back to the event name when
 * the event is one this helper does not know (preserves forward
 * compat for an event the cloud adds before the helper does). */
function lifecycleTooltip(event: string): string {
  switch (event) {
    case "PreToolUse":
      return "Before any tool runs"
    case "PostToolUse":
      return "After a tool returns"
    case "Stop":
      return "Before the agent's final reply"
    case "UserPromptSubmit":
      return "When a user prompt arrives"
    case "SubagentStop":
      return "When a subagent stops"
    case "PreCompact":
      return "Before context compaction"
    case "SessionStart":
      return "When the session opens"
    case "SessionEnd":
      return "When the session closes"
    default:
      return event
  }
}

function FieldCheckRows({ rows }: { rows: FieldCheck[] }) {
  return (
    <dl
      role="list"
      className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5"
    >
      {rows.map((fc, i) => {
        const isLast = i === rows.length - 1
        return (
          <Row
            key={`${fc.path}-${i}`}
            path={fc.path}
            description={fc.check_description}
            isLast={isLast}
          />
        )
      })}
    </dl>
  )
}

/** D52d follow-up (a11y): the footer pipe/brace separators are
 * decorative ASCII, not data. We render verdicts + emits as <ul
 * role='list'> with one <li> per item so the SR experience reads
 * "verdicts: pass, fail" / "emits: step, subject, …" instead of
 * "verdicts colon pass space pipe space fail". Visual separators are
 * applied via CSS pseudo-elements with aria-hidden so sighted users
 * still see `pass | fail` and `{ step, subject }`. The colour token
 * is bumped from tertiary to secondary so 11/10 px labels clear
 * WCAG AA 4.5:1 on the off-white catalog/wizard surfaces. */
function FieldChecksFooter({
  verdicts,
  evidencePaths,
  t,
}: {
  verdicts: ReadonlyArray<string>
  evidencePaths: ReadonlyArray<string>
  t: T
}) {
  return (
    <div
      data-testid="verifier-field-checks-footer"
      className="mt-2 pt-2 border-t border-black/[0.05] space-y-1.5 text-[11px] text-[var(--color-text-secondary)] leading-relaxed"
    >
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span
          id="verifier-field-checks-footer-verdicts-label"
          className="uppercase tracking-wider font-semibold text-[10px] text-[var(--color-text-secondary)]"
        >
          {t("rules.verifier.fieldChecks.verdicts")}
        </span>
        <ul
          role="list"
          aria-labelledby="verifier-field-checks-footer-verdicts-label"
          className="font-mono inline-flex flex-wrap gap-x-2 [&>li+li]:before:content-['|'] [&>li+li]:before:mr-2 [&>li+li]:before:text-[var(--color-text-tertiary)]"
        >
          {verdicts.map((v) => (
            <li key={v} className="inline">
              {v}
            </li>
          ))}
        </ul>
      </div>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span
          id="verifier-field-checks-footer-emits-label"
          className="uppercase tracking-wider font-semibold text-[10px] text-[var(--color-text-secondary)]"
        >
          {t("rules.verifier.fieldChecks.emits")}
        </span>
        <span aria-hidden className="font-mono text-[var(--color-text-tertiary)]">
          {"{"}
        </span>
        <ul
          role="list"
          aria-labelledby="verifier-field-checks-footer-emits-label"
          className="font-mono inline-flex flex-wrap gap-x-2 break-all [&>li+li]:before:content-[','] [&>li+li]:before:mr-1 [&>li+li]:before:text-[var(--color-text-tertiary)]"
        >
          {evidencePaths.map((p) => (
            <li key={p} className="inline">
              {p}
            </li>
          ))}
        </ul>
        <span aria-hidden className="font-mono text-[var(--color-text-tertiary)]">
          {"}"}
        </span>
      </div>
    </div>
  )
}

/** D57e: expose the lifecycle helper so wizard / dashboard callers
 * that want the same plain-language label can reuse it without
 * re-declaring the switch. */
export { lifecycleTooltip }

function Row({
  path,
  description,
  isLast,
}: {
  path: string
  description: string
  isLast: boolean
}) {
  // D52d follow-up (a11y): each (dt, dd) pair lives inside its own
  // role='listitem' wrapper so the grouping survives a description-list
  // role-strip under display:grid (see parent <dl role='list'> note).
  // We use display:contents on the wrapper so the dt/dd cells still
  // participate in the parent grid layout (no visual change).
  //
  // The connector glyph is purely decorative; semantic content is the
  // <dt> / <dd> pair. SR users hear "path X checks Y" via the aria-label
  // on the listitem; the role-stripped fallback still groups them.
  const connector = isLast ? "└─" : "├─"
  const ariaLabel = `${path} checks ${description}`
  return (
    <div role="listitem" aria-label={ariaLabel} className="contents">
      <dt className="flex items-baseline gap-1.5 font-mono text-[var(--color-text-primary)]">
        <span aria-hidden className="text-[var(--color-text-tertiary)] select-none">
          {connector}
        </span>
        <Code className="text-[11.5px]">{path}</Code>
      </dt>
      <dd className="text-[var(--color-text-secondary)] leading-relaxed">
        <span aria-hidden className="mr-1.5 text-[var(--color-text-tertiary)]">
          {"→"}
        </span>
        {description}
      </dd>
    </div>
  )
}

/** D57e: re-export the lifecycle helper for the wizard's Step 3
 * picker filter. Single source for "does this verifier fire on this
 * lifecycle": descriptors.ts in lib/. */
export { lifecycleGroupsFor }
