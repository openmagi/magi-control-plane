import type { ReactNode } from "react"

/**
 * D78: small inline callout used by docs pages.
 *
 * Three semantic tones, no chrome beyond a thin left rule + tint so
 * the docs pages stay scannable. Body is plain children so callers
 * can pass <p> + <ul> mixes without us needing a richer API.
 */
export interface CalloutAsideProps {
  tone?: "note" | "warn" | "tip"
  title?: ReactNode
  children: ReactNode
}

const TONES: Record<NonNullable<CalloutAsideProps["tone"]>, string> = {
  note: "border-l-[var(--color-accent)] bg-[var(--color-accent)]/5",
  warn: "border-l-amber-500 bg-amber-50",
  tip:  "border-l-emerald-500 bg-emerald-50/60",
}

const DOT: Record<NonNullable<CalloutAsideProps["tone"]>, string> = {
  note: "bg-[var(--color-accent)]",
  warn: "bg-amber-500",
  tip:  "bg-emerald-500",
}

export function CalloutAside({
  tone = "note", title, children,
}: CalloutAsideProps) {
  return (
    <aside
      role="note"
      className={
        "my-4 border-l-4 px-4 py-3 rounded-r-md text-sm leading-6 " +
        TONES[tone]
      }
    >
      {title && (
        <div className="mb-1 flex items-center gap-2 font-semibold text-[var(--color-text-primary)]">
          <span aria-hidden="true" className={"inline-block h-2 w-2 rounded-full " + DOT[tone]} />
          {title}
        </div>
      )}
      <div className="text-[var(--color-text-secondary)]">{children}</div>
    </aside>
  )
}
