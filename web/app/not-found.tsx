import Link from "next/link"
import { Button } from "@/components/ui"

/**
 * Global 404. Renders inside the root layout (no console/marketing
 * shell), so it is self-contained and centered. Static English copy:
 * this is outside the route groups where server i18n runs.
 */
export default function NotFound() {
  return (
    <main
      id="main-content"
      tabIndex={-1}
      className="mx-auto flex min-h-[70vh] max-w-md flex-col items-center justify-center gap-4 px-6 text-center outline-none"
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-text-tertiary)]">
        404
      </p>
      <h1 className="m-0 text-xl font-semibold text-[var(--color-text-primary)]">
        Page not found
      </h1>
      <p className="text-sm text-[var(--color-text-tertiary)]">
        That page does not exist or has moved.
      </p>
      <Link href="/">
        <Button variant="primary">Back to console</Button>
      </Link>
    </main>
  )
}
