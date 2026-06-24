import "./globals.css"
import type { Metadata, Viewport } from "next"
import { getLocale, getT } from "@/lib/i18n/server"

const SITE_URL =
  process.env.MAGI_CP_PUBLIC_SITE_URL ||
  process.env.MAGI_CP_PUBLIC_CLOUD_URL ||
  "https://cp.openmagi.ai"

const SOCIAL_TITLE = "Open Magi Control Plane"
const SOCIAL_DESC =
  "Guardrails for Claude Code. Every tool call, prompt, and session boundary checked against your rules at runtime, sealed into a tamper-evident ledger."
const SOCIAL_IMAGE = `${SITE_URL}/og-image.png`

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: "Open Magi Control Plane",
    template: "%s · Open Magi Control Plane",
  },
  description:
    "Governance over Claude Code. Out-of-loop terminal gate with cryptographic audit trail.",
  openGraph: {
    type: "website",
    siteName: "Open Magi Control Plane",
    url: SITE_URL,
    title: SOCIAL_TITLE,
    description: SOCIAL_DESC,
    images: [{ url: SOCIAL_IMAGE, width: 1200, height: 630, alt: SOCIAL_TITLE }],
  },
  twitter: {
    card: "summary_large_image",
    title: SOCIAL_TITLE,
    description: SOCIAL_DESC,
    images: [SOCIAL_IMAGE],
  },
  icons: {
    icon: [
      { url: "/icon.png", sizes: "any" },
      { url: "/openmagi-app-icon.png", sizes: "1024x1024", type: "image/png" },
    ],
    apple: [{ url: "/openmagi-app-icon.png", sizes: "1024x1024", type: "image/png" }],
  },
}

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#FAFAFA",
}

/**
 * Root layout. minimal shell shared by both route groups:
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
