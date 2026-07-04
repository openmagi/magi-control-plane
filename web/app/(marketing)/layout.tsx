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
        // Marketing-local vars. Ground + ink converge on The Ledger
        // (limestone/carbon), brand + CTA collapse to the single oxide
        // verdigris accent. The dark contract panel + terminal mockup
        // vars stay (they are a deliberate dark surface, not the accent).
        // The semantic --color-* tokens are intentionally NOT overridden
        // here: marketing DS components inherit the vendored Ledger tokens
        // so the whole site renders one palette.
        ["--canvas" as string]:        "#EFEDE8",
        ["--mist" as string]:          "#F9FAFB",
        ["--ink" as string]:           "#1B1D22",
        ["--night" as string]:         "#111827",
        ["--night-dim" as string]:     "#CBD5E1",
        ["--body" as string]:          "#334155",
        ["--subtle" as string]:        "#475569",
        ["--brand" as string]:         "#17635A",
        ["--brand-strong" as string]:  "#14564E",
        ["--brand-tint" as string]:    "#E9F1EF",
        ["--brand-ring" as string]:    "#5FB3A6",
        ["--cta" as string]:           "#17635A",
        ["--cta-hover" as string]:     "#14564E",
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
        backgroundColor: "transparent",
      }}
    >
      <NavBarShell />
      <main id="main-content" tabIndex={-1} className="outline-none">
        {children}
      </main>
      <Footer />
    </div>
  )
}
