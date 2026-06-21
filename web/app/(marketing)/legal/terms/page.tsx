import { getLocale } from "@/lib/i18n/server"
import { PageHeader } from "@/components/ui"

export const dynamic = "force-dynamic"

const KO = `
## 1. 서비스 개요

Magi Control Plane(이하 "본 서비스")은 OpenMagi(이하 "회사")가 제공하는 Claude
Code의 거버넌스 미들웨어입니다. 사용자는 본인의 firm/회사 자체 정책을 작성하고,
회사가 호스팅하는 클라우드 게이트가 정책에 따라 도구 호출을 허용/거부합니다.

본 알파 파일럿은 **무료**로 제공되며, 회사는 언제든지 가격 정책을 변경할 수
있으나 변경 시 30일 전 이메일로 통지합니다.

## 2. 사용자의 책임

- API 키는 본인 계정 자격증명입니다. 외부에 공유하지 마세요.
- 본 서비스를 통한 정책 작성/검증의 법적 효력은 사용자가 보장합니다.
  회사는 자연어 컴파일 결과의 정확성에 대해 보증하지 않습니다.
- 검증에 사용되는 사용자 입력(payload)에 개인정보(특히 주민등록번호 등) 등
  민감 정보를 포함하지 마세요. privilege_scan 등의 검증기는 검출만 할 뿐
  마스킹하지 않으며, 검출 시 본문은 감사 원장에 기록되지 않지만 신중을 기해
  주십시오.

## 3. 회사의 책임 한계

- 본 서비스는 "있는 그대로" 제공되며, 회사는 명시적/묵시적 어떠한 보증도
  하지 않습니다.
- 본 서비스 사용으로 발생하는 직간접 손해에 대해 회사는 책임지지 않습니다.
- 다음의 경우에는 SLA 외 조치를 취하지 않습니다: (i) 사용자의 잘못된 설정,
  (ii) Claude Code 자체의 버그/변경, (iii) 통제 불가능한 외부 의존성
  (Anthropic/OpenAI API 등).

## 4. 데이터 처리

- 모든 정책, 감사 기록, HITL 의사결정은 사용자의 테넌트 내에서만 격리됩니다.
- 회사는 사용자 데이터를 광고나 마케팅 목적으로 사용하지 않습니다.
- 회사는 본 서비스 운영을 위해 최소한의 로그(API 호출 metric, 에러 트레이스)를
  수집하며, 이는 IP/이메일과 연결되지 않습니다.
- 자세한 내용은 [개인정보처리방침](/legal/privacy)을 참조하세요.

## 5. 종료

- 사용자는 언제든지 알파 파일럿을 중단할 수 있으며, 요청 시 회사는 30일 내에
  사용자 데이터를 영구 삭제합니다.
- 회사는 (i) 본 약관 위반, (ii) 보안 사고 발생, (iii) 서비스 종료를 사유로
  사전 통지 후 사용자 계정을 정지/해지할 수 있습니다.

## 6. 변경 사항

본 약관은 변경될 수 있으며, 변경 시 사용자에게 이메일로 통지합니다. 변경 후
서비스를 계속 사용하시면 변경된 약관에 동의한 것으로 간주됩니다.

## 7. 준거법

본 약관은 대한민국 법률에 따릅니다.

## 8. 문의

질문이나 분쟁이 있으시면 kevin@openmagi.ai로 연락해 주세요.
`.trim()

const EN = `
## 1. Service

Magi Control Plane ("the Service") is a governance middleware for Claude Code
operated by OpenMagi ("we"). You author your firm/company's own policies; our
hosted cloud gate enforces them on every covered tool call.

This alpha pilot is **free**. We may change pricing later; if we do, we will
email you at least 30 days before any change takes effect.

## 2. Your responsibilities

- Your API key is your credential. Don't share it.
- You are responsible for the legal effect of policies you author. We do not
  warrant the accuracy of natural-language compile output.
- Don't submit sensitive personal data (in particular Korean RRN, US SSN
  equivalents) into verifier payloads. Our verifiers detect such patterns
  but do not redact upstream.

## 3. Disclaimers

- The Service is provided "AS IS". We disclaim all warranties, express or
  implied.
- We are not liable for direct or indirect damages from your use of the
  Service.
- We do not honour SLAs in any of: (i) misconfiguration on your end,
  (ii) Claude Code defects or breaking changes, (iii) outages of external
  dependencies (Anthropic / OpenAI APIs, hosting providers).

## 4. Data

- Policies, audit ledger entries, and HITL decisions are isolated to your
  tenant.
- We never use your data for advertising or training.
- We collect minimal operational metrics (rate, error trace) not tied to
  your IP or email. See the [Privacy Policy](/legal/privacy).

## 5. Termination

- You may quit the alpha at any time. On request we permanently delete your
  data within 30 days.
- We may suspend or terminate your account, with notice, in cases of:
  (i) breach of these Terms, (ii) security incident, or (iii) Service sunset.

## 6. Changes

We may update these Terms with email notice. Continued use after the
effective date constitutes acceptance.

## 7. Governing law

These Terms are governed by the laws of the Republic of Korea.

## 8. Contact

Questions or disputes: kevin@openmagi.ai.
`.trim()

function MarkdownBody({ src }: { src: string }) {
  // tiny renderer: split into headings (## …) and paragraphs.
  const blocks = src.split(/\n\n+/)
  return (
    <div className="prose-like text-sm leading-7 text-[var(--color-text-secondary)] max-w-3xl space-y-4">
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

export default function TermsPage() {
  const locale = getLocale()
  const body = locale === "ko" ? KO : EN
  return (
    <>
      <PageHeader
        title={locale === "ko" ? "이용약관" : "Terms of Service"}
        description={locale === "ko"
          ? "마지막 업데이트: 2026-06-20 (Alpha 파일럿 버전)"
          : "Last updated: 2026-06-20 (Alpha pilot)"}
      />
      <MarkdownBody src={body} />
    </>
  )
}
