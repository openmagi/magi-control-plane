import Link from "next/link"
import { Fragment } from "react"
import { getLocale } from "@/lib/i18n/server"
import { Code } from "@/components/ui"
import { DocsLayout } from "../_components/DocsLayout"
import { CalloutAside } from "../_components/CalloutAside"
import { LEDGER_VERDICTS_ORDERED } from "@/lib/runtime-manifest"

/**
 * D78: concepts page. Defines the 5 nouns operators see every day:
 * policy, verifier, evidence, pack, prebuilt. Plain language; if the
 * operator needs to think about an internal concept (matcher,
 * lifecycle), define it inline with a 1-2 sentence example.
 *
 * Review fix: the canonical verdict allowlist is imported from
 * `runtime-manifest` (gated against `src/magi_cp/policy/verdicts.py`).
 * Adding a verdict in Python without updating the manifest fails the
 * vitest gate before the docs go stale.
 */
export const dynamic = "force-static"

function VerdictList() {
  return (
    <>
      {LEDGER_VERDICTS_ORDERED.map((v, i) => (
        <Fragment key={v}>
          {i > 0 ? ", " : ""}
          <Code inline>{v}</Code>
        </Fragment>
      ))}
    </>
  )
}

export default function ConceptsPage() {
  const isKo = getLocale() === "ko"

  return (
    <DocsLayout
      current="concepts"
      title={isKo ? "개념" : "Concepts"}
      subtitle={isKo
        ? "정책, verifier, evidence, 팩, prebuilt. 다섯 가지 용어만 잡으면 나머지는 따라옵니다."
        : "Policy, verifier, evidence, pack, prebuilt. Pin these five and the rest follows."
      }
    >
      {isKo ? (
        <>
          <h2>정책 (policy)</h2>
          <p>
            훅 이벤트 + 매처 + verifier + 액션의 조합 하나입니다.
            예를 들어 “PreToolUse(Bash) 에서 rm -rf 가 들어오면 차단(block)”은 정책 한 개입니다.
            정책의 IR (intermediate representation) 은 JSON 한 덩어리고, 클라우드에 저장됩니다.
          </p>
          <p>
            매처는 “어떤 도구 호출에 이 정책을 적용할까”를 정합니다. 표준 도구 이름
            (<Code inline>Bash</Code>, <Code inline>Read</Code>) 이나 MCP 패턴
            (<Code inline>mcp__github__create_pr</Code>) 모두 지원합니다.
          </p>

          <h2>Verifier</h2>
          <p>
            “입력을 받아 verdict 를 내는 함수”입니다. 네 가지가 있습니다.
          </p>
          <ul>
            <li><b>step</b>: 사전에 떨어진 evidence step 의 존재만 확인 (가장 가볍습니다)</li>
            <li><b>regex</b>: 입력의 어느 필드가 어떤 정규식과 매칭하는지</li>
            <li><b>llm_critic</b>: LLM 에게 Yes/No 를 물어보는 guarded prompt</li>
            <li><b>shacl</b>: RDF 그래프 + SHACL 모양으로 결정론적 검증</li>
          </ul>
          <p>
            verdict 는 여섯 가지입니다: <VerdictList />.
            <Code inline>pass</Code> 와 <Code inline>not_applicable</Code> 만 통과로 취급되고,
            <Code inline>deny</Code> 는 차단을 즉시 강제, <Code inline>review</Code> 와
            <Code inline>needs_review</Code> 는 HITL 큐로 보냅니다.
            이 값들은 <Link href="/ledger">/ledger</Link> 와 <Link href="/policies">정책 dry-run</Link> 화면에
            그대로 표시되니, 도구가 모르는 verdict 를 보여 줄 일은 없습니다.
          </p>

          <h2>Evidence</h2>
          <p>
            verifier 가 만든 기록입니다. 어떤 입력에 대해 무엇을 봤는지, verdict 가 무엇이었는지
            구조화된 한 줄이 ledger 에 떨어집니다. <Link href="/ledger">/ledger</Link> 에서 볼 수 있습니다.
            evidence 는 정책 사이에 연결될 수 있습니다: 정책 A 가 evidence 를 떨어뜨리면 정책 B 가
            “A 의 evidence 가 있을 때만 통과” 를 강제합니다.
          </p>

          <h2>팩 (pack)</h2>
          <p>
            정책 묶음입니다. 도메인 별 묶음 (legal-filing, security-baseline, …) 을 한 번에
            on/off 합니다. 팩 단위로 켜면 안에 있는 정책이 전부 켜지고, 끄면 전부 꺼집니다.
            <Link href="/rules"> /rules</Link> 에서 다룹니다.
          </p>

          <h2>Prebuilt</h2>
          <p>
            한 번 클릭으로 켜지는 정책 템플릿입니다. <Link href="/presets">/presets</Link> 또는
            <Link href="/rules"> /rules</Link> 에서 보고 토글합니다.
            prebuilt 의 id 는 업그레이드를 가로질러 안정적이라는 약속이 있습니다
            (<Link href="/docs/upgrade">/docs/upgrade</Link>).
          </p>

          <h2>이벤트 lifecycle</h2>
          <p>
            한 턴은 보통 이렇게 흐릅니다.
            사용자 메시지마다 <Code inline>UserPromptSubmit</Code> 가 한 번 떨어지고,
            그 뒤로 도구 호출이 있을 때마다 <Code inline>PreToolUse</Code> →
            <Code inline>PostToolUse</Code> 쌍이 반복됩니다.
            마지막으로 <Code inline>Stop</Code> 또는 <Code inline>SessionEnd</Code> 가 닫아 줍니다.
            한 번의 <Code inline>UserPromptSubmit</Code> 뒤에 여러 도구 호출이 따라올 수 있으므로,
            “모든 Bash 직전”을 가드하려면 <Code inline>PreToolUse</Code> 정책이 맞습니다.
            magi-cp 정책은 어느 이벤트에든 붙을 수 있지만 액션 종류가 다릅니다
            (<Link href="/docs/inject-context">inject-context</Link>,
            <Link href="/docs/input-rewrite"> input-rewrite</Link>).
          </p>

          <CalloutAside tone="note" title="요약">
            정책 = 매처 + verifier + 액션. verifier 가 만드는 게 evidence. 팩은 정책 묶음, prebuilt 는 템플릿.
          </CalloutAside>
        </>
      ) : (
        <>
          <h2>Policy</h2>
          <p>
            A single composition of hook event + matcher + verifier + action.
            For example, "PreToolUse(Bash) blocks on rm -rf" is one policy.
            Its IR (intermediate representation) is a JSON blob stored in the cloud.
          </p>
          <p>
            The matcher decides "which tool calls does this policy apply to".
            It accepts standard tool names (<Code inline>Bash</Code>,
            <Code inline>Read</Code>) or MCP patterns
            (<Code inline>mcp__github__create_pr</Code>).
          </p>

          <h2>Verifier</h2>
          <p>
            A function that takes an input and returns a verdict. Four kinds:
          </p>
          <ul>
            <li><b>step</b>: confirms an earlier evidence step exists (lightest)</li>
            <li><b>regex</b>: matches a field of the input against a regex</li>
            <li><b>llm_critic</b>: a guarded Yes/No prompt to an LLM</li>
            <li><b>shacl</b>: deterministic RDF graph + SHACL shape</li>
          </ul>
          <p>
            The verdict allowlist is six values: <VerdictList />.
            <Code inline>pass</Code> and <Code inline>not_applicable</Code> are passing;
            <Code inline>deny</Code> blocks immediately; <Code inline>review</Code> and
            <Code inline>needs_review</Code> route to the HITL queue. These exact strings
            show up in <Link href="/ledger">/ledger</Link> and the policy dry-run UI, so the
            console will never display a verdict the docs haven't named.
          </p>

          <h2>Evidence</h2>
          <p>
            The record a verifier produces. For each input it logs what it saw and what
            verdict it returned, into the ledger at <Link href="/ledger">/ledger</Link>.
            Evidence can chain between policies: policy A drops evidence, policy B requires
            that evidence be present to pass.
          </p>

          <h2>Pack</h2>
          <p>
            A bundle of policies. Toggle a domain bundle (legal-filing, security-baseline, ...)
            on or off as a single unit. Managed at <Link href="/rules">/rules</Link>.
          </p>

          <h2>Prebuilt</h2>
          <p>
            A one-click policy template. Listed at <Link href="/presets">/presets</Link>
            or <Link href="/rules">/rules</Link>. Prebuilt ids are stable across upgrades
            (see <Link href="/docs/upgrade">upgrade</Link>).
          </p>

          <h2>Event lifecycle</h2>
          <p>
            A typical turn looks like this. One <Code inline>UserPromptSubmit</Code> fires
            on the user message, then one or more <Code inline>PreToolUse</Code> →
            <Code inline>PostToolUse</Code> pairs follow as the model uses tools, and
            <Code inline>Stop</Code> or <Code inline>SessionEnd</Code> closes the turn.
            Because many tool calls can ride one <Code inline>UserPromptSubmit</Code>,
            "gate every Bash" belongs on <Code inline>PreToolUse</Code>, not on
            <Code inline>UserPromptSubmit</Code>. A magi-cp policy can bind to any event,
            but the available action kinds differ (<Link href="/docs/inject-context">inject context</Link>,
            <Link href="/docs/input-rewrite">rewrite input</Link>).
          </p>

          <CalloutAside tone="note" title="TL;DR">
            Policy = matcher + verifier + action. Verifiers produce evidence.
            Packs bundle policies; prebuilts are one-click templates.
          </CalloutAside>
        </>
      )}
    </DocsLayout>
  )
}
