import LangSelect from "@/components/ui/LangSelect"
import { ArrowTopRightOnSquareIcon } from "@heroicons/react/24/outline"

/**
 * Sidebar footer: locale switcher + GitHub external link.
 *
 * Server component. LangSelect ships its own server action so the
 * locale change is a normal form submission, no client state needed.
 */
export function SidebarFooter() {
  return (
    <div className="mt-auto px-3 pt-4 pb-4 border-t border-[var(--color-border-subtle)] flex flex-col gap-3">
      <LangSelect />
      <a
        href="https://github.com/openmagi/magi-control-plane"
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)] hover:no-underline transition-colors duration-150"
      >
        GitHub
        <ArrowTopRightOnSquareIcon aria-hidden="true" className="w-3 h-3" />
      </a>
    </div>
  )
}
