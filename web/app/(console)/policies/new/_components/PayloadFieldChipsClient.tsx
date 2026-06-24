"use client"

/**
 * P7 (issue #1): interactive CC hook payload field chips.
 *
 * Two variants share the same chip-row visual:
 *
 *   variant="path"       — chips are <button>s that insert the path
 *                          string at the cursor of `targetTextareaId`.
 *                          Used in the regex / llm_critic forms (the
 *                          author wants the path itself in their
 *                          pattern / NL prompt).
 *   variant="shacl-stub" — chips insert a SHACL `sh:PropertyShape`
 *                          stub (or `sh:NodeShape` for dict / list
 *                          kinds) anchored on the chip's path under
 *                          the canonical `magi:` namespace. Drops the
 *                          stub at the cursor so the author extends a
 *                          shape that is GUARANTEED to find a focus
 *                          node at runtime — closes the vacuous-
 *                          satisfaction footgun the inert <select>
 *                          previously promised but didn't deliver.
 *
 * Why a button (not a <span>) — P1 #7 in the review explicitly calls
 * out the keyboard/aria gap. `<button>` is in the tab order, lands
 * with role="button", and the title/aria-label exposes the type +
 * description to screen readers.
 */

import { useCallback } from "react"

export type ChipField = {
  path: string
  type: "str" | "int" | "bool" | "list" | "dict"
  description: string
  example?: string
  sh_datatype?: string
  sh_kind?: "node" | "property"
  /** D64: friendly display label (KO + EN). When present, the chip
   * renders the locale-matched label as its primary text and keeps
   * the raw `path` in the title= tooltip + aria-label + click-to-
   * insert behaviour. UNKNOWN paths (no entry in the lookup) fall
   * back to showing the raw path verbatim. */
  display_label_ko?: string
  display_label_en?: string
}

type Variant = "path" | "shacl-stub"

interface Props {
  fields: ChipField[]
  targetTextareaId: string
  variant: Variant
  introText: string
  locale: "ko" | "en"
}

const MAGI_NS_PREFIX_BLOCK = `@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://magi.openmagi.ai/cc/hook#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
`

function buildShaclStub(f: ChipField): string {
  // Property shape for scalar leaves; node shape for nested JSON. The
  // hook subject IRI is always materialized by the runtime so
  // `sh:targetSubjectsOf` / `sh:targetClass magi:Hook` always lands on
  // exactly one focus node per hook firing.
  if (f.sh_kind === "node" || f.type === "dict" || f.type === "list") {
    return `\n[] a sh:NodeShape ;\n   sh:targetClass magi:Hook ;\n   sh:property [\n     sh:path        magi:${f.path} ;\n     sh:datatype    ${f.sh_datatype ?? "rdf:JSON"} ;\n     sh:minCount    1\n   ] .\n`
  }
  return `\n[] a sh:PropertyShape ;\n   sh:path        magi:${f.path} ;\n   sh:datatype    ${f.sh_datatype ?? "xsd:string"} ;\n   sh:minCount    1\n   # add sh:pattern / sh:not [sh:pattern "..."] here\n   .\n`
}

function spliceIntoTextarea(
  el: HTMLTextAreaElement | HTMLInputElement,
  insertion: string,
): void {
  const start = el.selectionStart ?? el.value.length
  const end = el.selectionEnd ?? el.value.length
  const before = el.value.slice(0, start)
  const after = el.value.slice(end)
  // Auto-prepend the prefix block for the FIRST SHACL stub so the
  // author doesn't end up with an unparseable shape graph.
  let payload = insertion
  if (
    insertion.includes("magi:") &&
    !el.value.includes("@prefix magi:")
  ) {
    payload = MAGI_NS_PREFIX_BLOCK + insertion
  }
  el.value = before + payload + after
  const caret = (before + payload).length
  el.setSelectionRange(caret, caret)
  el.focus()
  // Fire input so React state (if any) / form validation pick it up.
  el.dispatchEvent(new Event("input", { bubbles: true }))
}

export default function PayloadFieldChipsClient({
  fields, targetTextareaId, variant, introText, locale,
}: Props) {
  const onChipActivate = useCallback(
    (f: ChipField) => {
      const el = document.getElementById(targetTextareaId)
      if (
        !(el instanceof HTMLTextAreaElement) &&
        !(el instanceof HTMLInputElement)
      ) {
        return
      }
      const insertion = variant === "shacl-stub"
        ? buildShaclStub(f)
        : f.path
      spliceIntoTextarea(el, insertion)
    },
    [targetTextareaId, variant],
  )

  if (fields.length === 0) return null
  const ariaInsertVerb = locale === "ko" ? "삽입" : "Insert"

  return (
    <div className="mb-2">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5">
        {introText}
      </p>
      <div className="flex flex-wrap gap-1.5" role="list">
        {fields.map((f) => {
          // D64: friendly display label as primary chip text, raw path
          // moves into title / aria-label / sr-only span. UNKNOWN paths
          // (no lookup entry) fall back to the raw path verbatim so an
          // operator-typed MCP slug still chips honestly.
          //
          // Click-to-insert STAYS the raw path (handled in
          // `onChipActivate`) — operators authoring regex / shacl need
          // the literal field path that the runtime materializes, not
          // a label the runtime doesn't know.
          const friendly =
            (locale === "ko" ? f.display_label_ko : f.display_label_en)
            ?? f.display_label_en
            ?? f.path
          const isFriendly = friendly !== f.path
          const aria = `${ariaInsertVerb} ${friendly} (${f.path}, ${f.type})${
            f.description ? " — " + f.description : ""
          }`
          const title = isFriendly
            ? `${friendly}\n${f.path} (${f.type}) — ${f.description}${
                f.example ? "\n\nexample: " + f.example : ""
              }`
            : `${f.type} — ${f.description}${
                f.example ? "\n\nexample: " + f.example : ""
              }`
          return (
            <button
              key={f.path}
              type="button"
              role="listitem"
              aria-label={aria}
              data-field-path={f.path}
              data-display-label={friendly}
              title={title}
              onClick={() => onChipActivate(f)}
              className="inline-flex items-center gap-1 rounded-md border border-black/[0.08] bg-white px-2 py-0.5 text-[11px] text-[var(--color-text-secondary)] hover:border-[var(--color-accent)]/40 hover:bg-[var(--color-accent)]/[0.04] focus-visible:outline-2 focus-visible:outline-[var(--color-accent)] cursor-pointer"
            >
              <span className={isFriendly ? "" : "font-mono"}>{friendly}</span>
              {isFriendly && (
                <span className="sr-only">
                  {" "}({f.path})
                </span>
              )}
              <span className="text-[10px] text-[var(--color-text-tertiary)]">
                :{f.type}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
