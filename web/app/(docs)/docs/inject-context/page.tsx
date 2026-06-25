import Link from "next/link"
import { getLocale } from "@/lib/i18n/server"
import { Code, CodeBlock } from "@/components/ui"
import { DocsLayout } from "../_components/DocsLayout"
import { CalloutAside } from "../_components/CalloutAside"
import {
  CONTEXT_INJECTION_EXCLUDED_EVENTS,
  CONTEXT_INJECTION_ALTERNATE_CHANNEL,
  CONTEXT_ACCEPTING_EVENT_COUNT,
  HOOK_EVENTS_ALL,
} from "@/lib/runtime-manifest"

/**
 * D78: how additionalContext works in CC, which events accept it, and
 * which are excluded.
 *
 * Review fix: the excluded-event list and the per-event alternate-
 * channel reason are imported from `runtime-manifest`, which the
 * vitest gate pins to `src/magi_cp/policy/ir.py`. Of the 30 CC hook
 * events, 8 are excluded (4 with a specialized `hookSpecificOutput`
 * shape, 4 with no downstream same-session model turn); 22 accept
 * `additionalContext`. `SessionStart` IS in the accepting set. CC
 * uses additionalContext on SessionStart to prime the first model
 * turn.
 */
export const dynamic = "force-static"

