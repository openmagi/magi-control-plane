import NavBarShell from "@/components/ui/NavBarShell"
import Footer from "@/components/ui/Footer"

/**
 * Marketing shell. NavBarShell + content + Footer.
 *
 * D38: tokens scoped to this layout; the cream canvas covers the whole
 * page but the grid pattern is NOT painted at the layout level (the live
 * openmagi.ai site uses sectional background variation, not a single
 * page-wide grid). Page sections opt into the grid only where the
 * pattern reinforces the content (hero). Other sections set their own
 * canvas in `SectionShell`.
 */
export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="min-h-screen antialiased"
      style={{
        ["--canvas" as string]:        "#F7F7F4",
        ["--mist" as string]:          "#F9FAFB",
        ["--ink" as string]:           "#0B0F19",
        ["--night" as string]:         "#111827",
        ["--night-dim" as string]:     "#CBD5E1",
        ["--body" as string]:          "#334155",
        ["--subtle" as string]:        "#475569",
        ["--brand" as string]:         "#0F766E",
        ["--brand-strong" as string]:  "#115E59",
        ["--brand-tint" as string]:    "#F0FDFA",
        ["--brand-ring" as string]:    "#5EEAD4",
        ["--cta" as string]:           "#7C3AED",
        ["--cta-hover" as string]:     "#6D28D9",
        ["--panel" as string]:         "#0E120B",
        ["--panel-2" as string]:       "#12170D",
        ["--panel-border" as string]:  "#2A3119",
        ["--panel-text" as string]:    "#C7D2A8",
        ["--panel-bright" as string]:  "#E7ECD9",
        ["--panel-dim" as string]:     "#8A937A",
        ["--term-bg" as string]:       "#0B0F0A",
        ["--term-prompt" as string]:   "#7EE787",
        ["--term-out" as string]:      "#D7DCC3",
        ["--term-dim" as string]:      "#6B7560",
        ["--term-err" as string]:      "#F08E60",
        ["--color-surface-base" as string]:   "#F7F7F4",
        ["--color-surface-raised" as string]: "#FFFFFF",
        ["--color-surface-overlay" as string]:"#F0FDFA",
        ["--color-text-primary" as string]:   "#0B0F19",
        ["--color-text-secondary" as string]: "#334155",
        ["--color-text-tertiary" as string]:  "#64748B",
        ["--color-text-on-accent" as string]: "#FFFFFF",
        ["--color-accent" as string]:         "#7C3AED",
        ["--color-accent-light" as string]:   "#6D28D9",
        ["--color-accent-hover" as string]:   "#6D28D9",
        ["--color-border-subtle" as string]:  "rgba(11,15,25,0.08)",
        ["--color-border-strong" as string]:  "rgba(11,15,25,0.12)",
        ["--color-border-focus" as string]:   "#7C3AED",
        fontFamily:
          "'Plus Jakarta Sans', 'Apple SD Gothic Neo', 'Noto Sans KR', system-ui, sans-serif",
        backgroundColor: "#FFFFFF",
        color: "#0B0F19",
      }}
    >
      <div className="sticky top-0 z-30 backdrop-blur bg-white/85 border-b border-[var(--color-border-subtle)]">
        <div className="mx-auto" style={{ maxWidth: "var(--content-max)" }}>
          <NavBarShell />
        </div>
      </div>
      <main id="main-content" tabIndex={-1} className="outline-none">
        {children}
      </main>
      <Footer />
    </div>
  )
}
