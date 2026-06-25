import Link from "next/link"
import { getLocale, getT } from "@/lib/i18n/server"
import { Code } from "@/components/ui"
import { DocsLayout, DOCS_NAV, navLabelKey } from "./_components/DocsLayout"
import { CalloutAside } from "./_components/CalloutAside"
import {
  HOOK_EVENTS_ALL,
  CONTEXT_ACCEPTING_EVENT_COUNT,
} from "@/lib/runtime-manifest"

/**
 * D78: /docs index. 3-paragraph quickstart + a card grid linking to
 * each of the 10 docs. Statically rendered (no LLM, no cloud).
 *
 * Review fix: total event count is derived from the runtime manifest
 * (gated against `src/magi_cp/policy/ir.py`), separately from the
 * context-accepting subset which the inject-context page narrates.
 */
export const dynamic = "force-static"

const TOTAL_HOOK_EVENTS = HOOK_EVENTS_ALL.length
const CONTEXT_EVENTS = CONTEXT_ACCEPTING_EVENT_COUNT

/* One-line descriptors for the card grid. Titles themselves are pulled
 * through `t("docs.nav.*")` so the i18n dict drives both the rail and
 * the card grid (review fix: dead `docs.nav.*` keys are now load-
 * bearing). */
const ONELINER_KO: Record<string, string> = {
  "concepts":        "정책·verifier·evidence·팩·prebuilt 의 뜻 한 줄 정리.",
  "first-policy":    "prebuilt 하나를 켜고 시뮬레이터로 확인까지.",
  "run-command":     "훅이 떴을 때 셸 명령이나 첨부 스크립트를 돌리는 법.",
  "inject-context":  `${CONTEXT_EVENTS}개 이벤트에서 LLM 입력을 보강하고 ${TOTAL_HOOK_EVENTS - CONTEXT_EVENTS}개는 왜 빠졌는지.`,
  "input-rewrite":   "PreToolUse 에서 도구 호출 인자를 안전하게 고치는 DSL.",
  "conversational":  "자연어로 정책을 묘사하고 wizard 로 넘기는 흐름.",
  "env-reference":   "MAGI_CP_* 모든 환경변수의 기본값과 한 줄 설명.",
  "troubleshooting": "자주 마주치는 에러와 해결 방법.",
  "upgrade":         "Docker 스택을 안전하게 올리고 prebuilt id 안정성 약속.",
}

const ONELINER_EN: Record<string, string> = {
  "concepts":        "One-line definitions for policy, verifier, evidence, pack, prebuilt.",
  "first-policy":    "Enable a prebuilt and confirm it with the simulator.",
  "run-command":     "Run a shell command or attached script when a hook fires.",
  "inject-context":  `Inject context on ${CONTEXT_EVENTS} hook events, why ${TOTAL_HOOK_EVENTS - CONTEXT_EVENTS} are excluded.`,
  "input-rewrite":   "Safely transform tool-call arguments at PreToolUse.",
  "conversational":  "Describe a policy in natural language, hand off to wizard.",
  "env-reference":   "Every MAGI_CP_* env var with default + one-line description.",
  "troubleshooting": "Common errors and how to fix them.",
  "upgrade":         "Upgrade the docker stack and the prebuilt id stability promise.",
}

