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
      className="mx-auto mt-8 max-w-[var(--content-max)] border-t border-[var(--color-border-subtle)] px-5 pb-8 pt-5 text-sm text-[var(--color-text-tertiary)]"
    >
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          Open Magi Control Plane
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
