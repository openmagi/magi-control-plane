"use client"

import { useEffect } from "react"
// Import from the _ds barrel, not "@/components/ui": the top-level barrel
// re-exports NavBarShell, which pulls in server-only i18n (next/headers)
// and cannot be bundled into this Client Component.
import { Button, ErrorState } from "@/components/ui/_ds"

/**
 * Console error boundary. Catches render/data throws from any console
 * page (all `force-dynamic` with blocking cloud fetches) and shows the
 * standard ErrorState with a retry instead of Next's default error
 * screen. Client Component per the App Router error convention, so copy
 * is static English (the app's i18n is server-only).
 */
export default function ConsoleError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    // Surface to the browser console / error reporting; the digest ties
    // a client report back to the server log line.
    console.error(error)
  }, [error])

  return (
    <ErrorState
      status="Error"
      title="Something went wrong on this page."
      body="The console hit an unexpected error. Retry, or check the server logs if it keeps happening."
      actions={
        <Button variant="primary" onClick={() => reset()}>
          Retry
        </Button>
      }
    />
  )
}
