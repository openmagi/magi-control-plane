import "./globals.css"
import type { Metadata, Viewport } from "next"
import { Instrument_Sans, JetBrains_Mono } from "next/font/google"
import { getLocale, getT } from "@/lib/i18n/server"

/* The Ledger body + mono voices, self-hosted via next/font (no
   render-blocking CDN @import). Referenced by --font-sans / --font-mono in
   globals.css. Cabinet Grotesk (display) loads from Fontshare in <head>. */
const instrumentSans = Instrument_Sans({
  subsets: ["latin"],
  variable: "--font-instrument-sans",
  display: "swap",
})
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
})

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
  themeColor: "#EFEDE8",
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
        <link rel="preconnect" href="https://api.fontshare.com" crossOrigin="anonymous" />
        <link
          rel="stylesheet"
          href="https://api.fontshare.com/v2/css?f[]=cabinet-grotesk@500,700,800&display=swap"
        />
      </head>
      <body className={`${instrumentSans.variable} ${jetbrainsMono.variable}`}>
        <a className="skip-link" href="#main-content">
          {t("nav.skipToMain")}
        </a>
        {children}
      </body>
    </html>
  )
}
