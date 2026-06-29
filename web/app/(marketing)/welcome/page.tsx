import type { Metadata } from "next"
import Link from "next/link"
import { getLocale } from "@/lib/i18n/server"

export const dynamic = "force-dynamic"

/** Bump when og-image.png changes visibly. The OG image URL embeds
 *  this version as a query string so Telegram / Slack / X crawlers
 *  drop their cached preview and refetch on the next link share. */
const OG_IMAGE_VERSION = "3"

export const metadata: Metadata = {
  title: "Open Magi · Control Plane: Guardrails for Claude Code",
  description:
    "Every action your agent takes (tool calls, prompts, session boundaries) is checked against your rules at runtime. Block, ask a human, or audit. All sealed in a tamper-evident ledger.",
  openGraph: {
    title: "Open Magi · Control Plane: Guardrails for Claude Code",
    description:
      "Run Claude Code on systems that matter. Magi catches every agent action before it ships, with rules you author in the dashboard. No agent changes needed.",
    type: "website",
    locale: "ko_KR",
    alternateLocale: "en_US",
    // Next.js merges page metadata SHALLOWLY onto the layout's metadata,
    // so a page that exports its own openGraph block wipes out the
    // layout's openGraph.images by definition. We have to re-declare
    // the image here. Absolute URL because Telegram + several other
    // crawlers refuse to resolve relative og:image paths.
    //
    // OG_IMAGE_VERSION is the cache-buster. Bumping it forces Telegram /
    // Slack / X crawlers to refetch the image instead of serving their
    // stale cached copy. Increment whenever og-image.png changes
    // visibly.
    images: [{
      url: `https://cp.openmagi.ai/og-image.png?v=${OG_IMAGE_VERSION}`,
      width: 1200,
      height: 630,
      alt: "Open Magi Control Plane: Guardrails for Claude Code",
    }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Open Magi · Control Plane: Guardrails for Claude Code",
    description:
      "Run Claude Code on systems that matter. Magi catches every agent action before it ships, with rules you author in the dashboard.",
    images: [`https://cp.openmagi.ai/og-image.png?v=${OG_IMAGE_VERSION}`],
  },
  alternates: { canonical: "/welcome" },
  robots: { index: true, follow: true },
}

/** D38 marketing landing.
 *  Sectional background variation (cream + grid hero / white / cream-tint
 *  band / dark olive CTA band) lifts the page off the flat grid-covered
 *  D37 layout. Right column of the hero is a Claude Code TUI mock that
 *  shows the gate intercepting a real tool call, replacing the abstract
 *  policy IR card. How / pricing / install paths are friendlier with
 *  command previews and concrete next-step buttons. */
export default async function WelcomePage() {
  const locale = await getLocale()
  const isKo = locale === "ko"
  const C = isKo ? KO : EN
  return (
    <div>
      <Hero c={C.hero} isKo={isKo} />
      <SectionShell tone="white">
        <Capabilities c={C.capabilities} />
      </SectionShell>
      <SectionShell tone="tint">
        <Why c={C.why} />
      </SectionShell>
      <SectionShell tone="white">
        <How c={C.how} />
      </SectionShell>
      <SectionShell tone="tint">
        <FAQ c={C.faq} />
      </SectionShell>
      <SectionShell tone="dark">
        <CTA c={C.cta} />
      </SectionShell>
    </div>
  )
}

// ── types ──────────────────────────────────────────────────────────
type HeroCopy = {
  chips: string[]
  title: string
  subtitle: string
  cta: string
  bullets: string[]
  toolsLabel: string
  tools: string[]
  tui: TuiCopy
}
type TuiCopy = {
  windowTitle: string
  userPrompt: string
  toolCall: string
  toolEcho: string
  verdictLabel: string
  ruleLabel: string
  ruleValue: string
  evidenceLabel: string
  evidenceValue: string
  ledgerLabel: string
  ledgerValue: string
  hint: string
  userReply: string
  hitlLabel: string
  reviewerLabel: string
  reviewerValue: string
  linkLabel: string
  linkValue: string
  statusLabel: string
  statusValue: string
  statusBar: string
  caption: string
}
type CapabilitiesCopy = {
  eyebrow: string
  heading: string; sub: string
  groups: Array<{ icon: "clock" | "shield" | "code"; label: string; tagline: string; items: string[] }>
}
type WhyCopy = { eyebrow: string; heading: string; items: Array<{ q: string; a: string }> }
type HowCopy = {
  eyebrow: string; heading: string; sub: string
  steps: Array<{ n: string; title: string; body: string; cmd?: string; cmdLabel?: string }>
}
type FAQCopy = { eyebrow: string; heading: string; items: Array<{ q: string; a: string }> }
type CTACopy = { heading: string; body: string; cta: string; ctaSecondary: string }

// ── shells ─────────────────────────────────────────────────────────
function SectionShell({
  tone,
  children,
}: {
  tone: "white" | "tint" | "dark"
  children: React.ReactNode
}) {
  const bg =
    tone === "white" ? "bg-white" :
    tone === "tint"  ? "bg-[var(--canvas)]" :
    /* dark */         "bg-[var(--panel)]"
  const border =
    tone === "white" ? "border-[var(--color-border-subtle)]" :
    tone === "tint"  ? "border-[var(--color-border-subtle)]" :
    /* dark */         "border-transparent"
  return (
    <section className={`relative ${bg} border-t ${border}`}>
      <div
        className="mx-auto px-5 md:px-8 py-16 md:py-24"
        style={{ maxWidth: "var(--content-max)" }}
      >
        {children}
      </div>
    </section>
  )
}

// ── visual primitives ─────────────────────────────────────────────
function PrimaryCTA({ href, children, size = "md" }: { href: string; children: React.ReactNode; size?: "md" | "lg" }) {
  const external = href.startsWith("http")
  const sizeCls = size === "lg" ? "px-7 py-3.5 text-base" : "px-5 py-3 text-sm"
  const cls =
    `inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg bg-[var(--cta)] ${sizeCls} ` +
    "font-semibold text-white hover:bg-[var(--cta-hover)] " +
    "shadow-[0_0_16px_rgba(124,58,237,0.3),0_0_32px_rgba(124,58,237,0.1)] " +
    "transition-colors duration-200 cursor-pointer " +
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--cta)] " +
    "focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--canvas)]"
  if (external) return <a href={href} target="_blank" rel="noopener noreferrer" className={cls}>{children}<Arrow /></a>
  return <Link href={href} prefetch={false} className={cls}>{children}<Arrow /></Link>
}

