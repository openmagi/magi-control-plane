import "./globals.css"
import type { Metadata, Viewport } from "next"
import { getLocale, getT } from "@/lib/i18n/server"

export const metadata: Metadata = {
  title: {
    default: "magi-control-plane",
    template: "%s · magi-control-plane",
  },
  description:
    "Governance over Claude Code. Out-of-loop terminal gate with cryptographic audit trail.",
}

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#FAFAFA",
}

/**
 * Root layout — minimal shell shared by both route groups:
 *   (marketing) → NavBarShell + Footer (see app/(marketing)/layout.tsx)
 *   (console)   → Sidebar + content    (see app/(console)/layout.tsx)
 *
 * The skip-link and the font preconnect live here because both shells
 * need them. The actual chrome lives in the group layouts.
 */
export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const locale = await getLocale()
  const { t } = await getT()
  return (
    <html lang={locale} suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://cdn.jsdelivr.net" crossOrigin="anonymous" />
      </head>
      <body>
        <a className="skip-link" href="#main-content">
          {t("nav.skipToMain")}
        </a>
        {children}
      </body>
    </html>
  )
}
