"use client"

import { useFormStatus } from "react-dom"

/**
 * Server-action-aware submit button.
 *
 * While the parent <form action={…}> is pending, the button:
 *   - disables itself
 *   - swaps to `pendingLabel`
 *   - shows a small animated dot string so the operator sees activity
 *     during the multi-second LLM call (compile path runs two sequential
 *     model requests — typical 5–20s).
 *
 * `progressHint` renders only while pending; useful for spelling out
 * what's happening ("LLM compiler + critic running…").
 */
export function SubmitButton({
  label,
  pendingLabel = "Working…",
  progressHint,
  className = "primary",
}: {
  label: string
  pendingLabel?: string
  progressHint?: string
  className?: string
}) {
  const { pending } = useFormStatus()
  return (
    <>
      <button
        type="submit"
        className={className}
        disabled={pending}
        aria-busy={pending}
        style={pending ? { opacity: 0.7, cursor: "wait" } : undefined}
      >
        {pending ? (
          <>
            {pendingLabel}
            <span aria-hidden className="dots-anim" style={{ marginLeft: 6 }}>
              <Dot delay={0} />
              <Dot delay={150} />
              <Dot delay={300} />
            </span>
          </>
        ) : (
          label
        )}
      </button>
      {pending && progressHint && (
        <div
          role="status"
          aria-live="polite"
          className="muted"
          style={{ marginTop: 10, fontSize: 12 }}
        >
          {progressHint}
        </div>
      )}
    </>
  )
}

function Dot({ delay }: { delay: number }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 4,
        height: 4,
        borderRadius: 4,
        background: "currentColor",
        marginLeft: 3,
        animation: "magi-cp-dot 1s infinite ease-in-out",
        animationDelay: `${delay}ms`,
      }}
    />
  )
}
