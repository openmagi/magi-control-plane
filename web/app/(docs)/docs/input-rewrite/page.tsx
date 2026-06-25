import Link from "next/link"
import { getLocale } from "@/lib/i18n/server"
import { Code, CodeBlock } from "@/components/ui"
import { DocsLayout } from "../_components/DocsLayout"
import { CalloutAside } from "../_components/CalloutAside"

/**
 * D78: how PreToolUse updatedInput works, the rewriter DSL.
 */
export const dynamic = "force-static"

export default function InputRewritePage() {
  const isKo = getLocale() === "ko"

  return (
    <DocsLayout
      current="input-rewrite"
      title={isKo ? "입력 재작성 (input_rewrite)" : "Rewriting input (input_rewrite)"}
      subtitle={isKo
        ? "PreToolUse 에서 도구 인자를 안전한 형태로 다듬어 다시 흘려보냅니다."
        : "Patch tool arguments at PreToolUse before they reach the runtime."
      }
    >
      {isKo ? (
        <>
          <h2>왜 쓰나</h2>
          <p>
            매번 차단하면 사용자 경험이 끊깁니다. 대신 인자를 살짝 손봐서 안전한 형태로 통과시키는
            게 더 부드럽습니다. 예: <Code inline>file:///etc/passwd</Code> 를
            <Code inline> https://example.com/etc/passwd</Code> 로 강제 변환.
          </p>

          <h2>지원 위치</h2>
          <p>
            Claude Code 의 <Code inline>PreToolUse</Code> 훅에만 적용됩니다.
            훅이 <Code inline>hookSpecificOutput.updatedInput</Code> 를 돌려주면 그 객체가
            도구의 새로운 인자가 됩니다.
          </p>

          <h2>DSL</h2>
          <p>magi-cp 가 제공하는 재작성 연산자 세 가지:</p>

          <h3>prefix_strip</h3>
          <p>대상 필드가 특정 접두사로 시작하면 제거합니다.</p>
          <CodeBlock>{`{
  "kind": "input_rewrite",
  "target": "tool_input.url",
  "op": "prefix_strip",
  "prefix": "file://"
}`}</CodeBlock>

          <h3>scheme_force</h3>
          <p>URL 의 scheme 을 강제로 바꿉니다 (검열 우회 가드).</p>
          <CodeBlock>{`{
  "kind": "input_rewrite",
  "target": "tool_input.url",
  "op": "scheme_force",
  "from": "http",
  "to": "https"
}`}</CodeBlock>

          <h3>regex_substitute</h3>
          <p>정규식 캡처 → 치환. 가장 자유롭지만 가장 위험합니다.</p>
          <CodeBlock>{`{
  "kind": "input_rewrite",
  "target": "tool_input.command",
  "op": "regex_substitute",
  "pattern": "^rm -rf (?P<path>.+)$",
  "replacement": "rm -ri \\\\g<path>"
}`}</CodeBlock>

          <CalloutAside tone="warn">
            정규식 치환은 의도하지 않은 인자를 망칠 수 있습니다. 시뮬레이터
            (<Link href="/docs/first-policy">/docs/first-policy</Link>) 로 미리 시험하세요.
          </CalloutAside>

          <h2>적용 순서</h2>
          <p>
            같은 PreToolUse 에 여러 재작성 정책이 붙으면 정책 우선순위 → 매칭 순서대로
            연쇄 적용됩니다. 마지막 결과만 도구에 전달됩니다.
          </p>

          <h2>관련 문서</h2>
          <ul>
            <li><Link href="/docs/conversational">대화형 작성기</Link>: "URL 의 file:// 만 제거" 같은 자연어로 시작</li>
            <li><Link href="/docs/inject-context">컨텍스트 주입</Link>: 재작성 대신 LLM 에 알려서 다시 생성시키기</li>
          </ul>
        </>
      ) : (
        <>
          <h2>Why</h2>
          <p>
            Blocking interrupts the user. Patching the argument into a safe form keeps the flow
            going. Example: rewrite <Code inline>file:///etc/passwd</Code> to
            <Code inline> https://example.com/etc/passwd</Code> instead of denying outright.
          </p>

          <h2>Where it applies</h2>
          <p>
            Only at the <Code inline>PreToolUse</Code> hook. Returning
            <Code inline> hookSpecificOutput.updatedInput</Code> from that hook replaces the
            tool's arguments for the call.
          </p>

          <h2>DSL</h2>
          <p>magi-cp ships three rewrite operators:</p>

          <h3>prefix_strip</h3>
          <p>Drop a prefix from the target field if it matches.</p>
          <CodeBlock>{`{
  "kind": "input_rewrite",
  "target": "tool_input.url",
  "op": "prefix_strip",
  "prefix": "file://"
}`}</CodeBlock>

          <h3>scheme_force</h3>
          <p>Force the URL scheme. Useful as an HTTPS guard.</p>
          <CodeBlock>{`{
  "kind": "input_rewrite",
  "target": "tool_input.url",
  "op": "scheme_force",
  "from": "http",
  "to": "https"
}`}</CodeBlock>

          <h3>regex_substitute</h3>
          <p>Capture + substitute. Maximum freedom; maximum risk.</p>
          <CodeBlock>{`{
  "kind": "input_rewrite",
  "target": "tool_input.command",
  "op": "regex_substitute",
  "pattern": "^rm -rf (?P<path>.+)$",
  "replacement": "rm -ri \\\\g<path>"
}`}</CodeBlock>

          <CalloutAside tone="warn">
            Regex substitution can mangle args you didn't intend to touch. Confirm with the
            simulator (<Link href="/docs/first-policy">/docs/first-policy</Link>).
          </CalloutAside>

          <h2>Order of application</h2>
          <p>
            When several rewrites bind to the same PreToolUse, they apply in priority + match
            order, chained. Only the final result reaches the tool.
          </p>

          <h2>Related</h2>
          <ul>
            <li><Link href="/docs/conversational">Conversational</Link>: start with "strip file:// from URLs".</li>
            <li><Link href="/docs/inject-context">Inject context</Link>: nudge the model to regenerate instead.</li>
          </ul>
        </>
      )}
    </DocsLayout>
  )
}
