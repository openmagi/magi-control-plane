import NavBarShell from "@/components/ui/NavBarShell"
import Footer from "@/components/ui/Footer"

/**
 * Marketing shell — NavBarShell + content + Footer.
 *
 * Used by /welcome, /install, /legal/{terms,privacy}. Different audience
 * (potential customers + curious visitors) needs different chrome than
 * the operational console (Sidebar + content).
 */
export default function MarketingLayout({ children }: { children: React.ReactNode }) {
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
      <Footer />
    </>
  )
}
