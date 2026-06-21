import { getLocale } from "@/lib/i18n/server"
import { PageHeader } from "@/components/ui"

export const dynamic = "force-dynamic"

const KO = `
## 1. 수집 항목

본 서비스 운영을 위해 회사는 다음 정보를 수집합니다.

- **계정 정보**: 이메일, 소속 firm/회사 (Clawy Pro+ 결제 시 Stripe 경유)
- **사용 데이터**: API 호출 시간 / endpoint / 응답 코드 — 사용자 식별 정보 미포함
- **감사 원장**: 사용자가 작성한 정책, 검증 결과, HITL 의사결정 — 사용자
  테넌트 내에서만 보관
- **로그**: 에러 트레이스, 인프라 메트릭 — IP/이메일 미연결

회사는 **사용자가 검증에 제출한 텍스트(payload) 본문을 저장하지 않습니다**.
검증 결과(verdict, reasons)만 감사 원장에 기록됩니다.

## 2. 이용 목적

수집된 정보는 (i) 본 서비스 제공, (ii) 보안 침해 대응, (iii) 사용자 지원에만
사용됩니다. 마케팅이나 광고 목적으로 사용하지 않습니다.

## 3. 제3자 제공

다음의 경우에만 제3자에게 정보를 제공합니다:

- 법령에 따른 정부 요청 (영장, 수사 영장 등)
- 인프라 제공업체 (호스팅, 모니터링) — 최소한의 운영 데이터만, DPA 체결 후

타사 AI API(Anthropic, OpenAI)에는 **/policies/compile 페이지에서 자연어를
입력한 경우에만** 해당 자연어가 전송됩니다. 다른 사용자 데이터는 전송되지
않습니다.

## 4. 보관 기간

- 감사 원장: 사용자 테넌트 활성 기간 + 종료 후 30일
- 운영 로그: 90일
- Pro+ 가입 정보: 구독 활성 + 종료 후 3년 (마케팅 거부 시 즉시 삭제)

## 5. 사용자 권리

대한민국 개인정보보호법에 따라 사용자는 다음 권리를 가집니다:

- 본인 정보 열람·정정·삭제 요청
- 처리 정지 요청
- 동의 철회

권리 행사: kevin@openmagi.ai

## 6. 쿠키

본 서비스는 다음 쿠키를 사용합니다:

- \`magi-cp-locale\` (영구): 언어 설정. 24개월 유지.
- \`magi-cp-setup-key\` (HttpOnly, 24h): 설치 가이드 페이지의 API 키 캐싱.
- \`magi-cp-compile-result\`, \`magi-cp-verify-result\` (5분): 큰 결과 페이로드
  임시 저장.

모든 쿠키는 SameSite=Lax. 광고/추적 쿠키 사용하지 않습니다.

## 7. 정보 보호 책임자

- 이름: Kevin Sohn
- 이메일: kevin@openmagi.ai

## 8. 변경 사항

본 정책은 변경될 수 있으며, 변경 시 이메일로 통지합니다.
`.trim()

const EN = `
## 1. What we collect

To operate the Service we collect:

- **Account info**: email, firm/company (passed through from Stripe at Clawy Pro+ checkout)
- **Usage data**: timestamp / endpoint / status code — no PII
- **Audit ledger**: your policies, verifier verdicts, HITL decisions —
  isolated to your tenant
- **Logs**: error traces, infra metrics — not tied to IP/email

We **do not store the payload text** you submit to verifiers. Only the
verdict (and reasons) is appended to the audit ledger.

## 2. How we use it

To (i) operate the Service, (ii) respond to security incidents, (iii) support
alpha customers. **Never** for advertising, marketing, or model training.

## 3. Sharing

We only share data with third parties in these cases:

- Legal compulsion (warrants, subpoenas)
- Infrastructure providers (hosting, monitoring) — only operational data,
  under DPA

External AI APIs (Anthropic, OpenAI) **only receive natural-language text
you type into /policies/compile**. No other tenant data is sent.

## 4. Retention

- Audit ledger: while your tenant is active + 30 days
- Operational logs: 90 days
- Pro+ subscription records: while active + 3 years (deleted immediately on opt-out)

## 5. Your rights

Under the Korean PIPA and EU GDPR (where applicable):

- Access / correct / delete your data
- Halt processing
- Withdraw consent

Exercise these rights: kevin@openmagi.ai

## 6. Cookies

- \`magi-cp-locale\` (permanent): language preference (24 months)
- \`magi-cp-setup-key\` (HttpOnly, 24h): caches the key in the Setup wizard
- \`magi-cp-compile-result\`, \`magi-cp-verify-result\` (5 min): temporary
  buffer for large result payloads

All cookies SameSite=Lax. No advertising or tracking cookies.

## 7. Data Protection Officer

- Name: Kevin Sohn
- Email: kevin@openmagi.ai

## 8. Changes

We may update this policy. We will email you when we do.
`.trim()

function MarkdownBody({ src }: { src: string }) {
  const blocks = src.split(/\n\n+/)
  return (
    <div className="text-sm leading-7 text-[var(--color-text-secondary)] max-w-3xl space-y-4">
      {blocks.map((b, i) => {
        if (b.startsWith("## ")) {
          return (
            <h2 key={i} className="text-md font-semibold text-[var(--color-text-primary)] mt-6">
              {b.replace(/^## /, "")}
            </h2>
          )
        }
        const looksList = b.split("\n").every(line => line.trim().startsWith("- "))
        if (looksList) {
          return (
            <ul key={i} className="list-disc pl-5 space-y-1">
              {b.split("\n").map((line, j) => (
                <li key={j}>{line.replace(/^-\s+/, "")}</li>
              ))}
            </ul>
          )
        }
        return <p key={i}>{b}</p>
      })}
    </div>
  )
}

export default function PrivacyPage() {
  const locale = getLocale()
  const body = locale === "ko" ? KO : EN
  return (
    <>
      <PageHeader
        title={locale === "ko" ? "개인정보처리방침" : "Privacy Policy"}
        description={locale === "ko"
          ? "마지막 업데이트: 2026-06-20 (Alpha 파일럿 버전)"
          : "Last updated: 2026-06-20 (Alpha pilot)"}
      />
      <MarkdownBody src={body} />
    </>
  )
}
