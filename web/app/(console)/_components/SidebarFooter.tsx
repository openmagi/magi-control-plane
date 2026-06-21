import LangSelect from "@/components/ui/LangSelect"
import { ArrowTopRightOnSquareIcon } from "@heroicons/react/24/outline"

/**
 * Sidebar footer: locale switcher + GitHub external link. Matches
 * magi-agent's border-top divider + LanguageSwitcher pattern.
 */
export function SidebarFooter() {
  return (
    <div className="border-t border-black/[0.06] pt-4 mt-2 flex flex-col gap-3">
      <LangSelect />
      <a
        href="https://github.com/openmagi/magi-control-plane"
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 px-3 text-xs font-medium text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)] hover:no-underline transition-colors duration-150"
      >
        GitHub
        <ArrowTopRightOnSquareIcon aria-hidden="true" className="w-3 h-3" />
      </a>
    </div>
  )
}
