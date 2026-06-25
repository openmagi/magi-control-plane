import Link from "next/link"
import { getLocale } from "@/lib/i18n/server"
import { DocsLayout, DOCS_NAV } from "./_components/DocsLayout"
import { CalloutAside } from "./_components/CalloutAside"

/**
 * D78: /docs index. 3-paragraph quickstart + a card grid linking to
 * each of the 10 docs. Statically rendered (no LLM, no cloud).
 */
export const dynamic = "force-static"

const TITLES_KO: Record<string, { title: string; one: string }> = {
  "index":           { title: "시작하기",       one: "이 문서가 무엇이고 어디서부터 읽어야 할지." },
  "concepts":        { title: "개념",           one: "정책·verifier·evidence·팩·prebuilt 의 뜻 한 줄 정리." },
  "first-policy":    { title: "첫 정책",        one: "prebuilt 하나를 켜고 시뮬레이터로 확인까지." },
  "run-command":     { title: "스크립트 실행",  one: "훅이 떴을 때 셸 명령이나 첨부 스크립트를 돌리는 법." },
  "inject-context":  { title: "컨텍스트 주입",  one: "26개 이벤트에서 LLM 입력을 보강하고 4개는 왜 빠졌는지." },
  "input-rewrite":   { title: "입력 재작성",    one: "PreToolUse 에서 도구 호출 인자를 안전하게 고치는 DSL." },
  "conversational":  { title: "대화형 작성기",  one: "자연어로 정책을 묘사하고 wizard 로 넘기는 흐름." },
  "env-reference":   { title: "환경변수",       one: "MAGI_CP_* 모든 환경변수의 기본값과 한 줄 설명." },
  "troubleshooting": { title: "문제 해결",      one: "자주 마주치는 에러와 해결 방법." },
  "upgrade":         { title: "업그레이드",     one: "Docker 스택을 안전하게 올리고 prebuilt id 안정성 약속." },
}

const TITLES_EN: Record<string, { title: string; one: string }> = {
  "index":           { title: "Quickstart",       one: "What this is and where to start reading." },
  "concepts":        { title: "Concepts",         one: "One-line definitions for policy, verifier, evidence, pack, prebuilt." },
  "first-policy":    { title: "First policy",     one: "Enable a prebuilt and confirm it with the simulator." },
  "run-command":     { title: "Run a script",     one: "Run a shell command or attached script when a hook fires." },
  "inject-context":  { title: "Inject context",   one: "Inject context on 26 hook events, why 4 are excluded." },
  "input-rewrite":   { title: "Rewrite input",    one: "Safely transform tool-call arguments at PreToolUse." },
  "conversational":  { title: "Conversational",   one: "Describe a policy in natural language, hand off to wizard." },
  "env-reference":   { title: "Env reference",    one: "Every MAGI_CP_* env var with default + one-line description." },
  "troubleshooting": { title: "Troubleshooting",  one: "Common errors and how to fix them." },
  "upgrade":         { title: "Upgrade",          one: "Upgrade the docker stack and the prebuilt id stability promise." },
}

export default async function DocsIndexPage() {
  const isKo = getLocale() === "ko"
  const titles = isKo ? TITLES_KO : TITLES_EN
  const intro = isKo ? (
    <>
      <p>
        Magi Control Plane 은 Claude Code 의 훅 위에서 도는 결정론적 게이트입니다.
        Claude 가 도구를 호출하기 직전·직후, 또는 메시지를 받기 직전 같은 26 가지 이벤트마다
        정책을 한 번씩 평가해서 통과·차단·리뷰 요청·컨텍스트 주입 같은 동작을 강제합니다.
      </p>
      <p>
        대시보드는 그 정책을 만드는 도구입니다. <Link href="/rules">/rules</Link> 에서 prebuilt 를 켜거나,
        <Link href="/policies/new"> /policies/new</Link> 에서 자기 정책을 작성합니다.
        클라우드(컨트롤 플레인)는 정책 IR 만 들고 있고, 실제 차단은
        설치된 Claude Code 플러그인이 합니다. LLM 은 정책을 작성할 때만 부르고, 런타임 게이트는 절대 LLM 을 부르지 않습니다.
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
        On every one of 26 hook events (around tool calls, prompts, and session boundaries)
        the plane evaluates your policies once and enforces pass, block, ask-for-review,
        or context-injection accordingly.
      </p>
      <p>
        The dashboard is where you author those policies. Toggle a prebuilt on at <Link href="/rules">/rules</Link>,
        or hand-write a custom policy at <Link href="/policies/new">/policies/new</Link>. The cloud
        only stores policy IR; the actual blocking happens locally inside the installed Claude
        Code plugin. The LLM is called only at policy-authoring time, never at runtime.
      </p>
      <p>
        These docs are organized into 10 chapters below. New to the plane? Read
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
        : "Ten chapters that get a first-time operator a working mental model in ten minutes."
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
          const meta = titles[d.slug]
          return (
            <li key={d.slug}>
              <Link
                href={d.href}
                prefetch={false}
                className="block rounded-xl border border-[var(--color-border-subtle)] bg-white/60 p-4 transition-colors hover:border-[var(--color-accent)]/40 hover:bg-white"
              >
                <div className="font-semibold text-[var(--color-text-primary)]">{meta.title}</div>
                <div className="mt-1 text-xs text-[var(--color-text-tertiary)] leading-5">
                  {meta.one}
                </div>
              </Link>
            </li>
          )
        })}
      </ul>
    </DocsLayout>
  )
}
