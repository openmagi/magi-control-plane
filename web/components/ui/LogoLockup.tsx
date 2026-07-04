import Image from "next/image"

/**
 * Open Magi · Control Plane lockup.
 *
 * Binance / Binance Futures sub-product pattern:
 *
 *   [icon]  OpenMagi
 *           ──────
 *           CONTROL PLANE
 *
 * Icon left, then a compact vertical stack: parent wordmark, a thin
 * horizontal rule, the sub-product label. This reads as ONE mark
 * (not two side-by-side wordmarks competing) and keeps the lockup
 * narrow enough to sit cleanly in the nav.
 *
 * The wordmark is rendered as text (not the official lockup PNG) so
 * the rule + sub-label can sit flush under it. The wordmark is set in
 * carbon ink with a verdigris sub-label; the brand orange lives only in
 * the icon mark itself (the PNG), per the one-accent Ledger direction.
 */

export function LogoLockup({
  size = "md",
  className = "",
}: {
  size?: "sm" | "md" | "lg"
  className?: string
}) {
  const dims =
    size === "lg" ? { icon: 44, brand: "text-xl",   sub: "text-[10px]", gap: "gap-3" } :
    size === "sm" ? { icon: 28, brand: "text-sm",   sub: "text-[8px]",  gap: "gap-2" } :
    /* md */         { icon: 36, brand: "text-base", sub: "text-[9px]",  gap: "gap-2.5" }
  return (
    <span
      className={`inline-flex items-center ${dims.gap} ${className}`}
      role="img"
      aria-label="Open Magi · Control Plane"
    >
      <Image
        src="/openmagi-app-icon.png"
        alt=""
        width={dims.icon * 2}
        height={dims.icon * 2}
        style={{ width: dims.icon, height: dims.icon }}
        unoptimized
        priority
        aria-hidden="true"
      />
      <span className="flex flex-col items-start gap-[3px] leading-none">
        <span translate="no" className={`${dims.brand} font-bold tracking-tight leading-none`}>
          <span className="text-[var(--ink)]">Open </span>
          <span className="text-[var(--ink)]">Magi</span>
        </span>
        <span aria-hidden="true" className="block h-px w-full bg-[var(--ink)]/20" />
        <span
          translate="no"
          className={`${dims.sub} uppercase font-semibold tracking-[0.2em] text-[var(--brand)] leading-none`}
        >
          Control Plane
        </span>
      </span>
    </span>
  )
}

export function LogoLockupOnDark({
  size = "md",
  className = "",
}: {
  size?: "sm" | "md" | "lg"
  className?: string
}) {
  const dims =
    size === "lg" ? { icon: 44, brand: "text-xl",   sub: "text-[10px]", gap: "gap-3" } :
    size === "sm" ? { icon: 28, brand: "text-sm",   sub: "text-[8px]",  gap: "gap-2" } :
    /* md */         { icon: 36, brand: "text-base", sub: "text-[9px]",  gap: "gap-2.5" }
  return (
    <span
      className={`inline-flex items-center ${dims.gap} ${className}`}
      role="img"
      aria-label="Open Magi · Control Plane"
    >
      <Image
        src="/openmagi-app-icon.png"
        alt=""
        width={dims.icon * 2}
        height={dims.icon * 2}
        style={{ width: dims.icon, height: dims.icon }}
        unoptimized
        priority
        aria-hidden="true"
      />
      <span className="flex flex-col items-start gap-[3px] leading-none">
        <span translate="no" className={`${dims.brand} font-bold tracking-tight leading-none`}>
          <span className="text-white">Open </span>
          <span className="text-white">Magi</span>
        </span>
        <span aria-hidden="true" className="block h-px w-full bg-white/25" />
        <span
          translate="no"
          className={`${dims.sub} uppercase font-semibold tracking-[0.2em] text-[var(--brand-ring)] leading-none`}
        >
          Control Plane
        </span>
      </span>
    </span>
  )
}
