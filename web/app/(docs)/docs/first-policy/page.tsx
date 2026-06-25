import Link from "next/link"
import { getLocale } from "@/lib/i18n/server"
import { Code, CodeBlock } from "@/components/ui"
import { DocsLayout } from "../_components/DocsLayout"
import { CalloutAside } from "../_components/CalloutAside"

/**
 * D78: walkthrough of enabling a prebuilt and confirming it with the
 * D77 hook payload simulator. Plays as a numbered list; screenshot
 * placeholders are inline SVG (no external image deps).
 */
export const dynamic = "force-static"

function StepDiagram({ label }: { label: string }) {
  return (
    <div className="my-4 rounded-lg border border-dashed border-[var(--color-border-subtle)] bg-white/40 p-4">
      <svg viewBox="0 0 600 80" className="w-full" role="img" aria-label={label}>
        <rect x="8" y="20" width="120" height="40" rx="6" className="fill-[color:var(--color-accent-light)]" opacity="0.18"/>
        <rect x="160" y="20" width="160" height="40" rx="6" className="fill-emerald-500" opacity="0.18"/>
        <rect x="352" y="20" width="240" height="40" rx="6" className="fill-violet-500" opacity="0.18"/>
        <text x="68" y="45" textAnchor="middle" fontSize="12" className="fill-[color:var(--color-text-primary)]">/rules</text>
        <text x="240" y="45" textAnchor="middle" fontSize="12" className="fill-[color:var(--color-text-primary)]">enable prebuilt</text>
        <text x="472" y="45" textAnchor="middle" fontSize="12" className="fill-[color:var(--color-text-primary)]">test with simulator</text>
        <path d="M128 40 L160 40" stroke="currentColor" className="stroke-[color:var(--color-text-tertiary)]" strokeWidth="1.5" markerEnd="url(#arr)"/>
        <path d="M320 40 L352 40" stroke="currentColor" className="stroke-[color:var(--color-text-tertiary)]" strokeWidth="1.5" markerEnd="url(#arr)"/>
        <defs>
          <marker id="arr" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0 0 L10 5 L0 10 z" className="fill-[color:var(--color-text-tertiary)]"/>
          </marker>
        </defs>
      </svg>
    </div>
  )
}