export default function InjectContextPage() {
  const isKo = getLocale() === "ko"
  const totalEvents = HOOK_EVENTS_ALL.length
  const acceptingCount = CONTEXT_ACCEPTING_EVENT_COUNT

  return (
    <DocsLayout
      current="inject-context"
      title={isKo ? "컨텍스트 주입 (inject_context)" : "Injecting context (inject_context)"}
      subtitle={isKo
        ? "정책이 LLM 입력 앞쪽에 짧은 메모를 끼워 넣는 액션. 차단 없이 행동을 유도합니다."
        : "An action that prepends a short note to the LLM input. Shapes behavior without blocking."
      }
    >
      {isKo ? (
        <>
          <h2>왜 쓰나</h2>
          <p>
            Claude Code 가 잘못된 결정을 할 만한 자리에 미리 사실 한 줄을 떨궈 두는 게
            가장 가볍고 결정론적인 가드레일입니다. 차단 정책은 사용자 경험을 끊지만,
            컨텍스트 주입은 LLM 이 알아서 더 안전한 길을 고르게 만듭니다.
          </p>

          <h2>어떻게 동작하나</h2>
          <p>
            Claude Code 의 훅 응답에 <Code inline>hookSpecificOutput.additionalContext</Code> 를
            돌려주면 다음 LLM 호출의 시스템 프롬프트에 그 문자열이 끼어 들어갑니다.
            magi-cp 의 <Code inline>ContextInjectionPolicy</Code> IR 은 그 문자열 본문을
            정책 안에 그대로 들고 있고, 게이트가 그걸 한 글자도 안 바꾸고 내보냅니다.
            런타임 템플릿 렌더링은 없습니다 (<Code inline>{`{{ ... }}`}</Code> 같은 마커는 그대로 출력됩니다).
          </p>
          <CodeBlock>{`{
  "type": "context_injection",
  "id": "legal-citation-required",
  "description": "민감 파일 수정 시 형사소송법 인용 요청",
  "event": "PreToolUse",
  "matcher": "Edit",
  "template": "이 파일을 수정하려면 한국 형사소송법 제 N 조 인용이 필요합니다."
}`}</CodeBlock>

          <h2>지원 이벤트 ({acceptingCount}개)</h2>
          <p>
            Claude Code 의 {totalEvents}개 훅 이벤트 중 {acceptingCount}개가
            <Code inline> additionalContext</Code> 를 받습니다. 나머지 {CONTEXT_INJECTION_EXCLUDED_EVENTS.length}개는
            전용 <Code inline>hookSpecificOutput</Code> 필드를 쓰거나 (Elicitation /
            ElicitationResult / WorktreeCreate / MessageDisplay) 다음 같은 세션 LLM 호출이 없어
            (Stop / StopFailure / SessionEnd / SubagentStop) CC 가 조용히 버립니다.
            <Code inline>SessionStart</Code> 는 받는 쪽에 들어 있습니다.
            첫 LLM 호출을 위해 CC 가 이 훅의 <Code inline>additionalContext</Code> 를 실제로 사용합니다.
          </p>
          <CalloutAside tone="warn" title={`제외된 ${CONTEXT_INJECTION_EXCLUDED_EVENTS.length}개 이벤트`}>
            <ul className="list-disc pl-5 m-0">
              {CONTEXT_INJECTION_EXCLUDED_EVENTS.map((e) => (
                <li key={e}>
                  <Code inline>{e}</Code>
                  {": "}
                  {CONTEXT_INJECTION_ALTERNATE_CHANNEL[e]}
                </li>
              ))}
            </ul>
            <p className="mt-2 m-0">
              이 이벤트들에서는 wizard 가 inject_context 액션을 비활성화합니다. IR 을 직접 PUT 하면
              <Code inline> ContextInjectionPolicy.validate()</Code> 에서 거부됩니다.
            </p>
          </CalloutAside>

          <h2>템플릿 본문</h2>
          <p>
            <Code inline>template</Code> 은 IR 안에 들어가는 상수 문자열입니다. 런타임이 변수를
            치환해 주지 않으므로 본문에 <Code inline>{`{{ ... }}`}</Code> 를 쓰면 문자 그대로
            <Code inline>additionalContext</Code> 에 그대로 흘러갑니다. 정책에 따라 본문이
            달라야 한다면 정책을 여러 개 두세요.
          </p>
          <p>
            <Code inline>MAGI_CP_CONTEXT_TEMPLATES_DIR</Code> 는 컴파일러가 IR 을 만들 때
            본문을 끌어다 끼워 넣는 작성 시점 자료실입니다 (자세한 건
            <Code inline> magi_cp/policy/compiler.py</Code>). 런타임 렌더링 seam 이 아닙니다.
          </p>

          <h2>관련 정책 종류</h2>
          <ul>
            <li>verifier 가 <Code inline>not_applicable</Code> 일 때만 inject 하는 안전한 조합</li>
            <li>여러 정책이 같은 훅에 붙으면 inject 된 문자열은 줄바꿈으로 합쳐집니다</li>
            <li>대화형 작성기 사용법은 <Link href="/docs/conversational">conversational</Link></li>
          </ul>
        </>
      ) : (
        <>
          <h2>Why</h2>
          <p>
            Quietly nudging the model is the cheapest deterministic guardrail. Block actions
            interrupt the user; injecting context lets the model find the safer path on its own.
          </p>

          <h2>How it works</h2>
          <p>
            When you return <Code inline>hookSpecificOutput.additionalContext</Code> from a hook,
            Claude Code prepends that string to the next LLM call's system prompt. magi-cp's
            <Code inline> ContextInjectionPolicy</Code> IR carries the body verbatim and the gate
            emits it byte-for-byte. There is no runtime templating, so any
            <Code inline> {`{{ ... }}`}</Code> markers in the body land in
            <Code inline> additionalContext</Code> literally.
          </p>
          <CodeBlock>{`{
  "type": "context_injection",
  "id": "legal-citation-required",
  "description": "Require a KCPC citation on edits to sensitive files",
  "event": "PreToolUse",
  "matcher": "Edit",
  "template": "Editing this file requires a citation to KCPC Article N."
}`}</CodeBlock>

          <h2>Supported events ({acceptingCount})</h2>
          <p>
            Of Claude Code's {totalEvents} hook events, {acceptingCount} accept
            <Code inline> additionalContext</Code>. The other {CONTEXT_INJECTION_EXCLUDED_EVENTS.length} are excluded:
            four use a specialized <Code inline>hookSpecificOutput</Code> field (Elicitation /
            ElicitationResult / WorktreeCreate / MessageDisplay) and four have no downstream
            same-session model turn (Stop / StopFailure / SessionEnd / SubagentStop) so CC drops
            the field silently. <Code inline>SessionStart</Code> IS in the accepting set; CC uses
            its <Code inline>additionalContext</Code> to prime the first model turn of the
            session.
          </p>
          <CalloutAside tone="warn" title={`The ${CONTEXT_INJECTION_EXCLUDED_EVENTS.length} excluded events`}>
            <ul className="list-disc pl-5 m-0">
              {CONTEXT_INJECTION_EXCLUDED_EVENTS.map((e) => (
                <li key={e}>
                  <Code inline>{e}</Code>
                  {": "}
                  {CONTEXT_INJECTION_ALTERNATE_CHANNEL[e]}
                </li>
              ))}
            </ul>
            <p className="mt-2 m-0">
              The wizard hides the inject_context action when one of these is selected. PUTting
              the IR directly fails inside <Code inline>ContextInjectionPolicy.validate()</Code>.
            </p>
          </CalloutAside>

          <h2>The template body</h2>
          <p>
            <Code inline>template</Code> is a constant string the operator authored, stored in
            the IR. The runtime does not interpolate variables, so any
            <Code inline> {`{{ ... }}`}</Code> markers ride through to
            <Code inline> additionalContext</Code> as-is. If the body needs to vary by trigger,
            split the policy.
          </p>
          <p>
            <Code inline>MAGI_CP_CONTEXT_TEMPLATES_DIR</Code> is an authoring-time pool the
            compiler reads when building the IR (see <Code inline>magi_cp/policy/compiler.py</Code>).
            It is not a runtime render seam.
          </p>

          <h2>Composition</h2>
          <ul>
            <li>Inject only on <Code inline>not_applicable</Code> for a low-friction nudge.</li>
            <li>Multiple injecting policies on the same hook are joined with newlines.</li>
            <li>For NL authoring, see <Link href="/docs/conversational">conversational</Link>.</li>
          </ul>
        </>
      )}
    </DocsLayout>
  )
}
