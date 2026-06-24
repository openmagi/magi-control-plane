"use client"

import { useState } from "react"

const SENTINEL_TAG_DEFAULT = "GATE"

interface Props {
  initialMode: "tag" | "custom"
  initialTag: string
  initialCustom: string
  labels: {
    modeLabel: string
    modeTag: string
    modeCustom: string
    tagFieldLabel: string
    tagFieldHint: string
    tagPreviewIntro: string
    customFieldLabel: string
    customFieldHint: string
    customGroupsHint: string
  }
}

/** D34: Sentinel authoring with tag/custom mode toggle.
 *
 * Client component because the visible field swaps reactively on radio
 * change. without state, the user would have to refresh the page to
 * switch modes. The form submit serialises `sentinel_mode`,
 * `sentinel_tag`, and `sentinel_re_custom` separately; saveWizard
 * picks the right one to assemble the final sentinel_re. */
export default function SentinelModeSection({
  initialMode, initialTag, initialCustom, labels,
}: Props) {
  const [mode, setMode] = useState<"tag" | "custom">(initialMode)
  const [tag, setTag] = useState(initialTag || SENTINEL_TAG_DEFAULT)
  const [custom, setCustom] = useState(initialCustom)
  // PR4: the matter/doc_id named-group requirement is gone (PR1 dropped
  // it at the cloud layer). Any non-empty custom regex is allowed; the
  // dashboard no longer asserts a specific named-group shape.
  const customOk = custom.length > 0

  return (
    <div className="space-y-3">
      <input type="hidden" name="sentinel_mode" value={mode} />

      <fieldset>
        <legend className="block text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5">
          {labels.modeLabel}
        </legend>
        <div className="flex flex-wrap gap-2">
          <label className="cursor-pointer">
            <input
              type="radio"
              name="_sentinel_mode_ui"
              value="tag"
              checked={mode === "tag"}
              onChange={() => setMode("tag")}
              className="peer sr-only"
            />
            <span className="inline-flex items-center rounded-full border border-black/[0.08] bg-white px-3 py-1 text-xs font-semibold text-[var(--color-text-secondary)] hover:border-[var(--color-accent)]/40 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.06] peer-checked:text-[var(--color-accent-light)]">
              {labels.modeTag}
            </span>
          </label>
          <label className="cursor-pointer">
            <input
              type="radio"
              name="_sentinel_mode_ui"
              value="custom"
              checked={mode === "custom"}
              onChange={() => setMode("custom")}
              className="peer sr-only"
            />
            <span className="inline-flex items-center rounded-full border border-black/[0.08] bg-white px-3 py-1 text-xs font-semibold text-[var(--color-text-secondary)] hover:border-[var(--color-accent)]/40 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.06] peer-checked:text-[var(--color-accent-light)]">
              {labels.modeCustom}
            </span>
          </label>
        </div>
      </fieldset>

      {mode === "tag" ? (
        <div>
          <label htmlFor="w-sentinel-tag" className="block text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5">
            {labels.tagFieldLabel}
          </label>
          <input
            id="w-sentinel-tag"
            name="sentinel_tag"
            value={tag}
            onChange={(e) => setTag(e.target.value.toUpperCase())}
            maxLength={32}
            pattern="[A-Z][A-Z0-9_]{0,31}"
            placeholder={SENTINEL_TAG_DEFAULT}
            spellCheck={false}
            autoComplete="off"
            className="w-full rounded-xl border border-black/[0.08] bg-white px-4 py-3 text-base leading-6 text-[var(--color-text-primary)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/20 font-mono"
          />
          <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
            {labels.tagPreviewIntro}{" "}
            <code className="font-mono">
              {(tag || SENTINEL_TAG_DEFAULT)}_(?P&lt;subject&gt;…)_(?P&lt;payload_hash&gt;…)
            </code>
          </p>
        </div>
      ) : (
        <div>
          <label htmlFor="w-sentinel-custom" className="block text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5">
            {labels.customFieldLabel}
          </label>
          <textarea
            id="w-sentinel-custom"
            name="sentinel_re_custom"
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            maxLength={2000}
            rows={3}
            spellCheck={false}
            autoComplete="off"
            placeholder={`AKIA(?P<subject>[A-Z0-9]{16})(?P<payload_hash>.*)`}
            className="w-full rounded-xl border border-black/[0.08] bg-white px-4 py-3 text-sm leading-5 text-[var(--color-text-primary)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/20 font-mono"
          />
          <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
            {labels.customFieldHint}
          </p>
          {custom.length > 0 && !customOk && (
            <p className="mt-1 text-xs text-rose-700">
              {labels.customGroupsHint}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
