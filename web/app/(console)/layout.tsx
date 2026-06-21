import { Sidebar } from "./_components/Sidebar"

/**
 * Console shell: sidebar + content.
 *
 * Desktop only at D2 — mobile collapse (hamburger drawer) lands in D3.
 * The sidebar is sticky on the left and scrolls independently; main
 * content scrolls in its own column up to --content-max.
 */
export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main
        id="main-content"
        tabIndex={-1}
        className="flex-1 min-w-0 px-5 md:px-8 py-6 outline-none"
      >
        <div className="mx-auto" style={{ maxWidth: "var(--content-max)" }}>
          {children}
        </div>
      </main>
    </div>
  )
}