function GhostCTA({ href, children, size = "md" }: { href: string; children: React.ReactNode; size?: "md" | "lg" }) {
  const external = href.startsWith("http")
  const sizeCls = size === "lg" ? "px-6 py-3.5 text-base" : "px-5 py-3 text-sm"
  const cls =
    `inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg border border-[var(--ink)]/10 bg-white ${sizeCls} ` +
    "font-semibold text-[var(--ink)] " +
    "hover:border-[var(--ink)]/25 hover:bg-[var(--mist)] transition-colors duration-200 cursor-pointer " +
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ink)]/30 " +
    "focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--canvas)]"
  if (external) return <a href={href} target="_blank" rel="noopener noreferrer" className={cls}>{children}<Arrow /></a>
  return <Link href={href} prefetch={false} className={cls}>{children}<Arrow /></Link>
}

function Arrow() {
  return (
    <svg aria-hidden="true" className="w-4 h-4" viewBox="0 0 20 20" fill="none">
      <path d="M4 10h11M11 6l4 4-4 4" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-lg border border-[var(--ink)]/10 bg-white/80 px-3 py-1.5 text-[11px] font-bold uppercase tracking-[0.14em] text-[var(--body)]">
      {children}
    </span>
  )
}

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[12px] font-bold uppercase tracking-[0.18em] text-[var(--brand)]">
      {children}
    </p>
  )
}

function SectionHead({ eyebrow, heading, sub }: { eyebrow?: string; heading: string; sub?: string }) {
  return (
    <div className="text-center">
      {eyebrow && <Eyebrow>{eyebrow}</Eyebrow>}
      <h2 className="mt-3 text-3xl md:text-4xl font-black text-[var(--ink)] tracking-tight text-balance">
        {heading}
      </h2>
      {sub && (
        <p className="mt-3 mx-auto max-w-2xl text-sm md:text-base text-[var(--subtle)] text-pretty">
          {sub}
        </p>
      )}
    </div>
  )
}

function CheckGreen({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg aria-hidden="true" className={`${className} text-[var(--brand)]`} viewBox="0 0 20 20" fill="currentColor">
      <path d="M16.7 5.3a1 1 0 010 1.4l-7 7a1 1 0 01-1.4 0l-3.5-3.5a1 1 0 011.4-1.4l2.8 2.8 6.3-6.3a1 1 0 011.4 0z" />
    </svg>
  )
}

function CapabilityIcon({ kind, className = "w-5 h-5" }: { kind: "clock" | "shield" | "code"; className?: string }) {
  const common = `${className} text-[var(--brand)]`
  if (kind === "clock") {
    return (
      <svg aria-hidden="true" className={common} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3.2 2" />
      </svg>
    )
  }
  if (kind === "shield") {
    return (
      <svg aria-hidden="true" className={common} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 3l8 3v6c0 4.5-3.4 8.4-8 9.5C7.4 20.4 4 16.5 4 12V6l8-3z" />
        <path d="M9.5 12.2l1.9 1.9 3.6-3.6" />
      </svg>
    )
  }
  return (
    <svg aria-hidden="true" className={common} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 6l-5 6 5 6" />
      <path d="M16 6l5 6-5 6" />
      <path d="M14 4l-4 16" />
    </svg>
  )
}

