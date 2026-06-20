import "./globals.css"
import type { Metadata, Viewport } from "next"
import NavBarShell from "@/components/ui/NavBarShell"
import Footer from "@/components/ui/Footer"
import { getT } from "@/lib/i18n/server"

export const metadata: Metadata = {
  title: {
    default: "magi-control-plane",
    template: "%s · magi-control-plane",
  },
  description:
    "Governance over Claude Code — out-of-loop terminal gate with cryptographic audit trail.",
}

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0c0d10",
  // explicitly DO NOT disable zoom — accessibility (rule violation if maximumScale=1)
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const { locale, t } = await getT()
  return (
    <html lang={locale} suppressHydrationWarning>
      <head>
        <link
          rel="preconnect"
          href="https://cdn.jsdelivr.net"
          crossOrigin="anonymous"
        />
      </head>
      <body>
        <a className="skip-link" href="#main-content">
          {t("nav.skipToMain")}
        </a>
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
      </body>
    </html>
  )
}
