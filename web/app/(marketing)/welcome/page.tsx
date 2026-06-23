import type { Metadata } from "next"
import Link from "next/link"
import { getLocale } from "@/lib/i18n/server"
import { Badge, Button, Card, CardHeader } from "@/components/ui"

export const dynamic = "force-dynamic"

export const metadata: Metadata = {
  title: "magi-control-plane — Guardrails for Claude Code",
  description:
    "Every action your agent takes — tool calls, prompts, session boundaries — checked against your rules at runtime. Block, ask a human, or audit. All sealed in a tamper-evident ledger.",
  openGraph: {
    title: "magi-control-plane — Guardrails for Claude Code",
    description:
      "Run Claude Code on systems that matter. Magi catches every agent action before it ships — author rules in the dashboard, no agent changes.",
    type: "website",
    locale: "ko_KR",
    alternateLocale: "en_US",
  },
  twitter: { card: "summary_large_image" },
  alternates: { canonical: "/welcome" },
  robots: { index: true, follow: true },
}

/** D36: marketing landing — repositioned from "for lawyers" to a
 * broader developer / platform-team narrative. The agent + governance
 * story works for any team running Claude Code (or similar tool-use
 * agents) where blast radius matters. */
export default async function WelcomePage() {
  const locale = await getLocale()
  const isKo = locale === "ko"
  const C = isKo ? KO : EN
  return (
    <div className="pb-24">
      <Hero c={C.hero} />
      <Capabilities c={C.capabilities} />
      <Why c={C.why} />
      <How c={C.how} />
      <Pricing c={C.pricing} />
      <FAQ c={C.faq} />
      <CTA c={C.cta} />
    </div>
  )
}

// ── types ──────────────────────────────────────────────────────────
type HeroCopy = {
  eyebrow: string; title: string; subtitle: string
  cta: string; ctaSecondary: string; alpha: string
  terminalIntro: string
  terminalUser: string
  terminalDeny: string
  terminalLedger: string
}
type CapabilitiesCopy = {
  heading: string; sub: string
  groups: Array<{ label: string; tagline: string; items: string[] }>
}
type WhyCopy = { heading: string; items: Array<{ q: string; a: string }> }
type HowCopy = { heading: string; steps: Array<{ n: string; title: string; body: string }> }
type PricingCopy = {
  heading: string; sub: string
  plans: Array<{ name: string; price: string; cap: string; features: string[]; cta: string; primary: boolean }>
}
type FAQCopy = { heading: string; items: Array<{ q: string; a: string }> }
type CTACopy = { heading: string; body: string; cta: string }

// ── hero ───────────────────────────────────────────────────────────
function Hero({ c }: { c: HeroCopy }) {
  return (
    <section className="pt-12 md:pt-20 pb-12">
      <div className="text-center">
        <Badge variant="info">{c.eyebrow}</Badge>
        <h1 className="mt-5 text-4xl md:text-6xl font-semibold tracking-tight text-balance text-[var(--color-text-primary)] leading-[1.05]">
          {c.title}
        </h1>
        <p className="mt-6 mx-auto max-w-2xl text-base md:text-lg text-pretty text-[var(--color-text-secondary)] leading-7">
          {c.subtitle}
        </p>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <a href="https://clawy.pro/pricing" target="_blank" rel="noopener noreferrer">
            <Button variant="primary" size="lg">{c.cta}</Button>
          </a>
          <Link href="/install" prefetch={false}>
            <Button variant="ghost" size="lg">{c.ctaSecondary}</Button>
          </Link>
        </div>
        <p className="mt-4 text-xs text-[var(--color-text-tertiary)]">{c.alpha}</p>
      </div>

      {/* Hero terminal mock — shows a real "deny" sequence so the
          reader sees the product in motion without scrolling. */}
      <div className="mt-12 mx-auto max-w-3xl">
        <div className="rounded-2xl border border-[var(--color-border-subtle)] bg-[#0F1117] shadow-2xl shadow-[var(--color-accent)]/10 overflow-hidden">
          <div className="flex items-center gap-1.5 px-4 py-2.5 border-b border-white/[0.06] bg-white/[0.02]">
            <span className="w-2.5 h-2.5 rounded-full bg-rose-400/80" />
            <span className="w-2.5 h-2.5 rounded-full bg-amber-300/80" />
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-400/80" />
            <span className="ml-3 text-[11px] font-mono text-white/40">
              claude-code · main agent · PreToolUse
            </span>
          </div>
          <pre className="px-5 py-4 text-[12.5px] leading-6 font-mono text-white/85 whitespace-pre-wrap">
            <span className="text-white/45">{c.terminalIntro}</span>{"\n"}
            <span className="text-emerald-300">$</span>{" "}<span className="text-white">{c.terminalUser}</span>{"\n"}
            <span className="text-rose-400">{c.terminalDeny}</span>{"\n"}
            <span className="text-white/45">{c.terminalLedger}</span>
          </pre>
        </div>
      </div>
    </section>
  )
}