export default async function DocsIndexPage() {
  const isKo = getLocale() === "ko"
  const { t } = await getT()
  const oneliners = isKo ? ONELINER_KO : ONELINER_EN
  const intro = isKo ? (
    <>
      <p>
        Magi Control Plane 은 Claude Code 의 훅 위에서 도는 결정론적 게이트입니다.
        Claude 가 도구를 호출하기 직전·직후, 또는 메시지를 받기 직전 같은 {TOTAL_HOOK_EVENTS}가지 이벤트마다
        정책을 한 번씩 평가해서 통과·차단·리뷰 요청·컨텍스트 주입 같은 동작을 강제합니다.
        그중 {CONTEXT_EVENTS}개 이벤트가 컨텍스트 주입을 받습니다
        (자세한 건 <Link href="/docs/inject-context">컨텍스트 주입</Link>).
      </p>
      <p>
        대시보드는 그 정책을 만드는 도구입니다. <Link href="/rules">/rules</Link> 에서 prebuilt 를 켜거나,
        <Link href="/policies/new"> /policies/new</Link> 에서 자기 정책을 작성합니다.
        클라우드(컨트롤 플레인)는 정책 IR 만 들고 있고, 실제 차단은
        설치된 Claude Code 플러그인이 합니다.
        로컬 PreToolUse/PostToolUse 게이트는 직접 LLM 을 부르지 않습니다.
        LLM 호출은 정책 작성 시점 (자연어 → IR 컴파일/리뷰) 과 클라우드 측
        <Code inline> llm_critic</Code> verifier 평가에서만 발생합니다
        (자세한 건 <Link href="/docs/concepts">개념</Link> 의 verifier 절).
      </p>
      <p>
        이 문서는 아래 10 챕터로 구성돼 있습니다. 처음이라면 <Link href="/docs/concepts">개념</Link> →
        <Link href="/docs/first-policy"> 첫 정책</Link> 순서로 읽으세요. 막혔다면
        <Link href="/docs/troubleshooting"> 문제 해결</Link> 또는
        <Link href="/docs/env-reference"> 환경변수 레퍼런스</Link> 를 보세요.
      </p>
    </>
  ) : (
    <>
      <p>
        Magi Control Plane is a deterministic gate that runs on top of Claude Code's hooks.
        On every one of {TOTAL_HOOK_EVENTS} hook events (around tool calls, prompts, and
        session boundaries) the plane evaluates your policies once and enforces pass, block,
        ask-for-review, or context-injection accordingly. {CONTEXT_EVENTS} of those events
        accept context injection (see <Link href="/docs/inject-context">Inject context</Link>).
      </p>
      <p>
        The dashboard is where you author those policies. Toggle a prebuilt on at <Link href="/rules">/rules</Link>,
        or hand-write a custom policy at <Link href="/policies/new">/policies/new</Link>. The cloud
        only stores policy IR; the actual blocking happens locally inside the installed Claude
        Code plugin. The local PreToolUse/PostToolUse gate never calls an LLM directly. LLM
        calls happen at policy authoring time (NL → IR compile and review) and during cloud-side
        <Code inline> llm_critic</Code> verifier evaluation, both surfaced in
        <Link href="/docs/concepts"> Concepts</Link>.
      </p>
      <p>
        These docs are organized into 10 chapters below. New to the control plane? Read
        <Link href="/docs/concepts"> Concepts</Link> → <Link href="/docs/first-policy">First policy</Link> first.
        Stuck? Check <Link href="/docs/troubleshooting">Troubleshooting</Link> or
        <Link href="/docs/env-reference">Env reference</Link>.
      </p>
    </>
  )

  return (
    <DocsLayout
      current="index"
      title={isKo ? "Magi Control Plane 문서" : "Magi Control Plane docs"}
      subtitle={isKo
        ? "처음 설치한 운영자가 10분 안에 작동 모델을 잡을 수 있도록 만든 가이드 10 챕터."
        : "Ten chapters to give a first-time operator a working mental model in ten minutes."
      }
    >
      {intro}

      <CalloutAside tone="tip" title={isKo ? "지금 당장" : "Right now"}>
        {isKo
          ? <>설치를 막 끝냈다면 <Link href="/setup">/setup</Link> 에서 API 키를 검증한 뒤
              <Link href="/docs/first-policy"> 첫 정책</Link> 부터 시작하세요.</>
          : <>If you just finished install, verify your API key at <Link href="/setup">/setup</Link>
              and jump into <Link href="/docs/first-policy">First policy</Link>.</>
        }
      </CalloutAside>

      <h2 className="mt-8 mb-3 text-base font-semibold text-[var(--color-text-primary)]">
        {isKo ? "10 챕터" : "10 chapters"}
      </h2>
      <ul role="list" className="grid gap-3 sm:grid-cols-2">
        {DOCS_NAV.filter((d) => d.slug !== "index").map((d) => {
          const one = oneliners[d.slug]
          return (
            <li key={d.slug}>
              <Link
                href={d.href}
                prefetch={false}
                className="block rounded-xl border border-[var(--color-border-subtle)] bg-white/60 p-4 transition-colors hover:border-[var(--color-accent)]/40 hover:bg-white"
              >
                <div className="font-semibold text-[var(--color-text-primary)]">{t(navLabelKey(d.slug))}</div>
                <div className="mt-1 text-xs text-[var(--color-text-tertiary)] leading-5">
                  {one}
                </div>
              </Link>
            </li>
          )
        })}
      </ul>
    </DocsLayout>
  )
}
