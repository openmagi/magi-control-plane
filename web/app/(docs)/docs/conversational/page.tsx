import Link from "next/link"
import { getLocale } from "@/lib/i18n/server"
import { Code, CodeBlock } from "@/components/ui"
import { DocsLayout } from "../_components/DocsLayout"
import { CalloutAside } from "../_components/CalloutAside"

/**
 * D78: conversational compiler usage guide.
 */
export const dynamic = "force-static"

export default function ConversationalPage() {
  const isKo = getLocale() === "ko"

  return (
    <DocsLayout
      current="conversational"
      title={isKo ? "대화형 작성기 사용법" : "Using the conversational author"}
      subtitle={isKo
        ? "자연어로 정책을 묘사하면 IR 로 변환해 줍니다. 어떤 식으로 표현해야 잘 맞는지."
        : "Describe the policy in natural language and the compiler returns Policy IR."
      }
    >
      {isKo ? (
        <>
          <h2>들어가기</h2>
          <p>
            <Link href="/policies/new">/policies/new</Link> 에서 <b>"Conversational"</b> 탭을
            엽니다. <Code inline>MAGI_CP_LLM_COMPILER</Code> 환경변수가 설정돼 있어야 보입니다
            (그렇지 않다면 페이지 위쪽에 actionable 배너가 뜹니다).
          </p>

          <h2>잘 맞는 표현</h2>
          <p>다음 패턴이 가장 정확하게 컴파일됩니다.</p>
          <ul>
            <li><b>훅 이벤트 명시</b>: "PreToolUse 에서…"</li>
            <li><b>도구 이름 명시</b>: "Bash 가 호출되면…"</li>
            <li><b>매처 명확</b>: "command 가 rm -rf 로 시작하면…"</li>
            <li><b>액션 명확</b>: "차단해라 / 경고만 / 컨텍스트를 끼워라 / 스크립트 X 를 실행해라"</li>
          </ul>

          <h3>좋은 예</h3>
          <CodeBlock>{`PreToolUse(Bash) 에서 command 가 rm -rf 로 시작하면 차단합니다.`}</CodeBlock>
          <CodeBlock>{`PostToolUse(Read) 가 .env 파일을 읽었으면 컨텍스트로
"민감한 파일을 봤습니다. 사용자에게 알릴 것" 을 끼워 넣어주세요.`}</CodeBlock>

          <h3>피해야 할 표현</h3>
          <ul>
            <li>“보안 룰 만들어 줘” 같은 너무 추상적인 요청 (어떤 훅·매처·액션인지 불명)</li>
            <li>“가끔 차단해라” 같은 비결정론적 표현</li>
            <li>여러 정책을 한 번에 묶어 요청 (한 번에 한 정책이 정확합니다)</li>
          </ul>

          <h2>리뷰 단계</h2>
          <p>
            컴파일러가 만든 IR 은 <Code inline>MAGI_CP_LLM_REVIEWER</Code> 가 한 번 더 검수합니다.
            리뷰어가 위험 신호 (지나친 권한, 비결정론적 verifier 사용) 를 잡아내면 IR 이 거부됩니다.
          </p>

          <h2>wizard 핸드오프</h2>
          <p>
            IR 이 길어지거나 세부 필드를 손보고 싶으면 결과 화면 오른쪽의
            <b> "Open in wizard"</b> 를 누르세요. constrained form 으로 넘어가서 한 칸씩 다듬을 수 있습니다.
          </p>

          <CalloutAside tone="tip">
            처음이면 <Link href="/docs/first-policy">prebuilt 한 개</Link> 부터 켜고,
            거기서 IR 모양을 익힌 뒤 conversational 로 자기 정책을 만드는 게 빠릅니다.
          </CalloutAside>

          <h2>관련</h2>
          <ul>
            <li><Link href="/docs/troubleshooting">"LLM not configured" 배너 해결</Link></li>
            <li><Link href="/docs/env-reference">MAGI_CP_LLM_COMPILER / MAGI_CP_LLM_REVIEWER</Link></li>
          </ul>
        </>
      ) : (
        <>
          <h2>Getting in</h2>
          <p>
            Open <Link href="/policies/new">/policies/new</Link> and pick the
            <b> Conversational</b> tab. It only appears when <Code inline>MAGI_CP_LLM_COMPILER</Code>
            is configured; otherwise an actionable banner shows up at the top.
          </p>

          <h2>Phrasings that work well</h2>
          <ul>
            <li><b>State the hook</b>: "at PreToolUse, ..."</li>
            <li><b>Name the tool</b>: "when Bash is invoked, ..."</li>
            <li><b>Be explicit on the matcher</b>: "when the command starts with rm -rf, ..."</li>
            <li><b>Be explicit on the action</b>: "block / warn / inject context / run script X"</li>
          </ul>

          <h3>Good</h3>
          <CodeBlock>{`At PreToolUse(Bash), block when the command starts with rm -rf.`}</CodeBlock>
          <CodeBlock>{`At PostToolUse(Read), if a .env file was read, inject context
"this read a sensitive file; warn the user".`}</CodeBlock>

          <h3>Avoid</h3>
          <ul>
            <li>"Make me a security rule" (no hook, matcher, or action).</li>
            <li>"Block sometimes" (non-deterministic).</li>
            <li>Bundling multiple policies into one request (one at a time is sharper).</li>
          </ul>

          <h2>The review step</h2>
          <p>
            The IR the compiler produces is then double-checked by
            <Code inline> MAGI_CP_LLM_REVIEWER</Code>. The reviewer rejects IR that looks risky
            (over-broad permissions, non-deterministic verifier choice, ...).
          </p>

          <h2>Handoff to the wizard</h2>
          <p>
            If the IR is long or you want to tune individual fields, click
            <b> "Open in wizard"</b> on the result panel. You drop into the constrained form
            with the conversational draft pre-filled.
          </p>

          <CalloutAside tone="tip">
            New here? Enable a <Link href="/docs/first-policy">prebuilt</Link> first, get a feel
            for IR shape, then come back and write your own conversationally.
          </CalloutAside>

          <h2>Related</h2>
          <ul>
            <li><Link href="/docs/troubleshooting">"LLM not configured" banner</Link></li>
            <li><Link href="/docs/env-reference">MAGI_CP_LLM_COMPILER / MAGI_CP_LLM_REVIEWER</Link></li>
          </ul>
        </>
      )}
    </DocsLayout>
  )
}
