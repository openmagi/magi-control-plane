import NavBarShell from "@/components/ui/NavBarShell"
import Footer from "@/components/ui/Footer"

/**
 * Marketing shell — NavBarShell + content + Footer.
 *
 * D36: marketing surfaces follow the Magi design system
 * (dark slate + status-green CTA + Inter typography) per
 * design-system/magi-control-plane/MASTER.md, while the operational
 * dashboard stays on its light/purple console treatment. We scope
 * the dark palette to this layout via inline CSS variables so the
 * existing dashboard tokens stay untouched.
 */
export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="min-h-screen bg-[#0F172A] text-[#F8FAFC] antialiased font-[Inter]"
      style={{
        // Magi DS overrides scoped to marketing. The NavBarShell + Footer
        // read these tokens too, so the whole marketing chrome flips to
        // the dark palette without touching the dashboard tokens.
        ["--color-surface-base" as string]:   "#0F172A",
        ["--color-surface-raised" as string]: "#1E293B",
        ["--color-surface-overlay" as string]:"#1E293B",
        ["--color-surface-input" as string]:  "#0F172A",
        ["--color-border-subtle" as string]:  "rgba(255,255,255,0.08)",
        ["--color-border-strong" as string]:  "rgba(255,255,255,0.12)",
        ["--color-border-focus" as string]:   "#22C55E",
        ["--color-text-primary" as string]:   "#F8FAFC",
        ["--color-text-secondary" as string]: "#CBD5E1",
        ["--color-text-tertiary" as string]:  "#94A3B8",
        ["--color-text-disabled" as string]:  "#64748B",
        ["--color-text-on-accent" as string]: "#062B14",
        ["--color-accent" as string]:         "#22C55E",
        ["--color-accent-light" as string]:   "#4ADE80",
        ["--color-accent-hover" as string]:   "#16A34A",
      }}
    >
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
    </div>
  )
}
