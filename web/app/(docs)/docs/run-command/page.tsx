import Link from "next/link"
import { getLocale } from "@/lib/i18n/server"
import { Code, CodeBlock } from "@/components/ui"
import { DocsLayout } from "../_components/DocsLayout"
import { CalloutAside } from "../_components/CalloutAside"
import {
  RUN_COMMAND_RUNTIMES,
  RUN_COMMAND_TIMEOUT_DEFAULT_MS,
  RUN_COMMAND_TIMEOUT_MAX_MS,
  SCRIPT_ID_EXAMPLE,
} from "@/lib/runtime-manifest"

/**
 * D78: how to author a policy that runs a shell command or an
 * attached script when a CC hook fires.
 *
 * Review fix: runtime list, timeout default/max, and the script id
 * shape are imported from `runtime-manifest`, gated against
 * `src/magi_cp/policy/ir.py` (_RUN_COMMAND_RUNTIMES,
 * _DEFAULT_RUN_COMMAND_TIMEOUT_MS, _MAX_RUN_COMMAND_TIMEOUT_MS,
 * _SCRIPT_ID_RE). The IR field is `timeout_ms`, not `timeout_seconds`;
 * a sibling `timeout_seconds` would be silently ignored.
 */
export const dynamic = "force-static"

export default function RunCommandPage() {
  const isKo = getLocale() === "ko"
  const def = RUN_COMMAND_TIMEOUT_DEFAULT_MS
  const max = RUN_COMMAND_TIMEOUT_MAX_MS
  const runtimesText = RUN_COMMAND_RUNTIMES.join(" / ")

  return (
    <DocsLayout
      current="run-command"
      title={isKo ? "훅에서 명령 실행하기" : "Run a script from a hook"}
      subtitle={isKo
        ? "차단만 말고 실제로 뭔가 시키고 싶을 때. shell command 도, 업로드한 스크립트도 됩니다."
        : "When you need to do something, not just block. Run a shell command, or an uploaded script."
      }
    >
      {isKo ? (
        <>
          <h2>언제 쓰나</h2>
          <ul>
            <li>도구 호출 직후 결과를 외부 시스템에 알릴 때 (Slack ping, 등)</li>
            <li>차단 대신 lint·formatter 를 돌려 자동 수정할 때</li>
            <li>특정 매처가 떴을 때 정책 외의 부수효과를 일으킬 때</li>
          </ul>

          <h2>두 가지 모드</h2>
          <h3>1. inline command</h3>
          <p>
            Policy IR 에 <Code inline>action.kind = "run_command"</Code> 와
            <Code inline> action.command</Code> 를 적으면 셸이 그대로 실행합니다.
            짧고 의존성 없는 명령에 적합합니다.
          </p>
          <CodeBlock>{`{
  "kind": "run_command",
  "command": "echo \\"hook fired\\" | logger -t magi-cp",
  "runtime": "bash",
  "timeout_ms": ${def},
  "fail_closed": false
}`}</CodeBlock>

          <h3>2. 첨부 스크립트</h3>
          <p>
            긴 스크립트는 <Link href="/scripts">/scripts</Link> 에서 업로드합니다.
            업로드하면 cloud 가 본문의 sha256 을 계산해 64-hex 의 script id 를 발급합니다.
            정책의 <Code inline>script_path</Code> 에는 그 id 가 들어갑니다.
            파일 이름이 아닙니다.
          </p>
          <CodeBlock>{`{
  "kind": "run_command",
  "script_path": "${SCRIPT_ID_EXAMPLE}",
  "runtime": "python3",
  "timeout_ms": ${max},
  "fail_closed": true
}`}</CodeBlock>

          <h2>업로드 흐름</h2>
          <ol>
            <li><Link href="/scripts">/scripts</Link> → "Upload script" 클릭</li>
            <li>파일을 선택, runtime 선택 ({runtimesText}), 설명 입력</li>
            <li>업로드 후 보이는 64-hex <b>script id</b> 가 정책 IR 의 <Code inline>script_path</Code> 값입니다.</li>
          </ol>

          <h2>runtime</h2>
          <p>
            허용 runtime 은 {RUN_COMMAND_RUNTIMES.map((r, i) => (
              <span key={r}>
                {i > 0 ? ", " : ""}
                <Code inline>{r}</Code>
              </span>
            ))}{" "}세 가지입니다. 컨테이너 안에 없는 runtime 을 선택하면 정책이 invalid 로 거부됩니다.
            <Code inline> sh</Code> 는 허용되지 않습니다.
          </p>

          <h2>timeout</h2>
          <p>
            IR 필드는 <Code inline>timeout_ms</Code> 입니다 (밀리초). 기본 {def}ms,
            최대 {max}ms. <Code inline>timeout_seconds</Code> 는 인식되지 않고 무시되니
            반드시 <Code inline>timeout_ms</Code> 를 쓰세요. 초과하면 프로세스가 SIGKILL 됩니다.
            긴 작업은 fire-and-forget 으로 백그라운드에 두세요 (<Code inline>nohup … &</Code>).
          </p>

          <h2>fail_closed</h2>
          <p>
            <Code inline>fail_closed: true</Code> 면 명령이 0 이외 코드로 죽었을 때 정책 verdict 도
            <Code inline> fail</Code> 로 바뀝니다. 차단 정책과 같이 쓸 때 유용합니다.
            <Code inline> false</Code> 면 명령 실패는 ledger 에만 남고 verdict 에 영향을 안 줍니다.
          </p>

          <CalloutAside tone="warn" title="안전장치">
            셀프 호스트에서 <Code inline>run_command</Code> 자체가 꺼져 있을 수 있습니다.
            <Code inline> MAGI_CP_ALLOW_RUN_COMMAND=1</Code> 로 전역 허용한 뒤 사용하세요.
            호스티드에서는 서명된 spec 만 받습니다 (<Code inline>MAGI_CP_REQUIRE_SIGNED_RUN_COMMAND_SPEC</Code>).
          </CalloutAside>

          <h2>관련 환경변수</h2>
          <ul>
            <li><Code inline>MAGI_CP_ALLOW_RUN_COMMAND</Code></li>
            <li><Code inline>MAGI_CP_REQUIRE_SIGNED_RUN_COMMAND_SPEC</Code></li>
            <li><Code inline>MAGI_CP_SCRIPT_STORE_DIR</Code></li>
            <li><Code inline>MAGI_CP_RUN_COMMAND_LEDGER</Code></li>
          </ul>
          <p>전체 목록은 <Link href="/docs/env-reference">환경변수 레퍼런스</Link>.</p>
        </>
      ) : (
        <>
          <h2>When to use this</h2>
          <ul>
            <li>Notify an external system after a tool call (Slack ping, etc).</li>
            <li>Run a lint/formatter instead of blocking.</li>
            <li>Trigger a side-effect when a matcher fires.</li>
          </ul>

          <h2>Two modes</h2>
          <h3>1. Inline command</h3>
          <p>
            Put <Code inline>action.kind = "run_command"</Code> and
            <Code inline> action.command</Code> in the policy IR. Fine for short
            dependency-free commands.
          </p>
          <CodeBlock>{`{
  "kind": "run_command",
  "command": "echo \\"hook fired\\" | logger -t magi-cp",
  "runtime": "bash",
  "timeout_ms": ${def},
  "fail_closed": false
}`}</CodeBlock>

          <h3>2. Attached script</h3>
          <p>
            For longer scripts, upload via <Link href="/scripts">/scripts</Link>. The cloud
            hashes the body and issues a 64-hex script id; the policy's
            <Code inline> script_path</Code> field holds that id, not the local filename.
          </p>
          <CodeBlock>{`{
  "kind": "run_command",
  "script_path": "${SCRIPT_ID_EXAMPLE}",
  "runtime": "python3",
  "timeout_ms": ${max},
  "fail_closed": true
}`}</CodeBlock>

          <h2>Upload flow</h2>
          <ol>
            <li>Open <Link href="/scripts">/scripts</Link> and click "Upload script".</li>
            <li>Pick the file, choose runtime ({runtimesText}), add a description.</li>
            <li>The 64-hex <b>script id</b> shown after upload is what goes into the policy IR's <Code inline>script_path</Code>.</li>
          </ol>

          <h2>Runtime</h2>
          <p>
            Allowed runtimes: {RUN_COMMAND_RUNTIMES.map((r, i) => (
              <span key={r}>
                {i > 0 ? ", " : ""}
                <Code inline>{r}</Code>
              </span>
            ))}. Choosing a runtime that isn't present in the container makes the policy
            invalid. <Code inline>sh</Code> is not allowed.
          </p>

          <h2>Timeout</h2>
          <p>
            The IR field is <Code inline>timeout_ms</Code> (milliseconds). Default {def}ms,
            max {max}ms. A stray <Code inline>timeout_seconds</Code> is unrecognized and silently
            ignored, so be sure to spell the IR key <Code inline>timeout_ms</Code>. The process
            is SIGKILLed on overflow. For long jobs, fire-and-forget into the background
            (<Code inline>nohup … &</Code>).
          </p>

          <h2>fail_closed</h2>
          <p>
            With <Code inline>fail_closed: true</Code>, a non-zero exit flips the policy verdict
            to <Code inline>fail</Code>. Useful when pairing with block actions. With
            <Code inline> false</Code>, command failures land in the ledger but do not affect
            the verdict.
          </p>

          <CalloutAside tone="warn" title="Safety">
            On self-host, run_command may be globally disabled. Set
            <Code inline> MAGI_CP_ALLOW_RUN_COMMAND=1</Code> to allow. On hosted, only signed specs
            are accepted (<Code inline>MAGI_CP_REQUIRE_SIGNED_RUN_COMMAND_SPEC</Code>).
          </CalloutAside>

          <h2>Related env vars</h2>
          <ul>
            <li><Code inline>MAGI_CP_ALLOW_RUN_COMMAND</Code></li>
            <li><Code inline>MAGI_CP_REQUIRE_SIGNED_RUN_COMMAND_SPEC</Code></li>
            <li><Code inline>MAGI_CP_SCRIPT_STORE_DIR</Code></li>
            <li><Code inline>MAGI_CP_RUN_COMMAND_LEDGER</Code></li>
          </ul>
          <p>Full list: <Link href="/docs/env-reference">Env reference</Link>.</p>
        </>
      )}
    </DocsLayout>
  )
}
