import "./globals.css"
import Link from "next/link"

export const metadata = {
  title: "magi-control-plane",
  description: "Governance dashboard",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <header className="topbar">
          <Link href="/">magi-control-plane</Link>
          <nav aria-label="primary">
            <Link href="/policies">Policies</Link>
            <Link href="/presets">Presets</Link>
            <Link href="/hitl">Review queue</Link>
            <Link href="/ledger">Audit</Link>
          </nav>
        </header>
        <main>{children}</main>
      </body>
    </html>
  )
}
