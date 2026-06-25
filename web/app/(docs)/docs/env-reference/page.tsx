import { getLocale } from "@/lib/i18n/server"
import { Code } from "@/components/ui"
import { DocsLayout } from "../_components/DocsLayout"
import { CalloutAside } from "../_components/CalloutAside"
import { ENV_REFERENCE, groupEntries, type EnvVarEntry } from "@/lib/env-reference"

/**
 * D78: every MAGI_CP_* env var the operator might encounter, plus
 * the two provider keys we depend on. Server-rendered static; the
 * source-of-truth array lives in web/lib/env-reference.ts.
 */
export const dynamic = "force-static"

const GROUP_TITLES: Record<EnvVarEntry["group"], { ko: string; en: string }> = {
  cloud:     { ko: "클라우드 (제어판 서버)",      en: "Cloud (control-plane server)" },
  local:     { ko: "로컬 (Claude Code 플러그인)",  en: "Local (Claude Code plugin)" },
  dashboard: { ko: "대시보드 (Next.js 웹)",        en: "Dashboard (Next.js web)" },
  provider:  { ko: "LLM 프로바이더",               en: "LLM providers" },
}

function GroupSection({
  group, isKo,
}: { group: EnvVarEntry["group"]; isKo: boolean }) {
  const entries = groupEntries()[group]
  const title = GROUP_TITLES[group][isKo ? "ko" : "en"]
  return (
    <section className="mt-6">
      <h2 className="m-0 mb-3 text-lg font-semibold text-[var(--color-text-primary)]">{title}</h2>
      <div className="overflow-x-auto rounded-lg border border-[var(--color-border-subtle)]">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-white/60 text-left text-xs uppercase tracking-wide text-[var(--color-text-tertiary)]">
              <th className="px-3 py-2 font-medium">{isKo ? "이름" : "Name"}</th>
              <th className="px-3 py-2 font-medium">{isKo ? "기본값" : "Default"}</th>
              <th className="px-3 py-2 font-medium">{isKo ? "설명" : "Description"}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.name} className="border-t border-[var(--color-border-subtle)] align-top">
                <td className="px-3 py-2 whitespace-nowrap">
                  <Code inline>{e.name}</Code>
                  {e.allowed && (
                    <div className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">
                      {isKo ? "허용값: " : "Allowed: "}{e.allowed}
                    </div>
                  )}
                </td>
                <td className="px-3 py-2 whitespace-nowrap text-xs text-[var(--color-text-secondary)] font-mono">
                  {e.default}
                </td>
                <td className="px-3 py-2 text-xs text-[var(--color-text-secondary)] leading-5">
                  {isKo ? e.ko : e.en}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default function EnvReferencePage() {
  const isKo = getLocale() === "ko"

  return (
    <DocsLayout
      current="env-reference"
      title={isKo ? "환경변수 레퍼런스" : "Env reference"}
      subtitle={isKo
        ? `Magi Control Plane 이 인식하는 모든 환경변수 ${ENV_REFERENCE.length} 개. 기본값과 한 줄 설명.`
        : `Every env var Magi Control Plane reads. ${ENV_REFERENCE.length} entries with defaults and one-line descriptions.`
      }
    >
      <CalloutAside tone="note">
        {isKo
          ? <>이 페이지는 빌드 시점에 굳어 있습니다. 새 환경변수가 추가되면 <Code inline>web/lib/env-reference.ts</Code> 를 함께 업데이트하세요.</>
          : <>This page is statically rendered at build time. When a new env var ships, update <Code inline>web/lib/env-reference.ts</Code> alongside it.</>
        }
      </CalloutAside>

      <GroupSection group="cloud" isKo={isKo} />
      <GroupSection group="local" isKo={isKo} />
      <GroupSection group="dashboard" isKo={isKo} />
      <GroupSection group="provider" isKo={isKo} />
    </DocsLayout>
  )
}
