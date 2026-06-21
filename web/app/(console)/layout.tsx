import NavBarShell from "@/components/ui/NavBarShell"

/**
 * Console shell — placeholder during D1.
 *
 * D2 swaps NavBarShell for the new Sidebar primitive. Until then we
 * reuse the same chrome as marketing so D1 is a visual no-op — only the
 * route-group plumbing moves, content rendering stays byte-equivalent.
 */
export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <NavBarShell />
      <main
        id="main-content"
        tabIndex={-1}
        className="px-5 py-6 mx-auto outline-none"
        style={{ maxWidth: "var(--content-max)" }}
      >
        {children}
      </main>
    </>
  )
}
