import "./globals.css"
import type { Metadata, Viewport } from "next"
import NavBar from "@/components/ui/NavBar"

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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // lang="ko" until /i18n is wired (D1.3). next/font preload is delegated
    // to CDN @font-face for Pretendard + system Inter fallback in globals.css.
    <html lang="ko" suppressHydrationWarning>
      <head>
        {/* CDN preconnect for the Pretendard variable font */}
        <link
          rel="preconnect"
          href="https://cdn.jsdelivr.net"
          crossOrigin="anonymous"
        />
      </head>
      <body>
        <a className="skip-link" href="#main-content">
          Skip to main content
        </a>
        <NavBar />
        <main
          id="main-content"
          tabIndex={-1}
          className="px-5 py-5 mx-auto outline-none"
          style={{ maxWidth: "var(--content-max)" }}
        >
          {children}
        </main>
      </body>
    </html>
  )
}