// ── capabilities grid ──────────────────────────────────────────────
function Capabilities({ c }: { c: CapabilitiesCopy }) {
  return (
    <section className="mt-8">
      <h2 className="text-2xl md:text-3xl font-semibold text-[var(--color-text-primary)] text-center">
        {c.heading}
      </h2>
      <p className="mt-3 mx-auto max-w-2xl text-sm text-[var(--color-text-tertiary)] text-center text-pretty">
        {c.sub}
      </p>
      <div className="mt-8 grid gap-4 md:grid-cols-3">
        {c.groups.map((g, i) => (
          <div
            key={i}
            className="relative rounded-2xl border border-[var(--color-border-subtle)] bg-white p-6"
          >
            <p className="text-[11px] uppercase tracking-[0.14em] text-[var(--color-text-tertiary)] font-semibold">
              {g.label}
            </p>
            <h3 className="mt-1.5 text-lg font-semibold text-[var(--color-text-primary)] m-0">
              {g.tagline}
            </h3>
            <ul className="mt-4 space-y-1.5">
              {g.items.map((it) => (
                <li
                  key={it}
                  className="flex items-start gap-2 text-sm text-[var(--color-text-secondary)] leading-6"
                >
                  <span
                    aria-hidden="true"
                    className="mt-2 w-1 h-1 rounded-full bg-[var(--color-accent)] shrink-0"
                  />
                  <span>{it}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  )
}

// ── why ────────────────────────────────────────────────────────────
function Why({ c }: { c: WhyCopy }) {
  return (
    <section className="mt-20">
      <h2 className="text-2xl md:text-3xl font-semibold text-[var(--color-text-primary)] text-center">
        {c.heading}
      </h2>
      <div className="mt-8 grid gap-4 md:grid-cols-3">
        {c.items.map((it, i) => (
          <Card key={i} className="!p-6">
            <h3 className="text-base font-semibold text-[var(--color-text-primary)] m-0">
              {it.q}
            </h3>
            <p className="mt-3 text-sm text-[var(--color-text-secondary)] leading-6 text-pretty">
              {it.a}
            </p>
          </Card>
        ))}
      </div>
    </section>
  )
}

// ── how it works ───────────────────────────────────────────────────
function How({ c }: { c: HowCopy }) {
  return (
    <section id="how" className="mt-20">
      <h2 className="text-2xl md:text-3xl font-semibold text-[var(--color-text-primary)] text-center">
        {c.heading}
      </h2>
      <ol className="mt-8 mx-auto max-w-3xl space-y-3">
        {c.steps.map((s) => (
          <li
            key={s.n}
            className="flex gap-4 rounded-2xl border border-[var(--color-border-subtle)] bg-white p-5"
          >
            <div className="shrink-0 w-9 h-9 rounded-full bg-[var(--color-accent)]/10 text-[var(--color-accent-light)] flex items-center justify-center text-sm font-semibold">
              {s.n}
            </div>
            <div className="min-w-0">
              <h3 className="text-base font-semibold text-[var(--color-text-primary)] m-0">{s.title}</h3>
              <p className="mt-1.5 text-sm text-[var(--color-text-secondary)] leading-6 text-pretty">{s.body}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}

// ── pricing ────────────────────────────────────────────────────────
function Pricing({ c }: { c: PricingCopy }) {
  return (
    <section className="mt-20">
      <h2 className="text-2xl md:text-3xl font-semibold text-[var(--color-text-primary)] text-center">
        {c.heading}
      </h2>
      <p className="mt-3 mx-auto max-w-2xl text-sm text-[var(--color-text-tertiary)] text-center">
        {c.sub}
      </p>
      <div className="mt-8 grid gap-4 md:grid-cols-2 max-w-4xl mx-auto">
        {c.plans.map((p, i) => (
          <div
            key={i}
            className={`relative rounded-2xl border p-6 ${
              p.primary
                ? "border-[var(--color-accent)]/30 bg-[var(--color-accent)]/[0.04] shadow-lg shadow-[var(--color-accent)]/10"
                : "border-[var(--color-border-subtle)] bg-white"
            }`}
          >
            {p.primary && (
              <span className="absolute -top-3 left-6 px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider bg-[var(--color-accent)] text-white">
                recommended
              </span>
            )}
            <div className="flex items-baseline justify-between gap-3">
              <h3 className="text-lg font-semibold text-[var(--color-text-primary)] m-0">{p.name}</h3>
              <span className="text-2xl font-semibold text-[var(--color-text-primary)]">{p.price}</span>
            </div>
            <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">{p.cap}</p>
            <ul className="mt-5 space-y-2">
              {p.features.map((f) => (
                <li key={f} className="flex items-start gap-2 text-sm text-[var(--color-text-secondary)] leading-6">
                  <svg aria-hidden="true" className="mt-0.5 w-4 h-4 shrink-0 text-[var(--color-accent)]" viewBox="0 0 20 20" fill="currentColor">
                    <path d="M16.7 5.3a1 1 0 010 1.4l-7 7a1 1 0 01-1.4 0l-3.5-3.5a1 1 0 011.4-1.4l2.8 2.8 6.3-6.3a1 1 0 011.4 0z" />
                  </svg>
                  <span>{f}</span>
                </li>
              ))}
            </ul>
            <a
              href={p.primary ? "https://clawy.pro/pricing" : "https://github.com/openmagi/magi-control-plane"}
              target="_blank"
              rel="noopener noreferrer"
              className="block mt-6"
            >
              <Button variant={p.primary ? "primary" : "ghost"} className="w-full">
                {p.cta}
              </Button>
            </a>
          </div>
        ))}
      </div>
    </section>
  )
}

// ── faq ────────────────────────────────────────────────────────────
function FAQ({ c }: { c: FAQCopy }) {
  return (
    <section className="mt-20">
      <h2 className="text-2xl md:text-3xl font-semibold text-[var(--color-text-primary)] text-center">
        {c.heading}
      </h2>
      <div className="mt-8 mx-auto max-w-3xl space-y-2">
        {c.items.map((it, i) => (
          <details
            key={i}
            className="group rounded-2xl border border-[var(--color-border-subtle)] bg-white open:bg-gray-50/60"
          >
            <summary className="flex items-center justify-between gap-3 cursor-pointer list-none px-5 py-4 select-none">
              <span className="text-sm font-semibold text-[var(--color-text-primary)]">{it.q}</span>
              <svg
                aria-hidden="true"
                className="w-4 h-4 text-[var(--color-text-tertiary)] transition-transform group-open:rotate-180 shrink-0"
                viewBox="0 0 20 20" fill="currentColor"
              >
                <path d="M10 12.5l-4.7-4.7a1 1 0 011.4-1.4L10 9.7l3.3-3.3a1 1 0 011.4 1.4L10 12.5z" />
              </svg>
            </summary>
            <div className="px-5 pb-5 -mt-1 text-sm text-[var(--color-text-secondary)] leading-6">
              {it.a}
            </div>
          </details>
        ))}
      </div>
    </section>
  )
}

// ── final CTA ──────────────────────────────────────────────────────
function CTA({ c }: { c: CTACopy }) {
  return (
    <section className="mt-20">
      <div className="mx-auto max-w-3xl rounded-3xl border border-[var(--color-accent)]/25 bg-gradient-to-br from-[var(--color-accent)]/[0.06] via-white to-white p-10 text-center">
        <h2 className="text-2xl md:text-3xl font-semibold text-[var(--color-text-primary)] m-0">
          {c.heading}
        </h2>
        <p className="mt-3 mx-auto max-w-xl text-sm text-[var(--color-text-secondary)] leading-6">
          {c.body}
        </p>
        <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
          <a href="https://clawy.pro/pricing" target="_blank" rel="noopener noreferrer">
            <Button variant="primary" size="lg">{c.cta}</Button>
          </a>
          <Link href="/install" prefetch={false}>
            <Button variant="ghost" size="lg">Self-host</Button>
          </Link>
        </div>
      </div>
    </section>
  )
}

// ── copy ───────────────────────────────────────────────────────────
const KO = {
  hero: {
    eyebrow: "Open Source · Apache 2.0",
    title: "Claude Code 의 모든 도구 호출을 정책으로 게이트.",
    subtitle:
      "PreToolUse hook 에 드롭인. 매 호출마다 block / ask / audit 중 하나로 판정하고, 모든 결정을 Ed25519 서명 + 해시 체인 원장에 봉인합니다. 자체 호스팅 무료, 호스티드는 Clawy Pro+ 에 포함.",
    cta: "Pro+ 로 시작",
    ctaSecondary: "또는 자체 호스팅",
    alpha: "Apache 2.0 · GitHub 전체 공개 · 운영 부담 없으려면 Clawy Pro+",
    terminalIntro: "# Claude Code → magi-gate (PreToolUse)",
    terminalUser: "Bash: aws s3 rm s3://prod-backups --recursive",
    terminalDeny: "✗ MAGI: aws_key_detected — 'AKIA…' regex matched payload (policy: block-prod-rm)",
    terminalLedger: "# verdict deny · ledger entry h=8a6f… (signed, chained)",
  },
  capabilities: {
    heading: "한 정책 모델로 표현 가능한 것",
    sub: "8 hook events × 3 action archetypes × 4 condition kinds. 정책을 코드 변경 없이 위저드로 작성.",
    groups: [
      {
        label: "WHEN — 8 hook events",
        tagline: "라이프사이클 어느 시점에든",
        items: [
          "PreToolUse / PostToolUse — 도구 실행 전·후",
          "UserPromptSubmit — 프롬프트가 LLM 으로 가기 직전",
          "PreCompact — 컨텍스트 압축 직전 (증거 체인 보존)",
          "Stop / SubagentStop — 에이전트 / 서브에이전트 종료",
          "SessionStart / SessionEnd — 세션 경계",
        ],
      },
      {
        label: "WHAT — 3 archetypes",
        tagline: "정책의 의도를 명확히",
        items: [
          "Block — 조건 fail 시 호출 자체를 차단",
          "Ask — review queue 로 보내고 사람 승인 받음",
          "Audit — ledger 에 기록만, 차단은 안 함",
          "(condition 없는 emit-signal 도 archetype 으로 표현)",
        ],
      },
      {
        label: "CONDITION — 4 kinds",
        tagline: "조건 표현 방식 다양",
        items: [
          "Wired verifier — 빌트인 검증자 (citation, privilege, …)",
          "Inline regex — Python re 패턴 매칭",
          "LLM critic — 자연어 기준 (LLM 호출, preview)",
          "SHACL shape — Turtle 시맨틱 검증 (preview)",
        ],
      },
    ],
  },
  why: {
    heading: "왜 magi-control-plane 인가",
    items: [
      {
        q: "에이전트는 빠르게 발화합니다",
        a: "도구 호출 결정이 사람보다 빠르게 흐르기 때문에, 한 번의 잘못된 호출이 바로 인프라/데이터/평판에 반영됩니다. PreToolUse hook 단에서 막아야 사후 복구 비용이 들지 않습니다.",
      },
      {
        q: "감사 체인은 재구성하지 못합니다",
        a: "어떤 호출이 통과했고, 무엇이 차단됐고, 누가 승인했는지 — 사고 발생 후 로그를 모아 재구성하는 건 늦습니다. magi 는 매 결정을 Ed25519 서명 + SHA-256 해시 체인으로 그 자리에서 봉인합니다.",
      },
      {
        q: "에이전트 자체는 그대로 둡니다",
        a: "Claude Code 의 managed-settings.json + 한 줄짜리 bash shim 만 있으면 됩니다. 에이전트 워크플로 변경 0, SDK 의존 0. PreToolUse hook 표준만 따라가면 다른 에이전트 호환도 빠릅니다.",
      },
    ],
  },
  how: {
    heading: "어떻게 동작하나",
    steps: [
      { n: "1", title: "키 발급", body: "Clawy Pro+ 결제 = 테넌트 + API 키 자동 발급, 이메일 전달. 자체 호스팅이면 본인 인스턴스에서 발급." },
      { n: "2", title: "한 줄 설치", body: "curl -fsSL <인스턴스>/install.sh | bash -s -- mcp_… 한 줄. managed-settings.json + bash 게이트 자동 배치." },
      { n: "3", title: "정책 작성", body: "Guided 위저드 6단계 (When → What → Condition → Specifics → Name → Review). 자연어로 쓰면 LLM 컴파일러가 IR 로 변환." },
      { n: "4", title: "에이전트 사용", body: "Claude Code 평소처럼 사용. 매 도구 호출마다 PreToolUse 가 cloud 한테 verdict 물어보고, pass 만 실행, block/ask 는 막거나 HITL 큐." },
      { n: "5", title: "원장 확인", body: "대시보드 /ledger 에서 모든 결정 시간순. 체인 무결성 검증은 GET 한 번. 모든 항목 서명되어 있어 위·변조 즉시 감지." },
    ],
  },
  pricing: {
    heading: "두 가지 운영 방식",
    sub: "기능은 동일. 운영을 본인이 하느냐, 우리가 하느냐의 차이입니다.",
    plans: [
      {
        name: "Self-host", price: "무료", cap: "Apache 2.0 · 영구",
        features: [
          "전체 소스 GitHub 공개 (코드 직접 감사)",
          "Helm chart / fly.io / docker compose 다 지원",
          "본인 인프라에 본인 데이터 (원장 본인 PVC)",
          "Korean legal-domain 프리셋 포함",
          "GitHub Discussions 커뮤니티 지원",
        ],
        cta: "GitHub 가서 보기", primary: false,
      },
      {
        name: "Clawy Pro+", price: "포함", cap: "Pro+ 구독에 호스티드 인스턴스 번들",
        features: [
          "운영 부담 0 — 호스티드 인스턴스 저희가 운영",
          "Stripe 결제 → 키 자동 발급 + 이메일",
          "한국 리전 배포, 데이터 격리, Slack 지원",
          "키 자동 회전 + 원장 백업",
          "GA SLA 99.5% (알파 기간 best-effort)",
        ],
        cta: "Pro+ 시작", primary: true,
      },
    ],
  },
  faq: {
    heading: "자주 묻는 질문",
    items: [
      {
        q: "도구 호출 페이로드가 우리 서버로 전송되나요?",
        a: "검증 시 payload 텍스트가 cloud 에 도달하지만 본문은 저장하지 않습니다. 저장되는 건 verdict + reasons + 정책 id 입니다. LLM critic kind 를 쓰면 criterion + payload 가 LLM 공급자(Anthropic/OpenAI) 로 가는데, 이건 정책마다 선택입니다.",
      },
      {
        q: "Claude Code 외 다른 에이전트도 지원하나요?",
        a: "현재는 Claude Code 의 hooks 메커니즘에 통합. 동일 패턴(PreToolUse hook + JSON 응답)이면 다른 에이전트도 호환 가능. Cursor, Continue 등은 알파 사용자 수요에 따라 우선순위 조정 중.",
      },
      {
        q: "온프레미스 / Air-gapped 배포는?",
        a: "OSS 이므로 가능합니다. 다만 LLM critic 와 SHACL 은 외부 의존 (각각 LLM 공급자 / pyshacl) 이 필요해서, air-gapped 환경에선 regex / wired verifier kind 만 사용 가능.",
      },
      {
        q: "원장이 정말 위·변조 불가능한가요?",
        a: "각 항목은 SHA-256 으로 이전 항목과 체인 연결, Ed25519 로 서명. 단일 행 수정 시 모든 후속 해시가 깨져 무결성 검증에서 즉시 감지. 키 회전(kid) 지원으로 키 유출 시에도 이력 보존.",
      },
      {
        q: "HITL 큐는?",
        a: "정책이 'ask' archetype 인 정책은 조건 fail 시 review 큐에 들어갑니다. 대시보드 /hitl 에서 검토자가 승인·거부. 승인 시 서명 토큰이 발급되어 호출 재개.",
      },
    ],
  },
  cta: {
    heading: "5분 안에 시작",
    body: "Pro+ 결제 = 자동 키 + 호스티드 인스턴스. 또는 GitHub 에서 본인 인프라에 직접 호스팅.",
    cta: "Pro+ 시작",
  },
} satisfies {
  hero: HeroCopy; capabilities: CapabilitiesCopy; why: WhyCopy
  how: HowCopy; pricing: PricingCopy; faq: FAQCopy; cta: CTACopy
}

const EN = {
  hero: {
    eyebrow: "Open Source · Apache 2.0",
    title: "Govern every Claude Code tool call.",
    subtitle:
      "A drop-in PreToolUse gate. Each call is judged block / ask / audit; every decision sealed in an Ed25519-signed, hash-chained ledger. Self-host free, hosted bundled into Clawy Pro+.",
    cta: "Start with Pro+",
    ctaSecondary: "Or self-host",
    alpha: "Apache 2.0 · full source on GitHub · hosted via Clawy Pro+ when you'd rather not run ops.",
    terminalIntro: "# Claude Code → magi-gate (PreToolUse)",
    terminalUser: "Bash: aws s3 rm s3://prod-backups --recursive",
    terminalDeny: "✗ MAGI: aws_key_detected — 'AKIA…' regex matched payload (policy: block-prod-rm)",
    terminalLedger: "# verdict deny · ledger entry h=8a6f… (signed, chained)",
  },
  capabilities: {
    heading: "Everything the policy model can express",
    sub: "8 hook events × 3 action archetypes × 4 condition kinds. Authored from the dashboard wizard, no code change.",
    groups: [
      {
        label: "WHEN — 8 hook events",
        tagline: "At any lifecycle moment",
        items: [
          "PreToolUse / PostToolUse — before / after tool runs",
          "UserPromptSubmit — before prompt reaches the LLM",
          "PreCompact — before context compaction (protect evidence chain)",
          "Stop / SubagentStop — main / sub agent stops",
          "SessionStart / SessionEnd — session boundary markers",
        ],
      },
      {
        label: "WHAT — 3 archetypes",
        tagline: "Name what the policy is for",
        items: [
          "Block — refuse the call when the condition fails",
          "Ask — send to the review queue, human approves",
          "Audit — record to the ledger, never blocks",
          "(unconditional emit-signal also expressed as audit + no condition)",
        ],
      },
      {
        label: "CONDITION — 4 kinds",
        tagline: "Express the rule the way it fits",
        items: [
          "Wired verifier — built-in checks (citation, privilege, …)",
          "Inline regex — Python re pattern against the payload",
          "LLM critic — natural-language rule (LLM-judged, preview)",
          "SHACL shape — semantic Turtle validation (preview)",
        ],
      },
    ],
  },
  why: {
    heading: "Why magi-control-plane",
    items: [
      {
        q: "Agents fire faster than humans review",
        a: "Tool-call decisions race past the operator. A single wrong call hits infra / data / reputation in real time. Catching it at the PreToolUse hook keeps recovery cost out of the picture.",
      },
      {
        q: "You can't reconstruct an audit chain after the fact",
        a: "Which calls passed, which were blocked, who approved what — collecting it from logs after the incident is too late. magi seals every decision with an Ed25519 signature and SHA-256 hash chain at the moment the verdict is reached.",
      },
      {
        q: "Leave the agent untouched",
        a: "One managed-settings.json + one bash shim. Zero agent workflow change, zero SDK dependency. Follows Claude Code's PreToolUse hook contract — other compatible agents are a small adapter away.",
      },
    ],
  },
  how: {
    heading: "How it works",
    steps: [
      { n: "1", title: "Get a key", body: "Subscribe to Clawy Pro+ — tenant + API key auto-provisioned and emailed. Self-hosting? Issue the key from your own instance." },
      { n: "2", title: "One-line install", body: "curl -fsSL <your-instance>/install.sh | bash -s -- mcp_… drops ~/.claude/managed-settings.json + the bash gate." },
      { n: "3", title: "Author a policy", body: "6-step Guided wizard (When → What → Condition → Specifics → Name → Review). Or describe the policy in natural language and the LLM compiler emits IR." },
      { n: "4", title: "Use Claude Code", body: "Use the agent as before. Each tool call triggers PreToolUse → cloud verdict. Pass executes, block refuses, ask routes to the HITL queue." },
      { n: "5", title: "Inspect the ledger", body: "Dashboard /ledger shows every decision in time order. Chain integrity verified by a single GET. Every entry signed; tampering caught instantly." },
    ],
  },
  pricing: {
    heading: "Two ways to run it",
    sub: "Same feature set. The only difference is who runs the cloud instance.",
    plans: [
      {
        name: "Self-host", price: "Free", cap: "Apache 2.0 · forever",
        features: [
          "Full source on GitHub (audit the code yourself)",
          "Helm chart / fly.io / docker compose supported",
          "Your infra, your data (ledger on your PVC)",
          "Korean legal-domain preset included",
          "Community support via GitHub Discussions",
        ],
        cta: "See on GitHub", primary: false,
      },
      {
        name: "Clawy Pro+", price: "Included", cap: "hosted instance bundled into Pro+ subscription",
        features: [
          "Zero ops — we run the hosted instance",
          "Auto-provisioned at subscribe time (Stripe → key emailed)",
          "Korea-region deploy, data isolation, email + Slack support",
          "Auto key rotation + ledger backups",
          "GA SLA 99.5% (best-effort during alpha)",
        ],
        cta: "Start Pro+", primary: true,
      },
    ],
  },
  faq: {
    heading: "FAQ",
    items: [
      {
        q: "Does the tool-call payload reach your servers?",
        a: "Payload text reaches the cloud at verify time but is NOT persisted — only the verdict, reasons, and policy id are sealed in the ledger. If you author an LLM critic condition, the criterion + payload are sent to the configured LLM provider; that's per-policy and opt-in.",
      },
      {
        q: "Other agents besides Claude Code?",
        a: "Today we integrate via Claude Code's hooks mechanism. Any agent that emits PreToolUse-style hooks with JSON responses can adapt with a small shim. Cursor / Continue are on the roadmap — alpha-user demand drives priority.",
      },
      {
        q: "On-prem / air-gapped deploy?",
        a: "Yes — the whole project is OSS. Note the LLM critic and SHACL kinds depend on external libs (LLM provider / pyshacl) so air-gapped installs are restricted to regex + wired-verifier conditions.",
      },
      {
        q: "Is the ledger really tamper-evident?",
        a: "Each entry is SHA-256-chained to its predecessor and Ed25519-signed. Any single-row mutation breaks every subsequent hash; the chain-integrity endpoint catches it instantly. Key rotation (kid) preserves history even when the signing key changes.",
      },
      {
        q: "How does HITL work?",
        a: "Policies with archetype = ask send a review-queue entry on condition fail. A reviewer approves / rejects from the dashboard /hitl page; approval issues a signed token that resumes the call.",
      },
    ],
  },
  cta: {
    heading: "Get started in 5 minutes",
    body: "Subscribe to Pro+ for auto-provisioned hosted. Or fork on GitHub and self-host on your own infra.",
    cta: "Start Pro+",
  },
} satisfies {
  hero: HeroCopy; capabilities: CapabilitiesCopy; why: WhyCopy
  how: HowCopy; pricing: PricingCopy; faq: FAQCopy; cta: CTACopy
}