// ── hero ───────────────────────────────────────────────────────────
function Hero({ c, isKo }: { c: HeroCopy; isKo: boolean }) {
  return (
    <section
      className="relative border-b border-[var(--color-border-subtle)]"
      style={{
        backgroundColor: "var(--canvas)",
        backgroundImage:
          "linear-gradient(to right, rgba(17,24,39,0.05) 1px, transparent 1px), linear-gradient(to bottom, rgba(17,24,39,0.05) 1px, transparent 1px)",
        backgroundSize: "44px 44px",
      }}
    >
      <div
        className="mx-auto px-5 md:px-8 pt-14 md:pt-20 pb-16 md:pb-24"
        style={{ maxWidth: "var(--content-max)" }}
      >
        <div className="grid gap-8 md:gap-6 lg:gap-10 md:grid-cols-[1.2fr_1fr] items-start">
          {/* Left: copy column */}
          <div>
            <div className="flex flex-wrap gap-2">
              {c.chips.map((chip) => <Chip key={chip}>{chip}</Chip>)}
            </div>
            <h1
              className={`mt-6 text-5xl md:text-[64px] font-black tracking-tight text-balance text-[var(--ink)] leading-[1.08] md:leading-[1.06] ${
                isKo ? "max-w-[22ch]" : "max-w-[14ch]"
              }`}
            >
              {c.title}
            </h1>
            <p className="mt-5 max-w-xl text-base md:text-lg text-pretty text-[var(--body)] leading-7">
              {c.subtitle}
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <PrimaryCTA size="lg" href="/install">{c.cta}</PrimaryCTA>
            </div>
            <ul className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-[var(--ink)]">
              {c.bullets.map((b) => (
                <li key={b} className="inline-flex items-center gap-1.5">
                  <CheckGreen />
                  <span>{b}</span>
                </li>
              ))}
            </ul>
            <p className="mt-6 text-xs text-[var(--subtle)] tracking-wide">
              {c.toolsLabel}{" "}
              <span translate="no" className="font-medium text-[var(--ink)]">{c.tools.join(" · ")}</span>
            </p>
          </div>

          {/* Right: Claude Code TUI mock */}
          <div className="w-full md:max-w-[560px] md:justify-self-end">
            <TuiDemo c={c.tui} />
          </div>
        </div>
      </div>
    </section>
  )
}

// ── tui demo ───────────────────────────────────────────────────────
function TuiDemo({ c }: { c: TuiCopy }) {
  return (
    <figure className="m-0">
      <div
        className="rounded-xl border border-[var(--panel-border)] bg-[var(--term-bg)] shadow-[0_28px_70px_-22px_rgba(15,23,42,0.35)] overflow-hidden"
        translate="no"
      >
        {/* macOS-style chrome */}
        <div className="flex items-center gap-1.5 px-3.5 py-2.5 border-b border-[var(--panel-border)]/80">
          <span aria-hidden="true" className="w-2.5 h-2.5 rounded-full bg-[#FF5F57]" />
          <span aria-hidden="true" className="w-2.5 h-2.5 rounded-full bg-[#FEBC2E]" />
          <span aria-hidden="true" className="w-2.5 h-2.5 rounded-full bg-[#28C840]" />
          <span className="ml-3 text-[11px] font-mono text-[var(--term-dim)] tracking-tight">
            {c.windowTitle}
          </span>
        </div>
        <pre className="m-0 px-5 py-5 text-[12.5px] leading-[1.7] font-mono text-[var(--term-out)] whitespace-pre overflow-x-auto">
{`> `}<span className="text-[var(--panel-bright)]">{c.userPrompt}</span>{`

`}<span className="text-[var(--term-prompt)]">●</span>{` `}<span className="text-[var(--panel-bright)]">{c.toolCall}</span>{`
  `}<span className="text-[var(--term-dim)]">⎿</span>{`  `}<span className="text-[var(--term-dim)]">{c.toolEcho}</span>{`

`}<span className="text-[var(--term-err)]">✗ {c.verdictLabel}</span>{`
  `}<span className="text-[var(--term-dim)]">{c.ruleLabel.padEnd(10)}</span>{` `}<span className="text-[var(--panel-bright)]">{c.ruleValue}</span>{`
  `}<span className="text-[var(--term-dim)]">{c.evidenceLabel.padEnd(10)}</span>{` `}<span className="text-[var(--term-out)]">{c.evidenceValue}</span>{`
  `}<span className="text-[var(--term-dim)]">{c.ledgerLabel.padEnd(10)}</span>{` `}<span className="text-[var(--brand-ring)] underline decoration-dotted underline-offset-2">{c.ledgerValue}</span>{`

  `}<span className="text-[var(--term-dim)]">{c.hint}</span>{`

> `}<span className="text-[var(--panel-bright)]">{c.userReply}</span>{`

`}<span className="text-[var(--term-prompt)]">●</span>{` `}<span className="text-[var(--panel-bright)]">{c.hitlLabel}</span>{`
  `}<span className="text-[var(--term-dim)]">{c.reviewerLabel.padEnd(10)}</span>{` `}<span className="text-[var(--term-out)]">{c.reviewerValue}</span>{`
  `}<span className="text-[var(--term-dim)]">{c.linkLabel.padEnd(10)}</span>{` `}<span className="text-[var(--brand-ring)] underline decoration-dotted underline-offset-2">{c.linkValue}</span>{`
  `}<span className="text-[var(--term-dim)]">{c.statusLabel.padEnd(10)}</span>{` `}<span className="text-[var(--term-prompt)]">{c.statusValue}</span>
        </pre>
        {/* Status bar */}
        <div className="flex items-center gap-2 px-4 py-2 border-t border-[var(--panel-border)]/80 text-[10.5px] font-mono text-[var(--term-dim)]">
          <span className="text-[var(--term-err)]">▶▶</span>
          <span>{c.statusBar}</span>
        </div>
      </div>
      <figcaption className="mt-3 text-center text-[11px] text-[var(--subtle)] tracking-wide">
        {c.caption}
      </figcaption>
    </figure>
  )
}

