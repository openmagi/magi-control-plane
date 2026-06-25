import Link from "next/link"
import { getLocale } from "@/lib/i18n/server"
import { Code, CodeBlock } from "@/components/ui"
import { DocsLayout } from "../_components/DocsLayout"
import { CalloutAside } from "../_components/CalloutAside"

/**
 * D78: how additionalContext works in CC, which 26 events accept it,
 * why 4 are excluded (D59).
 */
export const dynamic = "force-static"

/** D59: the four events that do not accept additionalContext. */
const EXCLUDED_EVENTS = [
  "SessionStart",
  "SessionEnd",
  "Stop",
  "SubagentStop",
]

export default function InjectContextPage() {
  const isKo = getLocale() === "ko"

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
            magi-cp 는 정책 IR 에서 템플릿 한 줄을 받아 그대로 채워 보냅니다.
          </p>
          <CodeBlock>{`{
  "kind": "inject_context",
  "template_id": "legal-citation-required",
  "data": {
    "subject": "{{ tool_input.subject }}"
  }
}`}</CodeBlock>

          <h2>지원 이벤트 (26개)</h2>
          <p>
            Claude Code 의 30개 훅 이벤트 중 26개가 <Code inline>additionalContext</Code> 를
            받습니다. 네 개는 빠져 있는데, 다음 LLM 호출이 없거나 (SessionEnd / Stop /
            SubagentStop) 이미 종료 직전이라 끼울 자리가 없기 때문입니다.
            <Code inline>SessionStart</Code> 도 마찬가지로 첫 LLM 호출 전이라 끼울 자리가 없습니다.
          </p>
          <CalloutAside tone="warn" title="제외된 4개 이벤트">
            <ul className="list-disc pl-5 m-0">
              {EXCLUDED_EVENTS.map((e) => <li key={e}><Code inline>{e}</Code></li>)}
            </ul>
            <p className="mt-2 m-0">
              이 네 이벤트에서는 wizard 가 inject_context 액션을 비활성화합니다.
            </p>
          </CalloutAside>

          <h2>템플릿 작성</h2>
          <p>
            템플릿은 <Code inline>MAGI_CP_CONTEXT_TEMPLATES_DIR</Code> 에 둡니다.
            파일 이름이 곧 <Code inline>template_id</Code> 입니다. 본문은 Jinja2 스타일 변수
            <Code inline> {`{{ ... }}`}</Code> 를 지원합니다.
          </p>

          <h2>예시</h2>
          <CodeBlock>{`# templates/legal-citation-required.md
이 파일을 수정하려면 한국 형사소송법 제 N 조 인용이 필요합니다.
subject: {{ data.subject }}
`}</CodeBlock>

          <h2>관련 정책 종류</h2>
          <ul>
            <li>verifier 가 <Code inline>not_applicable</Code> 일 때만 inject 하는 안전한 조합</li>
            <li>여러 정책이 같은 훅에 붙으면 inject 된 문자열은 줄바꿈으로 합쳐집니다</li>
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
            Claude Code prepends that string to the next LLM call's system prompt. magi-cp picks
            up a template from the policy IR and fills it.
          </p>
          <CodeBlock>{`{
  "kind": "inject_context",
  "template_id": "legal-citation-required",
  "data": {
    "subject": "{{ tool_input.subject }}"
  }
}`}</CodeBlock>

          <h2>Supported events (26)</h2>
          <p>
            Of Claude Code's 30 hook events, 26 accept <Code inline>additionalContext</Code>.
            Four don't, because there is no next LLM call (Stop / SubagentStop / SessionEnd) or
            because we're before the first LLM call (SessionStart).
          </p>
          <CalloutAside tone="warn" title="The 4 excluded events">
            <ul className="list-disc pl-5 m-0">
              {EXCLUDED_EVENTS.map((e) => <li key={e}><Code inline>{e}</Code></li>)}
            </ul>
            <p className="mt-2 m-0">
              The wizard hides the inject_context action when one of these is selected.
            </p>
          </CalloutAside>

          <h2>Writing templates</h2>
          <p>
            Templates live in <Code inline>MAGI_CP_CONTEXT_TEMPLATES_DIR</Code>. The filename is
            the <Code inline>template_id</Code>. Bodies support Jinja2-style
            <Code inline> {`{{ ... }}`}</Code> variables.
          </p>

          <h2>Example</h2>
          <CodeBlock>{`# templates/legal-citation-required.md
Editing this file requires a citation to KCPC Article N.
subject: {{ data.subject }}
`}</CodeBlock>

          <h2>Composition</h2>
          <ul>
            <li>Inject only on <Code inline>not_applicable</Code> for a low-friction nudge.</li>
            <li>Multiple injecting policies on the same hook are joined with newlines.</li>
          </ul>
        </>
      )}
    </DocsLayout>
  )
}
