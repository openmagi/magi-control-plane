import type { Metadata } from "next"
import Link from "next/link"
import { getLocale, getT } from "@/lib/i18n/server"
import { Badge, Button, Card, CardHeader } from "@/components/ui"

export const dynamic = "force-dynamic"

export const metadata: Metadata = {
  title: "Claude Code를 변호사가 안심하고 쓸 수 있게 — magi-control-plane",
  description:
    "Claude Code의 모든 도구 호출을 정책으로 게이트하고, 위·변조 불가능한 감사 원장에 봉인합니다. 한국 로펌을 위한 비공개 알파 파일럿 — 무료.",
  openGraph: {
    title: "magi-control-plane — Claude Code 거버넌스 게이트 (Alpha)",
    description:
      "Claude Code 워크플로 변경 없음. 정책 위반 호출은 차단, 통과한 호출은 모두 Ed25519 서명 + 해시 체인 원장에 기록.",
    type: "website",
    locale: "ko_KR",
    alternateLocale: "en_US",
  },
  twitter: { card: "summary_large_image" },
  alternates: { canonical: "/welcome" },
  robots: { index: true, follow: true },
}

/**
 * Public marketing landing for the alpha pilot.
 *
 * Korean-first (Korean legal firms = beachhead). Reuses the vendored UI
 * primitives so the marketing surface stays visually consistent with the
 * dashboard — no separate brand system, no shadcn drift.
 */
export default async function WelcomePage() {
  const locale = await getLocale()
  const isKo = locale === "ko"
  const C = isKo ? KO : EN
  return (
    <div className="space-y-16 pb-16">
      <Hero c={C.hero} />
      <Problems c={C.problems} />
      <How c={C.how} />
      <Pricing c={C.pricing} isKo={isKo} />
      <FAQ c={C.faq} />
      <CTA c={C.cta} />
    </div>
  )
}

type HeroCopy = { eyebrow: string; title: string; subtitle: string; cta: string; ctaSecondary: string; alpha: string }
type ProblemsCopy = { heading: string; items: Array<{ q: string; a: string }> }
type HowCopy = { heading: string; steps: Array<{ n: string; title: string; body: string }> }
type PricingCopy = { heading: string; plans: Array<{ name: string; price: string; cap: string; features: string[]; cta: string; primary: boolean }> }
type FAQCopy = { heading: string; items: Array<{ q: string; a: string }> }
type CTACopy = { heading: string; body: string; cta: string }

function Hero({ c }: { c: HeroCopy }) {
  return (
    <section className="pt-12 pb-2 text-center">
      <Badge variant="info">{c.eyebrow}</Badge>
      <h1 className="mt-4 text-4xl md:text-5xl font-semibold tracking-tight text-balance text-[var(--color-text-primary)]">
        {c.title}
      </h1>
      <p className="mt-5 mx-auto max-w-2xl text-base md:text-lg text-pretty text-[var(--color-text-secondary)] leading-7">
        {c.subtitle}
      </p>
      <div className="mt-7 flex flex-wrap items-center justify-center gap-3">
        <Link href="/signup" prefetch={false}>
          <Button variant="primary" size="lg">{c.cta}</Button>
        </Link>
        <Link href="#how" prefetch={false}>
          <Button variant="ghost" size="lg">{c.ctaSecondary}</Button>
        </Link>
      </div>
      <p className="mt-4 text-xs text-[var(--color-text-tertiary)]">{c.alpha}</p>
    </section>
  )
}

function Problems({ c }: { c: ProblemsCopy }) {
  return (
    <section>
      <h2 className="text-2xl font-semibold text-[var(--color-text-primary)]">{c.heading}</h2>
      <div className="mt-6 grid gap-4 md:grid-cols-3">
        {c.items.map((it, i) => (
          <Card key={i}>
            <CardHeader title={it.q} />
            <p className="text-sm text-[var(--color-text-secondary)] leading-6 text-pretty">{it.a}</p>
          </Card>
        ))}
      </div>
    </section>
  )
}