export default function FirstPolicyPage() {
  const isKo = getLocale() === "ko"

  return (
    <DocsLayout
      current="first-policy"
      title={isKo ? "첫 정책 켜기" : "Enable your first policy"}
      subtitle={isKo
        ? "prebuilt 하나를 토글한 뒤 시뮬레이터로 정말 작동하는지 확인합니다."
        : "Toggle a prebuilt and confirm with the synthetic payload simulator."
      }
    >
      <StepDiagram label={isKo ? "정책 켜기 흐름" : "Enable policy flow"} />

      {isKo ? (
        <>
          <h2>1. /rules 로 가서 prebuilt 를 찾는다</h2>
          <p>
            왼쪽 사이드바의 <Link href="/rules">룰</Link> 항목을 누릅니다.
            카테고리별 prebuilt 목록이 나옵니다. 초보용으로는
            <b> filesystem/no-rm-rf </b> 가 가장 안전합니다 ("Bash 에서 rm -rf 차단").
          </p>

          <h2>2. 토글을 켠다</h2>
          <p>
            각 행 오른쪽의 토글을 누르면 즉시 활성화됩니다. 클라우드에 IR 이 저장되고,
            플러그인은 다음 훅이 발사될 때 클라우드에서 정책을 다시 끌어옵니다
            (별도 폴링 데몬은 없습니다). 활성화 직후 강제하고 싶다면 그냥 한 번
            새 메시지를 보내거나 <Code inline>claude</Code> 세션을 재시작하면 됩니다.
          </p>
          <CalloutAside tone="tip">
            캐시된 정책 사본이 의심스러우면 <Code inline>MAGI_CP_LOCAL_DIR</Code>
            (기본 <Code inline>~/.config/magi-cp</Code>) 아래의 정책 캐시 파일을 지우고 다시
            <Code inline> claude</Code> 를 띄우세요. 다음 훅에서 최신 IR 이 내려옵니다.
          </CalloutAside>

          <h2>3. 시뮬레이터로 발사 테스트</h2>
          <p>
            정책 행을 펼치면 <b>"이 정책 테스트"</b> 버튼이 있습니다. 가상의 hook payload
            (D77 simulator) 가 정책을 실제 evaluator 로 통과시키고 verdict 를 보여줍니다.
          </p>
          <p>예시 payload (정책이 차단해야 합니다):</p>
          <CodeBlock>{`{
  "hook_event": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": { "command": "rm -rf /tmp/important" }
}`}</CodeBlock>

          <h2>4. 결과 읽기</h2>
          <p>
            verdict <Code inline>fail</Code> 옆에 액션 <Code inline>block</Code> 이 보이면 OK 입니다.
            <Code inline>not_applicable</Code> 이 뜨면 매처가 안 맞은 경우니까 prebuilt 의 매처 설정을 확인하세요.
          </p>

          <h2>5. 실제 Claude Code 에서 확인</h2>
          <p>
            CC 셸에서 <Code inline>claude</Code> 로 들어가 <Code inline>rm -rf /tmp/foo</Code> 를
            시키면 차단 메시지가 떠야 합니다. 안 뜨면 <Link href="/docs/troubleshooting">문제 해결</Link>
             의 "훅이 발사되지 않음" 절을 보세요.
          </p>

          <h2>다음 단계</h2>
          <ul>
            <li><Link href="/docs/run-command">스크립트 실행</Link>: 차단만 말고 실제 동작 시키기</li>
            <li><Link href="/docs/inject-context">컨텍스트 주입</Link>: LLM 에 보강 정보 주기</li>
            <li><Link href="/docs/conversational">대화형 작성기</Link>: 자기 정책 만들기</li>
          </ul>
        </>
      ) : (
        <>
          <h2>1. Open /rules and pick a prebuilt</h2>
          <p>
            Click <Link href="/rules">Rules</Link> in the sidebar. You will see prebuilts grouped
            by category. The safest beginner pick is <b>filesystem/no-rm-rf</b>
            ("block Bash rm -rf").
          </p>

          <h2>2. Flip the toggle</h2>
          <p>
            The toggle on the right of each row enables the policy. The IR is saved to the
            cloud; the plugin fetches policies inline on the next hook invocation (there is no
            background poll loop). To force-pick-up, send a fresh message or restart your
            <Code inline> claude</Code> session.
          </p>
          <CalloutAside tone="tip">
            If you suspect a stale cached copy, delete the policy cache files under
            <Code inline> MAGI_CP_LOCAL_DIR</Code> (default
            <Code inline> ~/.config/magi-cp</Code>) and re-launch <Code inline>claude</Code>.
            The next hook downloads the current IR.
          </CalloutAside>

          <h2>3. Test it with the simulator</h2>
          <p>
            Expand the policy row and click <b>"Test this policy"</b>. The D77 simulator
            sends a synthetic hook payload through the real evaluator and shows you the verdict.
          </p>
          <p>Sample payload (the policy should block this):</p>
          <CodeBlock>{`{
  "hook_event": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": { "command": "rm -rf /tmp/important" }
}`}</CodeBlock>

          <h2>4. Read the result</h2>
          <p>
            A verdict of <Code inline>fail</Code> alongside action <Code inline>block</Code> means
            success. <Code inline>not_applicable</Code> means the matcher did not match, so check
            the prebuilt's matcher configuration.
          </p>

          <h2>5. Confirm against real Claude Code</h2>
          <p>
            From your CC shell, run <Code inline>claude</Code> and try
            <Code inline>rm -rf /tmp/foo</Code>. You should see a block message.
            If not, see the "hook is not firing" section in
            <Link href="/docs/troubleshooting"> Troubleshooting</Link>.
          </p>

          <h2>Next</h2>
          <ul>
            <li><Link href="/docs/run-command">Run a script</Link>: do something, not just block.</li>
            <li><Link href="/docs/inject-context">Inject context</Link>: feed extra info to the LLM.</li>
            <li><Link href="/docs/conversational">Conversational</Link>: write your own policy.</li>
          </ul>
        </>
      )}
    </DocsLayout>
  )
}
