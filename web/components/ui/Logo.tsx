import Image from "next/image"

/** Brand wordmark — Open Magi logo lockup, ~h-8. */
export function Logo({ className = "" }: { className?: string }) {
  return (
    <Image
      src="/openmagi-logo-lockup.png"
      alt="Open Magi"
      width={1945}
      height={470}
      className={`h-7 w-auto ${className}`}
      unoptimized
      priority
    />
  )
}

/** Brand mark only — square icon, used when space is constrained. */
export function LogoIcon({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <Image
      src="/openmagi-app-icon.png"
      alt="Open Magi"
      width={1024}
      height={1024}
      className={className}
      unoptimized
      priority
    />
  )
}
