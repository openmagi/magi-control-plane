"use client"

/**
 * P7 (issue #1): interactive CC hook payload field chips.
 *
 * Four variants share the same chip-row visual:
 *
 *   variant="path"        : chips are <button>s that insert the raw
 *                           path string at the cursor of
 *                           `targetTextareaId`. Legacy default still
 *                           used by step-ref / generic forms.
 *   variant="llm-marker"  : D82c. LLM critic textarea. Chips insert
 *                           `{<path>}` at the cursor so the runtime
 *                           marker substitutor (cloud verify_inline)
 *                           recognises the field and splices in the
 *                           live payload value before the LLM critic
 *                           sees the prompt. Without the brace marker
 *                           the operator can't tell where one variable
 *                           ends and another begins.
 *   variant="regex-target": D82c. Regex condition split into target-
 *                           field picker + pattern textarea. The chip
 *                           click sets the value of a separate `<select>`
 *                           identified by `targetSelectId`; the pattern
 *                           textarea is left alone (curly markers would
 *                           break the regex).
 *   variant="shacl-stub"  : chips insert a SHACL `sh:PropertyShape`
 *                           stub (or `sh:NodeShape` for dict / list
 *                           kinds) anchored on the chip's path under
 *                           the canonical `magi:` namespace. Drops the
 *                           stub at the cursor so the author extends a
 *                           shape that is GUARANTEED to find a focus
 *                           node at runtime, closes the vacuous-
 *                           satisfaction footgun the inert <select>
 *                           previously promised but didn't deliver.
 *
 * Why a button (not a <span>): P1 #7 in the review explicitly calls
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

// D82c fix: export so the page-side InlineSubConfigPanel (and any
// future caller) imports the same union the chip renderer matches
// against. Without this the panel's local `'path' | 'shacl-stub'`
// narrow silently masked a missing `'llm-marker'` branch (the panel
// could pass a too-narrow value at build time, then run-time inserted
// the raw path without curly braces, the exact failure mode the Step
// 3 split was added to prevent).
export type Variant = "path" | "shacl-stub" | "llm-marker" | "regex-target"

interface Props {
  fields: ChipField[]
  targetTextareaId: string
  variant: Variant
  introText: string
  locale: "ko" | "en"
  /** D82c: for variant="regex-target", id of the `<select>` whose
   * value the chip click should set. Ignored by other variants. */
  targetSelectId?: string
}

const MAGI_NS_PREFIX_BLOCK = `@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://magi.openmagi.ai/cc/hook#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
`

// D82c fix: mirror the runtime `_MARKER_RX` so chip insertion under
// the llm-marker variant cannot emit a span the substitutor would
// fail to recognise. Identifier-style dotted paths only (no `[]` /
// `-` / spaces); paths the regex rejects fall back to raw-path
// insertion + a dev-console warning.
const _MARKER_PATH_RX =
  /^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$/

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
  fields, targetTextareaId, variant, introText, locale, targetSelectId,
}: Props) {
  const onChipActivate = useCallback(
    (f: ChipField) => {
      // D82c: variant="regex-target" routes the chip click to a
      // separate <select> instead of splicing into a textarea. We
      // can't curly-wrap the regex pattern (the brace would break
      // the pattern compile), so the chip becomes the target-field
      // picker AND the pattern textarea stays untouched.
      if (variant === "regex-target") {
        const id = targetSelectId ?? ""
        const sel = document.getElementById(id)
        if (sel instanceof HTMLSelectElement) {
          // Add an option lazily when the chip-picked path isn't in
          // the static <option> list (e.g. operator-typed MCP slugs).
          const existing = Array.from(sel.options).some((o) => o.value === f.path)
          if (!existing) {
            const opt = document.createElement("option")
            opt.value = f.path
            opt.textContent = f.path
            sel.appendChild(opt)
          }
          sel.value = f.path
          sel.dispatchEvent(new Event("change", { bubbles: true }))
          return
        }
        // D82c fix: the select lookup failed (mismatched id, the form
        // re-rendered before the click landed, or the variant was
        // wired without `targetSelectId`). Fall back to splicing the
        // raw path into the pattern textarea so the click still does
        // something visible. Operators clicking a chip and seeing
        // nothing happen would otherwise reasonably conclude the
        // picker is broken. We log a one-line dev hint so the wiring
        // bug surfaces in the console without spamming users.
        if (typeof console !== "undefined") {
          // eslint-disable-next-line no-console
          console.warn(
            `[PayloadFieldChips] regex-target select not found `
            + `(id=${id || "<unset>"}); falling back to path insertion`,
          )
        }
        const fallback = document.getElementById(targetTextareaId)
        if (
          fallback instanceof HTMLTextAreaElement ||
          fallback instanceof HTMLInputElement
        ) {
          spliceIntoTextarea(fallback, f.path)
        }
        return
      }
      const el = document.getElementById(targetTextareaId)
      if (
        !(el instanceof HTMLTextAreaElement) &&
        !(el instanceof HTMLInputElement)
      ) {
        return
      }
      let insertion: string
      if (variant === "shacl-stub") {
        insertion = buildShaclStub(f)
      } else if (variant === "llm-marker") {
        // D82c: wrap path in curly braces so the runtime marker
        // substitutor recognises it and the operator can SEE where
        // the variable ends in the rendered prompt.
        //
        // D82c fix: the runtime regex `_MARKER_RX` only accepts
        // dotted-identifier paths. A path containing `[]` / `-` /
        // any non-identifier char would be inserted as a literal
        // `{citations[].quote}` and the regex would NOT match it,
        // and the LLM would see the literal braces (the exact failure
        // mode this commit set out to prevent). Today no `_*_FIELDS`
        // surfaces such a path, but the display-label table already
        // names `citations[].quote` and a future custom verifier
        // exposing it would fire this. Fall back to inserting the
        // raw path (NO braces) so the prompt at least stays readable;
        // log a dev hint so the wiring bug surfaces in the console.
        if (!_MARKER_PATH_RX.test(f.path)) {
          if (typeof console !== "undefined") {
            // eslint-disable-next-line no-console
            console.warn(
              "[PayloadFieldChips] llm-marker variant does not support "
              + `path "${f.path}": not a dotted identifier. Inserting `
              + "raw path; the marker substitutor will NOT resolve it.",
            )
          }
          insertion = f.path
        } else {
          insertion = `{${f.path}}`
        }
      } else {
        insertion = f.path
      }
      spliceIntoTextarea(el, insertion)
    },
    [targetTextareaId, variant, targetSelectId],
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
          // `onChipActivate`): operators authoring regex / shacl need
          // the literal field path that the runtime materializes, not
          // a label the runtime doesn't know.
          const friendly =
            (locale === "ko" ? f.display_label_ko : f.display_label_en)
            ?? f.display_label_en
            ?? f.path
          const isFriendly = friendly !== f.path
          // Aria-label leads with the raw path (the actual click-to-insert
          // target) and trails with the friendly cue, so SR users hear what
          // will land at the cursor first and the human-readable label after.
          // No em-dashes per the project no-em-dash hard rule (top AI-tell).
          const aria = isFriendly
            ? `${ariaInsertVerb} ${f.path}, ${friendly} (${f.type})${
                f.description ? ": " + f.description : ""
              }`
            : `${ariaInsertVerb} ${f.path} (${f.type})${
                f.description ? ": " + f.description : ""
              }`
          const title = isFriendly
            ? `${friendly}\n${f.path} (${f.type}): ${f.description}${
                f.example ? "\n\nexample: " + f.example : ""
              }`
            : `${f.type}: ${f.description}${
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
