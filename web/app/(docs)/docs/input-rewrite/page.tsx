import Link from "next/link"
import { getLocale } from "@/lib/i18n/server"
import { Code, CodeBlock } from "@/components/ui"
import { DocsLayout } from "../_components/DocsLayout"
import { CalloutAside } from "../_components/CalloutAside"
import { REWRITER_KINDS } from "@/lib/runtime-manifest"

/**
 * D78: how PreToolUse updatedInput works + the rewriter DSL.
 *
 * Review fix: the IR examples now use the real
 * `InputRewritePolicy` shape (`type`, `trigger`, `rewriter`) and the
 * rewriter kind names come from `runtime-manifest.REWRITER_KINDS`,
 * which is pinned to `src/magi_cp/policy/rewriters.py` by the vitest
 * gate.
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

          <h2>IR 모양</h2>
          <p>
            wrapper 는 <Code inline>type</Code>, <Code inline>id</Code>, <Code inline>description</Code>,
            <Code inline> trigger</Code>, <Code inline>rewriter</Code> 다섯 필드를 가진 한 덩어리입니다.
            <Code inline> rewriter.kind</Code> 가 연산자 이름이고, 대상 필드 이름은
            <Code inline> rewriter.config.field</Code> 한 칸에 들어갑니다
            (<Code inline>tool_input.url</Code> 같은 점-경로는 허용되지 않습니다).
            지원 연산자: {REWRITER_KINDS.map((k, i) => (
              <span key={k}>
                {i > 0 ? ", " : ""}
                <Code inline>{k}</Code>
              </span>
            ))}.
          </p>

          <h3>prefix_strip</h3>
          <p>대상 필드가 특정 접두사로 시작하면 제거합니다.</p>
          <CodeBlock>{`{
  "type": "input_rewrite",
  "id": "strip-file-scheme",
  "description": "WebFetch 의 file:// 접두를 제거합니다",
  "trigger": {
    "host": "claude-code",
    "event": "PreToolUse",
    "matcher": "WebFetch"
  },
  "rewriter": {
    "kind": "prefix_strip",
    "config": {
      "field": "url",
      "prefix": "file://",
      "strip_repeat": false
    }
  }
}`}</CodeBlock>

          <h3>scheme_force</h3>
          <p>URL 의 scheme 을 강제로 바꿉니다 (검열 우회 가드).</p>
          <CodeBlock>{`{
  "type": "input_rewrite",
  "id": "force-https",
  "description": "WebFetch 호출의 http:// 를 https:// 로 강제",
  "trigger": {
    "host": "claude-code",
    "event": "PreToolUse",
    "matcher": "WebFetch"
  },
  "rewriter": {
    "kind": "scheme_force",
    "config": {
      "field": "url",
      "from": "http://",
      "to": "https://"
    }
  }
}`}</CodeBlock>

          <h3>regex_substitute</h3>
          <p>정규식 캡처 → 치환. 가장 자유롭지만 가장 위험합니다.</p>
          <CodeBlock>{`{
  "type": "input_rewrite",
  "id": "soften-rm-rf",
  "description": "rm -rf 를 대화형 rm -ri 로 치환",
  "trigger": {
    "host": "claude-code",
    "event": "PreToolUse",
    "matcher": "Bash"
  },
  "rewriter": {
    "kind": "regex_substitute",
    "config": {
      "field": "command",
      "pattern": "^rm -rf (?P<path>.+)$",
      "replacement": "rm -ri \\\\g<path>",
      "count": 1
    }
  }
}`}</CodeBlock>

          <CalloutAside tone="warn">
            정규식 치환은 의도하지 않은 인자를 망칠 수 있습니다. 시뮬레이터
            (<Link href="/docs/first-policy">/docs/first-policy</Link>) 로 미리 시험하세요.
            <Code inline>config.field</Code> 는 한 칸짜리 식별자만 받습니다.
            점이 든 이름은 <Code inline>validate_rewriter_spec</Code> 가 거부합니다.
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

          <h2>IR shape</h2>
          <p>
            The wrapper has five fields: <Code inline>type</Code>, <Code inline>id</Code>,
            <Code inline> description</Code>, <Code inline>trigger</Code>, <Code inline>rewriter</Code>.
            The operator name lives in <Code inline>rewriter.kind</Code> and the target field name
            in <Code inline>rewriter.config.field</Code> (a single identifier, no dotted paths
            like <Code inline>tool_input.url</Code>). Supported operators:
            {" "}{REWRITER_KINDS.map((k, i) => (
              <span key={k}>
                {i > 0 ? ", " : ""}
                <Code inline>{k}</Code>
              </span>
            ))}.
          </p>

          <h3>prefix_strip</h3>
          <p>Drop a prefix from the target field if it matches.</p>
          <CodeBlock>{`{
  "type": "input_rewrite",
  "id": "strip-file-scheme",
  "description": "Drop file:// from WebFetch URLs",
  "trigger": {
    "host": "claude-code",
    "event": "PreToolUse",
    "matcher": "WebFetch"
  },
  "rewriter": {
    "kind": "prefix_strip",
    "config": {
      "field": "url",
      "prefix": "file://",
      "strip_repeat": false
    }
  }
}`}</CodeBlock>

          <h3>scheme_force</h3>
          <p>Force the URL scheme. Useful as an HTTPS guard.</p>
          <CodeBlock>{`{
  "type": "input_rewrite",
  "id": "force-https",
  "description": "Force WebFetch URLs to https://",
  "trigger": {
    "host": "claude-code",
    "event": "PreToolUse",
    "matcher": "WebFetch"
  },
  "rewriter": {
    "kind": "scheme_force",
    "config": {
      "field": "url",
      "from": "http://",
      "to": "https://"
    }
  }
}`}</CodeBlock>

          <h3>regex_substitute</h3>
          <p>Capture + substitute. Maximum freedom; maximum risk.</p>
          <CodeBlock>{`{
  "type": "input_rewrite",
  "id": "soften-rm-rf",
  "description": "Rewrite rm -rf to interactive rm -ri",
  "trigger": {
    "host": "claude-code",
    "event": "PreToolUse",
    "matcher": "Bash"
  },
  "rewriter": {
    "kind": "regex_substitute",
    "config": {
      "field": "command",
      "pattern": "^rm -rf (?P<path>.+)$",
      "replacement": "rm -ri \\\\g<path>",
      "count": 1
    }
  }
}`}</CodeBlock>

          <CalloutAside tone="warn">
            Regex substitution can mangle args you didn't intend to touch. Confirm with the
            simulator (<Link href="/docs/first-policy">/docs/first-policy</Link>).
            <Code inline>config.field</Code> only accepts single identifiers;
            <Code inline> validate_rewriter_spec</Code> rejects dotted paths.
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
