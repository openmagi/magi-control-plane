import Link from "next/link"
import { getLocale } from "@/lib/i18n/server"
import { Code, CodeBlock } from "@/components/ui"
import { DocsLayout } from "../_components/DocsLayout"
import { CalloutAside } from "../_components/CalloutAside"
import { POLICY_IR_VERSION_FIELD } from "@/lib/runtime-manifest"

/**
 * D78: how to upgrade the docker stack, version compatibility, and
 * the prebuilt-id stability promise (D60).
 *
 * Review fix: the policy-IR version field is named `version` per
 * dataclass in `src/magi_cp/policy/ir.py`. The backup script in
 * `scripts/` is `backup.sh`; there is no `snapshot.sh`.
 */
export const dynamic = "force-static"

export default function UpgradePage() {
  const isKo = getLocale() === "ko"

  return (
    <DocsLayout
      current="upgrade"
      title={isKo ? "업그레이드" : "Upgrade"}
      subtitle={isKo
        ? "Docker 스택을 안전하게 올리는 절차, 버전 호환성, 그리고 prebuilt id 안정성 약속."
        : "Safe upgrade procedure for the docker stack, version compatibility, and the prebuilt-id stability promise."
      }
    >
      {isKo ? (
        <>
          <h2>업그레이드 절차</h2>
          <ol>
            <li>릴리즈 노트를 한 줄이라도 읽습니다 (특히 breaking 표기).</li>
            <li>현재 정책·스크립트·ledger 백업: <Code inline>./scripts/backup.sh</Code></li>
            <li>이미지를 새 태그로 풀: <Code inline>docker compose pull</Code></li>
            <li>중단 없이 교체: <Code inline>docker compose up -d</Code></li>
            <li>대시보드 새로 고침, <Link href="/setup">/setup</Link> 가 여전히 API 키를 인식하는지 확인.</li>
            <li>한 prebuilt 를 시뮬레이터로 던져 보면 끝.</li>
          </ol>

          <h2>버전 호환성</h2>
          <p>
            컨트롤 플레인은 <b>마이너 N → N+1</b> 까지 정책 IR 호환을 약속합니다.
            메이저 업이 있을 때만 IR 마이그레이션 스크립트가 필요합니다.
            저장된 정책 JSON 의 각 정책 타입은 자기 <Code inline>{POLICY_IR_VERSION_FIELD}</Code>
            필드를 따로 들고 있습니다 (모든 dataclass 가 <Code inline>{POLICY_IR_VERSION_FIELD}: str = "0.1"</Code>
            로 시작). 한 정책의 IR 버전이 궁금하면 그 객체의 <Code inline>{POLICY_IR_VERSION_FIELD}</Code> 키를 보세요.
          </p>

          <CalloutAside tone="warn" title="롤백 정책">
            정책 IR 은 forward-compatible 만 약속됩니다.
            새 버전에서 만든 정책은 옛 버전에서 부적합으로 인식될 수 있습니다.
            업그레이드 후 만든 정책은 가능한 한 같은 메이저 안에서만 다루세요.
          </CalloutAside>

          <h2>Prebuilt id 안정성 약속 (D60)</h2>
          <p>
            한 번 공개된 prebuilt 의 id 는 그 메이저 라인이 살아 있는 한 절대 바뀌지 않습니다.
            id 가 바뀌면 운영자가 켜 둔 토글이 "사라진" 것처럼 보이고, 자동화 스크립트가 깨지기 때문입니다.
          </p>
          <p>
            대신 다음과 같은 비차단 변화는 허용됩니다.
          </p>
          <ul>
            <li>설명·라벨·태그 같은 메타 필드는 자유롭게 개선됩니다.</li>
            <li>verifier 의 내부 구현은 같은 verdict 를 유지하는 한 교체될 수 있습니다.</li>
            <li>새 prebuilt 가 추가되며, 옛것은 <Code inline>status: deprecated</Code> 로 표시됩니다.</li>
          </ul>

          <h2>업그레이드가 실패할 때</h2>
          <ol>
            <li>이전 태그로 되돌리기: <Code inline>docker compose down && IMAGE_TAG=&lt;prev&gt; docker compose up -d</Code></li>
            <li><Code inline>backup.sh</Code> 의 결과를 풀어 정책·스크립트 디렉터리 복구.</li>
            <li><Link href="/docs/troubleshooting">문제 해결</Link> 의 “Cloud unreachable” 절을 따라가서 클라우드 살아남 확인.</li>
          </ol>

          <h2>관련</h2>
          <ul>
            <li><Link href="/docs/env-reference">환경변수 레퍼런스</Link>: 버전 사이에 추가·삭제되는 환경변수 추적</li>
            <li><Link href="/endpoints">엔드포인트</Link>: 플러그인 측 에이전트 버전 확인</li>
          </ul>
        </>
      ) : (
        <>
          <h2>Upgrade procedure</h2>
          <ol>
            <li>Read the release notes (especially the breaking section).</li>
            <li>Back up current policies, scripts, ledger: <Code inline>./scripts/backup.sh</Code>.</li>
            <li>Pull the new image tag: <Code inline>docker compose pull</Code>.</li>
            <li>Zero-downtime swap: <Code inline>docker compose up -d</Code>.</li>
            <li>Refresh the dashboard. Confirm <Link href="/setup">/setup</Link> still accepts your API key.</li>
            <li>Throw one prebuilt at the simulator. Done.</li>
          </ol>

          <h2>Version compatibility</h2>
          <p>
            The control plane promises <b>minor N → N+1</b> Policy-IR compatibility.
            Major upgrades require an IR migration script. Every policy dataclass carries its
            own <Code inline>{POLICY_IR_VERSION_FIELD}</Code> field (the JSON key is literally
            <Code inline> {POLICY_IR_VERSION_FIELD}</Code>, defaulting to
            <Code inline> "0.1"</Code>); inspect that key on a saved policy to see what IR
            version it was authored on.
          </p>

          <CalloutAside tone="warn" title="Rollback policy">
            Policy IR is only forward-compatible. Policies authored on a newer version may not
            load on an older version. After an upgrade, treat new policies as same-major-only.
          </CalloutAside>

          <h2>Prebuilt id stability (D60)</h2>
          <p>
            Once a prebuilt is public, its id is stable for the lifetime of that major line.
            Renaming an id would make an operator's toggle "vanish" and break automation
            scripts.
          </p>
          <p>
            These non-breaking changes are allowed:
          </p>
          <ul>
            <li>Description, label, and tags can change freely.</li>
            <li>Verifier internals can be swapped as long as the verdict is preserved.</li>
            <li>New prebuilts are added; old ones gain <Code inline>status: deprecated</Code>.</li>
          </ul>

          <h2>If an upgrade fails</h2>
          <ol>
            <li>Roll back to the previous tag: <Code inline>docker compose down && IMAGE_TAG=&lt;prev&gt; docker compose up -d</Code>.</li>
            <li>Restore from the <Code inline>backup.sh</Code> output.</li>
            <li>Walk through the "Cloud unreachable" section of <Link href="/docs/troubleshooting">Troubleshooting</Link>.</li>
          </ol>

          <h2>Related</h2>
          <ul>
            <li><Link href="/docs/env-reference">Env reference</Link>: track env vars added/removed across versions.</li>
            <li><Link href="/endpoints">Endpoints</Link>: confirm plugin-side agent version.</li>
          </ul>
        </>
      )}
    </DocsLayout>
  )
}
