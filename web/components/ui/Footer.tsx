import Link from "next/link"
import { getLocale } from "@/lib/i18n/server"

/** Minimal site footer with legal links + contact. Server component. */
export default function Footer() {
  const locale = getLocale()
  const labelAbout   = locale === "ko" ? "소개"          : "About"
  const labelInstall = locale === "ko" ? "설치"          : "Install"
  const labelTerms   = locale === "ko" ? "이용약관"       : "Terms"
  const labelPrivacy = locale === "ko" ? "개인정보처리방침" : "Privacy"
  const labelContact = locale === "ko" ? "문의"          : "Contact"
  const labelGithub  = "GitHub"
  return (
    <footer
      role="contentinfo"
      className="mt-12 border-t border-[var(--color-border-subtle)] py-6 text-sm text-[var(--color-text-tertiary)]"
      style={{ maxWidth: "var(--content-max)", margin: "32px auto 0", padding: "20px 20px 32px" }}
    >
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          OpenMagi · magi-control-plane · alpha pilot
        </div>
        <nav aria-label="Footer" className="flex flex-wrap gap-4">
          <Link href="/welcome" className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]">
            {labelAbout}
          </Link>
          <Link href="/install" className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]">
            {labelInstall}
          </Link>
          <Link href="/legal/terms" className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]">
            {labelTerms}
          </Link>
          <Link href="/legal/privacy" className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]">
            {labelPrivacy}
          </Link>
          <a
            href="mailto:kevin@openmagi.ai"
            className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]"
          >
            {labelContact}
          </a>
          <a
            href="https://github.com/openmagi/magi-control-plane"
            target="_blank" rel="noreferrer"
            className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]"
          >
            {labelGithub}
          </a>
        </nav>
      </div>
    </footer>
  )
}
