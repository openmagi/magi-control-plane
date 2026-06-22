"use client"

import { useEffect, useState } from "react"

interface Props {
  formId: string
  inputName: string
  label: string
  hint: string
}

/** D34: Disables submit until at least one checkbox in the given form
 * (identified by formId) with the given name is checked. Used on
 * Step 3 (Condition) so the user gets a client-side guard instead of
 * a server-side error redirect when they uncheck every verifier. */
export default function MinOneSubmit({ formId, inputName, label, hint }: Props) {
  const [count, setCount] = useState(0)

  useEffect(() => {
    const form = document.getElementById(formId)
    if (!(form instanceof HTMLFormElement)) return
    const sync = () => {
      const inputs = form.querySelectorAll<HTMLInputElement>(`input[name="${inputName}"]`)
      let n = 0
      inputs.forEach((el) => { if (el.checked) n++ })
      setCount(n)
    }
    sync()
    form.addEventListener("change", sync)
    return () => form.removeEventListener("change", sync)
  }, [formId, inputName])

  const disabled = count === 0
  return (
    <div className="space-y-1">
      <button
        type="submit"
        disabled={disabled}
        className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[var(--color-accent)] px-5 py-3 text-sm font-semibold text-white shadow-sm hover:bg-[var(--color-accent-hover)] disabled:cursor-not-allowed disabled:opacity-50 cursor-pointer transition-colors"
      >
        {label}
      </button>
      {disabled && (
        <p className="text-[11px] text-[var(--color-text-tertiary)] text-center">
          {hint}
        </p>
      )}
    </div>
  )
}