// ── capabilities ───────────────────────────────────────────────────
function Capabilities({ c }: { c: CapabilitiesCopy }) {
  return (
    <>
      <SectionHead eyebrow={c.eyebrow} heading={c.heading} sub={c.sub} />
      <div className="mt-12 grid gap-5 md:grid-cols-3">
        {c.groups.map((g) => (
          <div
            key={g.label}
            className="relative rounded-2xl border border-[var(--color-border-subtle)] bg-white overflow-hidden hover:border-[var(--brand)]/40 hover:shadow-[0_8px_24px_-12px_rgba(15,23,42,0.18)] transition-all duration-200"
          >
            <span aria-hidden="true" className="absolute inset-y-0 left-0 w-[3px] bg-[var(--brand)]" />
            <div className="p-6 pl-7">
              <div className="flex items-center gap-2.5">
                <span className="inline-flex w-9 h-9 items-center justify-center rounded-lg bg-[var(--brand-tint)] border border-[var(--brand)]/20">
                  <CapabilityIcon kind={g.icon} />
                </span>
                <p translate="no" className="text-[11px] uppercase tracking-[0.16em] text-[var(--brand)] font-bold">
                  {g.label}
                </p>
              </div>
              <h3 className="mt-4 text-lg md:text-xl font-semibold text-[var(--ink)] m-0 leading-snug">
                {g.tagline}
              </h3>
              <ul className="mt-4 space-y-2">
                {g.items.map((it) => (
                  <li key={it} className="flex items-start gap-2 text-sm text-[var(--body)] leading-6">
                    <CheckGreen className="mt-1 w-3.5 h-3.5 shrink-0" />
                    <span>{it}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ))}
      </div>
    </>
  )
}

// ── why ────────────────────────────────────────────────────────────
function Why({ c }: { c: WhyCopy }) {
  return (
    <>
      <SectionHead eyebrow={c.eyebrow} heading={c.heading} />
      <div className="mt-12 grid gap-5 md:grid-cols-3">
        {c.items.map((it, i) => (
          <div
            key={it.q}
            className="rounded-2xl border border-[var(--color-border-subtle)] bg-white p-6 md:p-7"
          >
            <div className="flex items-baseline gap-3">
              <span className="text-[28px] md:text-[32px] font-bold text-[var(--brand)] leading-none tabular-nums">
                0{i + 1}
              </span>
              <span aria-hidden="true" className="h-px flex-1 bg-[var(--brand)]/30" />
            </div>
            <h3 className="mt-5 text-base md:text-lg font-semibold text-[var(--ink)] m-0 text-pretty leading-snug">
              {it.q}
            </h3>
            <p className="mt-3 text-sm text-[var(--body)] leading-7 text-pretty">{it.a}</p>
          </div>
        ))}
      </div>
    </>
  )
}

// ── how it works ───────────────────────────────────────────────────
function How({ c }: { c: HowCopy }) {
  return (
    <div id="how">
      <SectionHead eyebrow={c.eyebrow} heading={c.heading} sub={c.sub} />
      <ol className="mt-12 mx-auto max-w-3xl">
        {c.steps.map((s, i) => (
          <li key={s.n} className="relative pl-14 pb-8 last:pb-0">
            {/* connector */}
            {i < c.steps.length - 1 && (
              <span
                aria-hidden="true"
                className="absolute left-[18px] top-10 bottom-0 w-px bg-[var(--brand)]/25"
              />
            )}
            <div className="absolute left-0 top-0 w-[38px] h-[38px] rounded-full bg-white border-2 border-[var(--brand)]/40 text-[var(--brand-strong)] flex items-center justify-center text-sm font-bold tabular-nums shadow-sm">
              {s.n}
            </div>
            <h3 className="text-base md:text-lg font-semibold text-[var(--ink)] m-0 leading-snug">{s.title}</h3>
            <p className="mt-2 text-sm md:text-[15px] text-[var(--body)] leading-7 text-pretty">{s.body}</p>
            {s.cmd && (
              <div className="mt-3 rounded-xl border border-[var(--panel-border)] bg-[var(--term-bg)] overflow-hidden">
                {s.cmdLabel && (
                  <div className="px-4 py-2 border-b border-[var(--panel-border)]/70 text-[10.5px] font-mono uppercase tracking-[0.14em] text-[var(--term-dim)]">
                    {s.cmdLabel}
                  </div>
                )}
                <pre
                  translate="no"
                  className="m-0 px-4 py-3 text-[12.5px] leading-6 font-mono text-[var(--term-out)] whitespace-pre overflow-x-auto"
                >
                  {s.cmd}
                </pre>
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}

// ── faq ────────────────────────────────────────────────────────────
function FAQ({ c }: { c: FAQCopy }) {
  return (
    <>
      <SectionHead eyebrow={c.eyebrow} heading={c.heading} />
      <div className="mt-10 mx-auto max-w-3xl space-y-2">
        {c.items.map((it) => (
          <details
            key={it.q}
            className="group rounded-2xl border border-[var(--color-border-subtle)] bg-white open:bg-[var(--mist)] transition-colors duration-200"
          >
            <summary className="flex items-center justify-between gap-3 cursor-pointer list-none px-5 py-4 select-none rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--cta)]/40">
              <span className="text-sm md:text-[15px] font-semibold text-[var(--ink)] text-pretty">{it.q}</span>
              <svg aria-hidden="true" className="w-4 h-4 text-[var(--subtle)] transition-transform duration-200 group-open:rotate-180 shrink-0" viewBox="0 0 20 20" fill="currentColor">
                <path d="M10 12.5l-4.7-4.7a1 1 0 011.4-1.4L10 9.7l3.3-3.3a1 1 0 011.4 1.4L10 12.5z" />
              </svg>
            </summary>
            <div className="px-5 pb-5 -mt-1 text-sm text-[var(--body)] leading-7 text-pretty">
              {it.a}
            </div>
          </details>
        ))}
      </div>
    </>
  )
}

// ── final CTA ──────────────────────────────────────────────────────
function CTA({ c }: { c: CTACopy }) {
  return (
    <div className="text-center text-white">
      <h2 className="text-3xl md:text-4xl font-bold m-0 tracking-tight text-balance">
        {c.heading}
      </h2>
      <p className="mt-4 mx-auto max-w-xl text-sm md:text-base text-[var(--panel-bright)]/80 leading-7 text-pretty">
        {c.body}
      </p>
      <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
        <PrimaryCTA size="lg" href="/install">{c.cta}</PrimaryCTA>
        <a
          href="https://github.com/openmagi"
          target="_blank" rel="noopener noreferrer"
          className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-white/20 bg-white/5 px-6 py-3.5 text-base font-semibold text-white hover:bg-white/10 transition-colors duration-200 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
        >
          {c.ctaSecondary}<Arrow />
        </a>
      </div>
    </div>
  )
}

// ── copy ───────────────────────────────────────────────────────────
const KO = {
  hero: {
    chips: ["당신 룰대로", "런타임 검증", "오픈소스"],
    title: "Claude Code에 가드레일을",
    subtitle:
      "에이전트의 도구 호출과 프롬프트 전송, 세션 경계까지 매 시점 본인 룰로 검사합니다. 위험한 호출은 차단, 사람 승인이 필요한 건 리뷰 큐로, 나머지는 위·변조 불가능한 원장에 봉인.",
    cta: "30초 안에 시작",
    bullets: ["에이전트 코드 변경 0", "한 줄 설치", "감사 원장 내장"],
    toolsLabel: "동작 대상",
    tools: ["Claude Code", "PreToolUse hook", "Bash · Edit · Write"],
    tui: {
      windowTitle: "claude-code · ~/projects/payments",
      userPrompt: "방금 만든 AWS 키를 깃에 푸시해줘",
      toolCall: "Bash(git push origin main)",
      toolEcho: "$ git push origin main",
      verdictLabel: "Magi gate · BLOCKED",
      ruleLabel: "rule",
      ruleValue: "external-sends-require-signoff",
      evidenceLabel: "evidence",
      evidenceValue: "regex AKIA[A-Z0-9]+ matched in staged diff",
      ledgerLabel: "ledger",
      ledgerValue: "omg://2026-06-22T18:01Z/3a9c…",
      hint: "↳ \"approve\" 입력 시 HITL 큐로, 또는 diff 수정",
      userReply: "approve",
      hitlLabel: "Magi gate · ROUTED TO HITL",
      reviewerLabel: "reviewer",
      reviewerValue: "you (kevin@openmagi.ai)",
      linkLabel: "link",
      linkValue: "cloud.openmagi.ai/hitl/3a9c",
      statusLabel: "status",
      statusValue: "awaiting approval",
      statusBar: "bypass permissions on · 1 shell · esc to interrupt",
      caption: "차단 → 사람 승인 라우팅까지 한 컷. 모든 단계가 원장에 봉인됩니다.",
    },
  },
  capabilities: {
    eyebrow: "POLICY MODEL",
    heading: "한 모델로 다 표현됩니다",
    sub: "8 hook events × 3 archetypes × 4 condition kinds. 대시보드 위저드에서 코드 한 줄 없이 작성합니다.",
    groups: [
      {
        icon: "clock" as const,
        label: "When · 8 hook events",
        tagline: "라이프사이클 어디서든",
        items: [
          "Pre / PostToolUse · 도구 실행 전·후",
          "UserPromptSubmit · 프롬프트가 LLM으로 가기 직전",
          "PreCompact · 컨텍스트 압축 직전",
          "Stop / SubagentStop · 에이전트, 서브에이전트 종료",
          "SessionStart / SessionEnd · 세션 경계",
        ],
      },
      {
        icon: "shield" as const,
        label: "What · 3 archetypes",
        tagline: "정책 의도를 분명하게",
        items: [
          "Block · 조건 fail 시 호출 자체를 차단",
          "Ask · 리뷰 큐로 보내고 사람이 승인",
          "Audit · ledger에 기록만, 차단은 안 함",
          "Emit signal · 조건 없이 무조건 ledger 기록",
        ],
      },
      {
        icon: "code" as const,
        label: "Condition · 4 kinds",
        tagline: "규칙 표현 방식 자유",
        items: [
          "Wired verifier · 빌트인 검증자",
          "Inline regex · Python re 패턴",
          "LLM critic · 자연어 기준 (preview)",
          "SHACL shape · Turtle 시맨틱 (preview)",
        ],
      },
    ],
  },
  why: {
    eyebrow: "SOUND FAMILIAR?",
    heading: "에이전트는 빠르고, 잘못은 비쌉니다",
    items: [
      { q: "에이전트는 사람보다 빠릅니다", a: "도구 호출 결정은 사람보다 빠르게 흘러갑니다. 한 번의 잘못된 호출이 곧장 인프라와 데이터, 평판에 반영됩니다. PreToolUse hook 단에서 막아야 사후 복구 비용이 들지 않습니다." },
      { q: "감사 체인은 사후에 못 만듭니다", a: "어떤 호출이 통과했고 무엇이 차단됐는지 사고 후 로그를 모아 재구성하는 건 늦습니다. magi는 매 결정을 Ed25519 서명과 SHA-256 해시 체인으로 그 자리에서 봉인합니다." },
      { q: "에이전트는 그대로 두세요", a: "Claude Code의 managed-settings.json과 한 줄짜리 bash shim만 있으면 됩니다. 에이전트 워크플로 변경 0, SDK 의존 0." },
    ],
  },
  how: {
    eyebrow: "HOW IT WORKS",
    heading: "한 줄 명령, 그게 다입니다",
    sub: "Docker만 있으면 됩니다. 인스톨러가 OSS 저장소를 clone, .env 자동 생성, docker compose up, Claude Code 배선까지 한 번에 처리합니다.",
    steps: [
      {
        n: "1",
        title: "한 줄 명령 실행",
        body: "터미널에 붙여 넣으면 끝. 인스톨러가 공식 docker-compose.yml을 다운받고, 랜덤 키 4개를 .env에 생성한 뒤 docker compose로 컨트롤 플레인 + 대시보드를 localhost에 띄워줍니다.",
        cmdLabel: "Terminal · 한 번이면 됩니다",
        cmd: "curl -fsSL https://cp.openmagi.ai/install.sh | bash",
      },
      {
        n: "2",
        title: "정책 작성",
        body: "대시보드의 Guided 위저드 6단계로 클릭하거나, 자연어로 \"AWS 키를 외부로 보내지 마\" 같이 쓰면 LLM 컴파일러가 IR로 변환합니다.",
      },
      {
        n: "3",
        title: "Claude Code 평소처럼 쓰기",
        body: "이후로는 평소대로. 매 도구 호출마다 게이트가 verdict을 내립니다. pass는 실행, block은 거부, ask는 HITL 큐로. 모든 결정은 /ledger에 봉인됩니다.",
      },
    ],
  },
  faq: {
    eyebrow: "FAQ",
    heading: "자주 묻는 질문",
    items: [
      { q: "도구 호출 페이로드가 OpenMagi 서버로 전송되나요?", a: "검증 시점에 payload는 클라우드에 도달하지만 본문은 저장하지 않습니다. 저장되는 건 verdict, reasons, 정책 id뿐입니다. LLM critic kind를 쓰면 LLM 공급자로 가는데, 이건 정책마다 선택할 수 있습니다." },
      { q: "Claude Code 외 다른 에이전트도 지원하나요?", a: "현재는 Claude Code의 hooks 메커니즘을 통해 통합합니다. 같은 패턴(PreToolUse hook + JSON 응답)이면 작은 어댑터로 호환됩니다. Cursor, Continue 등은 로드맵에 있습니다." },
      { q: "온프레미스 · Air-gapped 배포는?", a: "OSS이므로 가능합니다. 단 LLM critic과 SHACL은 외부 의존이 필요해서 air-gapped 환경에선 regex, wired verifier kind만 사용 가능합니다." },
      { q: "원장이 정말 위·변조 불가능한가요?", a: "각 항목은 SHA-256으로 이전 항목과 체인 연결되고 Ed25519로 서명됩니다. 단일 행만 수정해도 모든 후속 해시가 깨져서 무결성 검증에서 즉시 잡힙니다." },
      { q: "HITL 큐는 어떻게 동작하나요?", a: "Ask archetype 정책은 조건 fail 시 리뷰 큐로 보냅니다. /hitl 대시보드에서 검토자가 승인하면 서명 토큰이 발급되어 호출이 재개됩니다." },
    ],
  },
  cta: {
    heading: "지금 한 줄로 시작하세요",
    body: "Docker만 있으면 됩니다. 인스톨러가 OSS 저장소 clone부터 docker compose up, Claude Code 배선까지 한 번에 처리합니다.",
    cta: "설치 가이드 열기",
    ctaSecondary: "openmagi GitHub org",
  },
} satisfies {
  hero: HeroCopy
  capabilities: CapabilitiesCopy; why: WhyCopy; how: HowCopy
  faq: FAQCopy; cta: CTACopy
}

const EN = {
  hero: {
    chips: ["YOUR RULES", "RUNTIME CHECKED", "OPEN SOURCE"],
    title: "Guardrails for Claude Code",
    subtitle:
      "Every tool call, prompt, and session boundary is checked against your rules at runtime. Block what is risky, queue what needs a human, and seal the rest into a tamper-evident ledger.",
    cta: "Get started in 30 seconds",
    bullets: ["No agent code change", "One-line install", "Audit ledger built-in"],
    toolsLabel: "Works with",
    tools: ["Claude Code", "PreToolUse hook", "Bash · Edit · Write"],
    tui: {
      windowTitle: "claude-code · ~/projects/payments",
      userPrompt: "push the new AWS key to github",
      toolCall: "Bash(git push origin main)",
      toolEcho: "$ git push origin main",
      verdictLabel: "Magi gate · BLOCKED",
      ruleLabel: "rule",
      ruleValue: "external-sends-require-signoff",
      evidenceLabel: "evidence",
      evidenceValue: "regex AKIA[A-Z0-9]+ matched in staged diff",
      ledgerLabel: "ledger",
      ledgerValue: "omg://2026-06-22T18:01Z/3a9c…",
      hint: "↳ reply \"approve\" to route to HITL, or revise the diff",
      userReply: "approve",
      hitlLabel: "Magi gate · ROUTED TO HITL",
      reviewerLabel: "reviewer",
      reviewerValue: "you (kevin@openmagi.ai)",
      linkLabel: "link",
      linkValue: "cloud.openmagi.ai/hitl/3a9c",
      statusLabel: "status",
      statusValue: "awaiting approval",
      statusBar: "bypass permissions on · 1 shell · esc to interrupt",
      caption: "Block to human-approval in one shot. Every step sealed in the ledger.",
    },
  },
  capabilities: {
    eyebrow: "POLICY MODEL",
    heading: "One model. Everything it expresses.",
    sub: "8 hook events × 3 archetypes × 4 condition kinds. Author it in the dashboard wizard, with no code change required.",
    groups: [
      {
        icon: "clock" as const,
        label: "When · 8 hook events",
        tagline: "Anywhere in the lifecycle",
        items: [
          "Pre / PostToolUse · before & after a tool runs",
          "UserPromptSubmit · before the prompt reaches the LLM",
          "PreCompact · before context compaction",
          "Stop / SubagentStop · main and sub agent stops",
          "SessionStart / SessionEnd · session boundaries",
        ],
      },
      {
        icon: "shield" as const,
        label: "What · 3 archetypes",
        tagline: "Name what the policy is for",
        items: [
          "Block · refuse the call when the condition fails",
          "Ask · send to the review queue, a human approves",
          "Audit · record to the ledger, never blocks",
          "Emit signal · unconditional ledger entry",
        ],
      },
      {
        icon: "code" as const,
        label: "Condition · 4 kinds",
        tagline: "Express the rule the way it fits",
        items: [
          "Wired verifier · built-in checks",
          "Inline regex · Python re pattern",
          "LLM critic · natural-language rule (preview)",
          "SHACL shape · semantic Turtle (preview)",
        ],
      },
    ],
  },
  why: {
    eyebrow: "SOUND FAMILIAR?",
    heading: "Agents are fast. Mistakes are expensive.",
    items: [
      { q: "Agents fire faster than humans review", a: "Tool-call decisions race past the operator. A single wrong call hits infra, data, and reputation in real time. Catching it at the PreToolUse hook keeps recovery cost out of the picture." },
      { q: "You can't reconstruct an audit chain after the fact", a: "Which calls passed, which were blocked, who approved what: collecting it from logs after the incident is too late. Magi seals every decision with an Ed25519 signature and SHA-256 hash chain in the moment." },
      { q: "Leave the agent untouched", a: "One managed-settings.json and one bash shim. Zero agent workflow change, zero SDK dependency. Compatible agents are a small adapter away." },
    ],
  },
  how: {
    eyebrow: "HOW IT WORKS",
    heading: "One command. That is it.",
    sub: "Docker is the only prereq. The installer clones the OSS repo, generates a .env, brings up docker compose, and wires Claude Code in one flow.",
    steps: [
      {
        n: "1",
        title: "Run the one-liner",
        body: "Paste this in your terminal. The installer downloads the official docker-compose.yml, generates four random keys into .env, and runs docker compose to bring the control plane + dashboard up on localhost.",
        cmdLabel: "Terminal · one shot",
        cmd: "curl -fsSL https://cp.openmagi.ai/install.sh | bash",
      },
      {
        n: "2",
        title: "Write a policy",
        body: "Click through the 6-step Guided wizard, or just type natural language like \"do not send AWS keys to external hosts\". The LLM compiler emits the IR for you.",
      },
      {
        n: "3",
        title: "Use Claude Code as before",
        body: "From now on, just run the agent. Every tool call hits the gate first: pass executes, block refuses, ask routes to the HITL queue. Every decision is sealed in /ledger.",
      },
    ],
  },
  faq: {
    eyebrow: "FAQ",
    heading: "Frequently asked",
    items: [
      { q: "Does the tool-call payload reach your servers?", a: "Payload text reaches the cloud at verify time but is NOT persisted: only the verdict, reasons, and policy id are sealed. LLM-critic conditions send the criterion + payload to the configured LLM provider; that is per-policy and opt-in." },
      { q: "Other agents besides Claude Code?", a: "Today we integrate via Claude Code's hooks mechanism. Any agent that emits PreToolUse-style hooks with JSON responses can adapt with a small shim. Cursor and Continue are on the roadmap." },
      { q: "On-prem or air-gapped?", a: "Yes. The whole project is OSS. LLM-critic and SHACL kinds depend on external libs, so air-gapped installs are restricted to regex + wired-verifier conditions." },
      { q: "Is the ledger really tamper-evident?", a: "Each entry is SHA-256-chained to its predecessor and Ed25519-signed. Any single-row mutation breaks every subsequent hash; the chain-integrity endpoint catches it instantly." },
      { q: "How does HITL work?", a: "Policies with archetype = ask send a review-queue entry on condition fail. A reviewer approves or rejects from /hitl. Approval issues a signed token that resumes the call." },
    ],
  },
  cta: {
    heading: "Get started in one line",
    body: "Docker is the only prerequisite. The installer handles git clone, docker compose up, and Claude Code wiring in one flow.",
    cta: "Open install guide",
    ctaSecondary: "openmagi GitHub org",
  },
} satisfies {
  hero: HeroCopy
  capabilities: CapabilitiesCopy; why: WhyCopy; how: HowCopy
  faq: FAQCopy; cta: CTACopy
}