function How({ c }: { c: HowCopy }) {
  return (
    <section id="how">
      <h2 className="text-2xl font-semibold text-[var(--color-text-primary)]">{c.heading}</h2>
      <ol className="mt-6 space-y-4">
        {c.steps.map(s => (
          <li key={s.n} className="flex gap-4">
            <div className="shrink-0 w-9 h-9 rounded-md border border-[var(--color-border-subtle)] flex items-center justify-center text-sm font-medium text-[var(--color-text-secondary)]">
              {s.n}
            </div>
            <div className="min-w-0">
              <h3 className="text-md font-medium text-[var(--color-text-primary)] m-0">{s.title}</h3>
              <p className="mt-1 text-sm text-[var(--color-text-secondary)] leading-6 text-pretty">{s.body}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}

function Pricing({ c, isKo }: { c: PricingCopy; isKo: boolean }) {
  return (
    <section>
      <h2 className="text-2xl font-semibold text-[var(--color-text-primary)]">{c.heading}</h2>
      <div className="mt-6 grid gap-4 md:grid-cols-2">
        {c.plans.map((p, i) => (
          <Card key={i} className={p.primary ? "border-[var(--color-border-focus)]" : undefined}>
            <div className="flex items-baseline justify-between gap-3">
              <h3 className="text-lg font-semibold text-[var(--color-text-primary)] m-0">{p.name}</h3>
              <div className="text-right">
                <div className="text-xl font-semibold text-[var(--color-text-primary)]">{p.price}</div>
                <div className="text-xs text-[var(--color-text-tertiary)]">{p.cap}</div>
              </div>
            </div>
            <ul className="mt-4 space-y-2 text-sm text-[var(--color-text-secondary)]">
              {p.features.map((f, j) => (
                <li key={j} className="flex items-start gap-2">
                  <span aria-hidden="true" className="mt-1 inline-block h-1.5 w-1.5 rounded-full bg-[var(--color-text-tertiary)]" />
                  <span>{f}</span>
                </li>
              ))}
            </ul>
            <div className="mt-5">
              <Link href={p.primary ? "/signup" : "mailto:kevin@openmagi.ai"} prefetch={false}>
                <Button variant={p.primary ? "primary" : "secondary"} size="md">{p.cta}</Button>
              </Link>
            </div>
          </Card>
        ))}
      </div>
      <p className="mt-4 text-xs text-[var(--color-text-tertiary)]">
        {isKo
          ? "알파 파일럿 기간 동안 모든 사용자에게 무료입니다. GA 출시 시 별도 안내드립니다."
          : "Free for all users during the alpha pilot. We'll announce pricing well before GA."}
      </p>
    </section>
  )
}

function FAQ({ c }: { c: FAQCopy }) {
  return (
    <section>
      <h2 className="text-2xl font-semibold text-[var(--color-text-primary)]">{c.heading}</h2>
      <div className="mt-6 space-y-3">
        {c.items.map((it, i) => (
          <details key={i} className="rounded-md border border-[var(--color-border-subtle)] p-4 open:bg-[var(--color-surface-overlay)]">
            <summary className="cursor-pointer text-sm font-medium text-[var(--color-text-primary)]">{it.q}</summary>
            <p className="mt-3 text-sm leading-6 text-[var(--color-text-secondary)] text-pretty">{it.a}</p>
          </details>
        ))}
      </div>
    </section>
  )
}

function CTA({ c }: { c: CTACopy }) {
  return (
    <section className="text-center rounded-lg border border-[var(--color-border-subtle)] p-10 bg-[var(--color-surface-overlay)]">
      <h2 className="text-2xl font-semibold text-[var(--color-text-primary)]">{c.heading}</h2>
      <p className="mt-3 mx-auto max-w-xl text-sm text-[var(--color-text-secondary)] text-pretty">{c.body}</p>
      <div className="mt-6">
        <Link href="/signup" prefetch={false}>
          <Button variant="primary" size="lg">{c.cta}</Button>
        </Link>
      </div>
    </section>
  )
}

const KO = {
  hero: {
    eyebrow: "Alpha · 한국 로펌 파일럿",
    title: "Claude Code를 변호사가 안심하고 쓸 수 있게.",
    subtitle:
      "터미널 밖에서 작동하는 거버넌스 게이트. 모델이 무엇을 호출하든, 매번 정책을 통과한 호출만 실행되고 — 모든 단계는 위·변조 불가능한 감사 원장에 기록됩니다. 변호사에게는 익숙한 워크플로, 파트너에게는 감사 가능한 증거.",
    cta: "알파 신청하기",
    ctaSecondary: "작동 방식 보기",
    alpha: "현재 한국 로펌 대상 비공개 알파 — 무료, 영업일 기준 1일 내 응답",
  },
  problems: {
    heading: "왜 magi-control-plane인가",
    items: [
      {
        q: "AI 보조에 비밀유지 의무가 따라옵니다",
        a: "Claude Code가 외부 도구를 호출할 때마다 클라이언트 자료가 노출될 위험이 있습니다. magi는 호출 시점에 정책을 강제하고, 정책을 통과하지 못한 호출은 차단합니다.",
      },
      {
        q: "감사를 견디는 증거 체인이 필요합니다",
        a: "어떤 인용이 검증됐는지, 어떤 단계를 사람이 승인했는지 — 모두 Ed25519 서명 + 해시 체인으로 봉인된 원장에 저장. 사고 후 재구성하지 않아도 됩니다.",
      },
      {
        q: "Claude Code를 그대로 쓰고 싶습니다",
        a: "기존 워크플로 변경 없음. managed-settings.json + bash 게이트 한 줄. PreToolUse hook에서 우리 클라우드를 호출해 정책을 적용 — 변호사는 평소처럼 Claude Code를 씁니다.",
      },
    ],
  },
  how: {
    heading: "동작 방식",
    steps: [
      { n: "1", title: "알파 신청", body: "이메일 한 줄로 신청. 영업일 1일 내 API 키와 설치 가이드를 보내드립니다." },
      { n: "2", title: "managed-settings.json 설치", body: "5분 안에 끝나는 설치. ~/.claude/managed-settings.json 한 파일과 bash 게이트 스크립트 한 줄을 떨어뜨립니다." },
      { n: "3", title: "정책 작성 또는 프리셋 선택", body: "자연어로 정책을 쓰면 LLM이 IR로 컴파일, 사람이 한 번 검토. 또는 한국 법무 도메인용 사전 정의 프리셋 사용." },
      { n: "4", title: "Claude Code 사용", body: "변호사는 평소처럼 Claude Code 사용. 모델이 도구를 호출할 때마다 PreToolUse 게이트가 정책을 적용 — 통과만 실행, 거부는 차단, 검토 필요는 큐로." },
      { n: "5", title: "감사 원장 확인", body: "대시보드에서 모든 통과·거부·HITL 결정을 시간순으로 확인. 체인 무결성 검증은 한 번의 GET 요청." },
    ],
  },
  pricing: {
    heading: "가격",
    plans: [
      {
        name: "Alpha 파일럿",
        price: "무료",
        cap: "알파 기간 한정",
        features: [
          "전 기능 사용 (정책 컴파일, 검증, HITL, 감사 원장)",
          "한국 법무 도메인 프리셋",
          "이메일 + Slack 지원 (영업일 4시간 내 응답)",
          "전용 테넌트, 데이터 격리",
        ],
        cta: "알파 신청",
        primary: true,
      },
      {
        name: "GA (예정)",
        price: "—",
        cap: "별도 안내",
        features: [
          "GA 출시 시 알파 사용자에게 우선 안내",
          "단일 노드 + 멀티 노드 옵션",
          "엔터프라이즈 SSO, 별도 SLA",
          "전용 한국 리전 배포 옵션",
        ],
        cta: "관심 등록",
        primary: false,
      },
    ],
  },
  faq: {
    heading: "자주 묻는 질문",
    items: [
      {
        q: "우리 클라이언트 자료가 OpenMagi 서버로 전송되나요?",
        a: "검증에 제출한 텍스트 본문은 저장하지 않습니다. 정책 결과(verdict, reasons)만 감사 원장에 저장. 자연어 정책 컴파일 시에만 외부 LLM(Anthropic/OpenAI)에 자연어 텍스트가 전송됩니다. 자세한 내용은 /legal/privacy 참조.",
      },
      {
        q: "Claude Code 외에 다른 AI 코딩 도구도 지원하나요?",
        a: "현재는 Claude Code의 hooks 메커니즘에 통합. Cursor, Continue 등은 로드맵 검토 중. 알파 사용자 요청 우선 반영.",
      },
      {
        q: "온프레미스 배포가 가능한가요?",
        a: "전체 코드는 GitHub에 공개되며, Helm chart로 자체 호스팅 가능합니다. 알파 기간에는 OpenMagi 호스팅 인스턴스(cloud.openmagi.ai) 사용을 권장 — 운영 부담 없이 빠르게 시작.",
      },
      {
        q: "감사 원장은 정말 위·변조 불가능한가요?",
        a: "각 항목은 SHA-256 해시 + 이전 항목 해시를 체인으로 연결, Ed25519 서명. 단일 항목 수정 시 모든 후속 해시가 깨져 즉시 감지. 키 회전(kid) 지원으로 키 유출 시에도 이력 보존.",
      },
      {
        q: "HITL(사람 승인) 큐는 어떻게 작동하나요?",
        a: "정책이 'review' 판정한 호출은 HITL 큐에 들어가고, 대시보드 /hitl 에서 파트너 또는 지정된 검토자가 승인·거부. 승인 시 서명된 토큰이 발급되어 호출이 재개됩니다.",
      },
    ],
  },
  cta: {
    heading: "5분 안에 시작하세요",
    body: "비공개 알파 — 한국 로펌 우선. 이메일 한 줄이면 응답드립니다.",
    cta: "알파 신청하기",
  },
} satisfies {
  hero: HeroCopy; problems: ProblemsCopy; how: HowCopy;
  pricing: PricingCopy; faq: FAQCopy; cta: CTACopy
}

const EN = {
  hero: {
    eyebrow: "Alpha · Korean Law Firm Pilot",
    title: "Make Claude Code safe for lawyers.",
    subtitle:
      "An out-of-loop governance gate. Every tool call Claude Code makes is policy-checked at execution time — only compliant calls run, and every step is sealed in a tamper-evident audit ledger. Familiar workflow for lawyers, auditable evidence for partners.",
    cta: "Apply for Alpha",
    ctaSecondary: "How it works",
    alpha: "Currently in private alpha for Korean law firms — free, 1 business day response",
  },
  problems: {
    heading: "Why magi-control-plane",
    items: [
      { q: "AI assistance comes with confidentiality duty",
        a: "Every Claude Code tool call risks exposing client data. magi enforces policy at the call site — non-compliant calls are blocked before they execute." },
      { q: "You need an evidence chain that survives audit",
        a: "Which citations were verified, which steps a human approved — sealed in a Ed25519-signed, hash-chained ledger. No need to reconstruct after the fact." },
      { q: "Lawyers want Claude Code unchanged",
        a: "No workflow changes. One managed-settings.json + one bash gate script. PreToolUse hook calls our cloud and enforces policy — lawyers use Claude Code as before." },
    ],
  },
  how: {
    heading: "How it works",
    steps: [
      { n: "1", title: "Apply for alpha", body: "One email. We send your API key + install guide within 1 business day." },
      { n: "2", title: "Install managed-settings.json", body: "5-minute install. One file at ~/.claude/managed-settings.json plus a one-line bash gate." },
      { n: "3", title: "Author policy or pick a preset", body: "Write policy in natural language — LLM compiles to IR, human reviews. Or use the prebuilt Korean legal-domain preset." },
      { n: "4", title: "Use Claude Code", body: "Lawyers work as usual. Every tool call triggers PreToolUse — passes execute, denies block, reviews enter the HITL queue." },
      { n: "5", title: "Inspect the audit ledger", body: "Dashboard shows every pass/deny/HITL decision in time order. Chain integrity verified with a single GET." },
    ],
  },
  pricing: {
    heading: "Pricing",
    plans: [
      { name: "Alpha pilot", price: "Free", cap: "alpha period",
        features: [
          "All features (policy compile, verify, HITL, audit ledger)",
          "Korean legal-domain preset",
          "Email + Slack support (4h response)",
          "Dedicated tenant, data isolation",
        ], cta: "Apply for alpha", primary: true },
      { name: "GA (planned)", price: "—", cap: "TBD",
        features: [
          "Alpha users get priority notice at GA",
          "Single + multi-node deploy options",
          "Enterprise SSO, dedicated SLA",
          "Optional dedicated Korea-region deploy",
        ], cta: "Register interest", primary: false },
    ],
  },
  faq: {
    heading: "FAQ",
    items: [
      { q: "Does my client data go to OpenMagi servers?",
        a: "We do NOT store the text payloads you submit to verifiers — only the verdict (and reasons) is sealed in the audit ledger. Natural-language policy compilation does send your description to external LLMs (Anthropic/OpenAI). See /legal/privacy for full detail." },
      { q: "Do you support coding tools other than Claude Code?",
        a: "Today we integrate via Claude Code's hooks mechanism. Cursor/Continue are on the roadmap; alpha-user demand drives priority." },
      { q: "Can I deploy on-prem?",
        a: "All code is on GitHub; self-host via the Helm chart. During alpha we recommend our hosted instance (cloud.openmagi.ai) so you can move quickly without ops overhead." },
      { q: "Is the ledger really tamper-evident?",
        a: "Each entry is SHA-256 hashed and chained to the previous entry's hash, then Ed25519-signed. Any single-row edit invalidates every subsequent hash. Key rotation (kid) preserves history even if a signing key is rotated." },
      { q: "How does the HITL queue work?",
        a: "Calls policy judges 'review' enter the HITL queue; a partner or designated reviewer approves/rejects from /hitl. Approval issues a signed token that resumes the call." },
    ],
  },
  cta: {
    heading: "Get started in 5 minutes",
    body: "Private alpha — Korean law firms prioritized. One email and we'll get back to you.",
    cta: "Apply for Alpha",
  },
} satisfies {
  hero: HeroCopy; problems: ProblemsCopy; how: HowCopy;
  pricing: PricingCopy; faq: FAQCopy; cta: CTACopy
}
