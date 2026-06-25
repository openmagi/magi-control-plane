import Link from "next/link"
import { getLocale } from "@/lib/i18n/server"
import { Code, CodeBlock } from "@/components/ui"
import { DocsLayout } from "../_components/DocsLayout"
import { CalloutAside } from "../_components/CalloutAside"

/**
 * D78: common errors + fixes. Pairs each symptom with the exact fix
 * and a pointer to the underlying env var or page.
 */
export const dynamic = "force-static"

export default function TroubleshootingPage() {
  const isKo = getLocale() === "ko"

  return (
    <DocsLayout
      current="troubleshooting"
      title={isKo ? "문제 해결" : "Troubleshooting"}
      subtitle={isKo
        ? "처음 1주 동안 마주칠 가능성이 높은 다섯 가지 막힘과 해결책."
        : "The five blockers a new operator hits in the first week, with the fix."
      }
    >
      {isKo ? (
        <>
          <h2>1. LLM not configured (대화형 작성기에서 배너)</h2>
          <p>증상: <Link href="/policies/new">/policies/new</Link> Conversational 탭이 회색이고 actionable 배너가 뜸.</p>
          <p>원인: <Code inline>MAGI_CP_LLM_COMPILER</Code> 와 <Code inline>MAGI_CP_LLM_REVIEWER</Code> 중 하나라도 비어 있음.</p>
          <CodeBlock>{`export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
export MAGI_CP_LLM_COMPILER=magi_cp.llm.anthropic_provider:anthropic_default
export MAGI_CP_LLM_REVIEWER=magi_cp.llm.openai_provider:openai_default
docker compose restart cloud`}</CodeBlock>

          <h2>2. Cloud unreachable (콘솔 상단 배너)</h2>
          <p>증상: 상단에 "Cloud unreachable", workspace 카드가 회색.</p>
          <p>점검 순서:</p>
          <ol>
            <li><Code inline>curl http://127.0.0.1:8787/healthz</Code> 가 200 인지 확인.</li>
            <li>대시보드 환경변수 <Code inline>MAGI_CP_CLOUD_URL</Code> 이 그 URL 을 가리키는지.</li>
            <li>도커 네트워크 격리: 같은 compose 네트워크에 있는지 (<Code inline>docker compose ps</Code>).</li>
          </ol>

          <h2>3. Provider unauthorized / 503 (정책 컴파일 실패)</h2>
          <p>증상: Conversational 작성기에서 "Provider returned 401/403/503".</p>
          <p>거의 항상 LLM 키 문제입니다.</p>
          <ul>
            <li>키가 만료되었거나 빌링 한도를 초과한 경우</li>
            <li>키가 reviewer 의 모델 (OpenAI gpt-5.5 등) 접근 권한이 없는 경우</li>
            <li>임시 provider outage → 1-2 분 뒤 재시도</li>
          </ul>

          <h2>4. Script timeout in run_command</h2>
          <p>증상: ledger 에 <Code inline>run_command</Code> 가 <Code inline>timeout</Code> 으로 떨어짐.</p>
          <p>해결:</p>
          <ul>
            <li>정책 IR 의 <Code inline>timeout_ms</Code> 를 늘리세요 (밀리초, 최대 30000).
                옛 표기 <Code inline>timeout_seconds</Code> 는 인식되지 않고 무시되니
                반드시 <Code inline>timeout_ms</Code> 키를 쓰세요.</li>
            <li>긴 작업을 백그라운드로 두기 (<Code inline>nohup … &</Code>).</li>
            <li>플러그인 컨테이너에 runtime 이 진짜 있는지 (<Code inline>which python3</Code>).</li>
          </ul>

          <h2>5. Hook not firing in Claude Code</h2>
          <p>증상: 시뮬레이터에서는 차단되는데 실제 <Code inline>claude</Code> 에서는 통과.</p>
          <p>점검:</p>
          <ol>
            <li>로컬 정책 캐시가 stale 한지: <Code inline>~/.config/magi-cp</Code> (또는
                <Code inline>$MAGI_CP_LOCAL_DIR</Code>) 의 캐시 파일을 지우고 <Code inline>claude</Code>
                를 재시작하면 다음 훅에서 최신 IR 이 내려옵니다.</li>
            <li>CC 의 디버그 출력으로 훅이 정말 발사되는지: <Code inline>CLAUDE_HOOK_DEBUG=1 claude …</Code>
                (또는 운영자가 CC 셸 안에서 <Code inline>claude doctor</Code> 로 hook 설정 확인).</li>
            <li>매처가 실제 도구 이름과 일치하는지: 시뮬레이터에 같은 payload 를 넣어봅니다.</li>
            <li>플러그인 로그에 보안상의 deny 가 있는지: <Code inline>tail -F ~/.config/magi-cp/plugin.log</Code></li>
          </ol>

          <CalloutAside tone="tip">
            <Link href="/docs/env-reference">환경변수 레퍼런스</Link> 에서 위에서 언급한 모든
            <Code inline> MAGI_CP_*</Code> 의 기본값과 한 줄 설명을 확인할 수 있습니다.
          </CalloutAside>
        </>
      ) : (
        <>
          <h2>1. LLM not configured (banner in conversational)</h2>
          <p>Symptom: <Link href="/policies/new">/policies/new</Link> Conversational tab is grey with an actionable banner.</p>
          <p>Cause: <Code inline>MAGI_CP_LLM_COMPILER</Code> or <Code inline>MAGI_CP_LLM_REVIEWER</Code> is unset.</p>
          <CodeBlock>{`export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
export MAGI_CP_LLM_COMPILER=magi_cp.llm.anthropic_provider:anthropic_default
export MAGI_CP_LLM_REVIEWER=magi_cp.llm.openai_provider:openai_default
docker compose restart cloud`}</CodeBlock>

          <h2>2. Cloud unreachable (banner at top)</h2>
          <p>Symptom: "Cloud unreachable" at the top of the console; workspace card greys out.</p>
          <p>Check in order:</p>
          <ol>
            <li><Code inline>curl http://127.0.0.1:8787/healthz</Code> returns 200.</li>
            <li>The dashboard's <Code inline>MAGI_CP_CLOUD_URL</Code> points at that URL.</li>
            <li>Docker network: dashboard + cloud on the same compose network (<Code inline>docker compose ps</Code>).</li>
          </ol>

          <h2>3. Provider unauthorized / 503 (compile fails)</h2>
          <p>Symptom: conversational shows "Provider returned 401/403/503".</p>
          <p>Almost always a key problem:</p>
          <ul>
            <li>Key expired or billing limit hit.</li>
            <li>Key lacks access to the reviewer's model (OpenAI gpt-5.5, etc).</li>
            <li>Provider outage. Retry after 1-2 minutes.</li>
          </ul>

          <h2>4. Script timeout in run_command</h2>
          <p>Symptom: ledger entry for <Code inline>run_command</Code> ends in <Code inline>timeout</Code>.</p>
          <p>Fix:</p>
          <ul>
            <li>Raise <Code inline>timeout_ms</Code> in the policy IR (milliseconds, cap 30000).
                The legacy spelling <Code inline>timeout_seconds</Code> is silently ignored;
                make sure the key is <Code inline>timeout_ms</Code>.</li>
            <li>Push long jobs to the background (<Code inline>nohup … &</Code>).</li>
            <li>Confirm the runtime is present in the plugin container (<Code inline>which python3</Code>).</li>
          </ul>

          <h2>5. Hook not firing in Claude Code</h2>
          <p>Symptom: simulator blocks, but real <Code inline>claude</Code> lets it through.</p>
          <p>Checks:</p>
          <ol>
            <li>Local policy cache may be stale: delete files under
                <Code inline> ~/.config/magi-cp</Code> (or <Code inline>$MAGI_CP_LOCAL_DIR</Code>)
                and restart <Code inline>claude</Code>. The next hook downloads the current IR.</li>
            <li>Confirm CC actually emits the hook via its own debug output:
                <Code inline> CLAUDE_HOOK_DEBUG=1 claude …</Code> (or open <Code inline>claude doctor</Code>
                inside the CC shell to inspect hook configuration).</li>
            <li>Matcher matches the real tool name: re-test the simulator with that payload.</li>
            <li>Plugin log: <Code inline>tail -F ~/.config/magi-cp/plugin.log</Code>.</li>
          </ol>

          <CalloutAside tone="tip">
            See <Link href="/docs/env-reference">Env reference</Link> for defaults + one-line
            descriptions of every <Code inline>MAGI_CP_*</Code> mentioned above.
          </CalloutAside>
        </>
      )}
    </DocsLayout>
  )
}
