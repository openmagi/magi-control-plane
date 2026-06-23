import type { Metadata } from "next"
import { getLocale } from "@/lib/i18n/server"
import { CopyButton } from "@/components/ui/CopyButton"

export const dynamic = "force-dynamic"

export const metadata: Metadata = {
  title: "Install · magi-control-plane",
  description:
    "한 줄 명령. Docker만 있으면 인스톨러가 자동으로 compose.yml 다운로드, .env 생성, 이미지 pull, Claude Code 배선까지 처리합니다.",
  openGraph: { title: "Install · magi-control-plane", type: "website" },
  alternates: { canonical: "/install" },
  robots: { index: true, follow: true },
}

/** D40 install page. Self-host only (Pro+/hosted dropped). One curl
 *  command that boots a local control plane via docker compose, wires
 *  Claude Code at the user's per-user paths, runs smoke test. Page copy
 *  matches scripts/quickstart.sh exactly. */
export default async function InstallPage({
  searchParams,
}: {
  searchParams: Record<string, string | undefined>
}) {
  const locale = await getLocale()
  const isKo = locale === "ko"
  const C = isKo ? KO_INSTALL : EN_INSTALL
  const oneLiner = "curl -fsSL https://cp.openmagi.ai/install.sh | bash"
  const redirectedFrom = searchParams.from
  return (
    <div className="bg-white">
      {redirectedFrom && (
        <div className="border-b border-amber-200 bg-amber-50">
          <div
            className="mx-auto px-5 md:px-8 py-3 text-sm text-amber-900"
            style={{ maxWidth: "var(--content-max)" }}
          >
            {isKo
              ? <>대시보드 페이지 <code className="font-mono">{redirectedFrom}</code> 는 자기 컨트롤 플레인을 self-host한 뒤 <code className="font-mono">http://localhost:3000{redirectedFrom}</code> 에서 보입니다. cp.openmagi.ai 는 install 안내만 호스팅합니다.</>
              : <>The dashboard page <code className="font-mono">{redirectedFrom}</code> only exists in your self-hosted control plane. After install, open <code className="font-mono">http://localhost:3000{redirectedFrom}</code>. cp.openmagi.ai only hosts the install guide.</>}
          </div>
        </div>
      )}
      {/* Hero band */}
      <section className="border-b border-[var(--color-border-subtle)] bg-[var(--canvas)]">
        <div
          className="mx-auto px-5 md:px-8 py-14 md:py-20 text-center"
          style={{ maxWidth: "var(--content-max)" }}
        >
          <p className="text-[12px] font-bold uppercase tracking-[0.18em] text-[var(--brand)]">
            {C.eyebrow}
          </p>
          <h1 className="mt-3 text-4xl md:text-5xl font-bold tracking-tight text-balance text-[var(--ink)] leading-[1.08]">
            {C.title}
          </h1>
          <p className="mt-4 mx-auto max-w-2xl text-base md:text-lg text-pretty text-[var(--body)] leading-7">
            {C.subtitle}
          </p>

          <div className="mt-10 mx-auto max-w-2xl">
            <div className="rounded-2xl border border-[var(--panel-border)] bg-[var(--term-bg)] overflow-hidden shadow-[0_18px_48px_-22px_rgba(15,23,42,0.25)]">
              <div className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--panel-border)]/70">
                <span className="text-[10.5px] font-mono uppercase tracking-[0.14em] text-[var(--term-dim)]">
                  Terminal
                </span>
                <CopyButton value={oneLiner} size="sm" variant="ghost" />
              </div>
              <pre
                translate="no"
                className="m-0 px-5 py-4 text-[14px] md:text-[15px] leading-7 font-mono text-[var(--term-out)] whitespace-pre overflow-x-auto text-left"
              >
                {oneLiner}
              </pre>
            </div>
            <p className="mt-3 text-xs text-[var(--subtle)]">{C.commandHint}</p>
          </div>
        </div>
      </section>

      {/* What happens transcript */}
      <section>
        <div
          className="mx-auto px-5 md:px-8 py-14 md:py-20"
          style={{ maxWidth: "var(--content-max)" }}
        >
          <div className="text-center max-w-2xl mx-auto">
            <p className="text-[12px] font-bold uppercase tracking-[0.18em] text-[var(--brand)]">
              {C.walkthroughEyebrow}
            </p>
            <h2 className="mt-3 text-3xl md:text-4xl font-bold text-[var(--ink)] tracking-tight text-balance leading-[1.1]">
              {C.walkthroughHeading}
            </h2>
            <p className="mt-4 mx-auto max-w-xl text-base text-[var(--body)] leading-7 text-pretty">
              {C.walkthroughBody}
            </p>
          </div>

          <figure className="m-0 mt-10 mx-auto max-w-3xl">
            <div className="rounded-xl border border-[var(--panel-border)] bg-[var(--term-bg)] shadow-[0_28px_70px_-22px_rgba(15,23,42,0.35)] overflow-hidden">
              <div className="flex items-center gap-1.5 px-3.5 py-2.5 border-b border-[var(--panel-border)]/80">
                <span aria-hidden="true" className="w-2.5 h-2.5 rounded-full bg-[#FF5F57]" />
                <span aria-hidden="true" className="w-2.5 h-2.5 rounded-full bg-[#FEBC2E]" />
                <span aria-hidden="true" className="w-2.5 h-2.5 rounded-full bg-[#28C840]" />
                <span className="ml-3 text-[11px] font-mono text-[var(--term-dim)]">{C.transcriptTitle}</span>
              </div>
              <pre
                translate="no"
                className="m-0 px-6 py-6 text-[13px] leading-[1.75] font-mono text-[var(--term-out)] whitespace-pre overflow-x-auto"
              >
                <TranscriptLines t={C.transcript} oneLiner={oneLiner} />
              </pre>
            </div>
            <figcaption className="mt-3 text-center text-[11px] text-[var(--subtle)] tracking-wide">
              {C.transcript.caption}
            </figcaption>
          </figure>

          {/* 3-column "what it handles" */}
          <div className="mt-14 grid gap-5 md:grid-cols-3 max-w-5xl mx-auto">
            {C.checklist.map((it) => (
              <div
                key={it.label}
                className="relative rounded-2xl border border-[var(--color-border-subtle)] bg-white p-6 overflow-hidden"
              >
                <span aria-hidden="true" className="absolute inset-y-0 left-0 w-[3px] bg-[var(--brand)]" />
                <div className="pl-3">
                  <span aria-hidden="true" className="inline-flex w-9 h-9 items-center justify-center rounded-lg bg-[var(--brand-tint)] border border-[var(--brand)]/20 text-[var(--brand-strong)]">
                    <svg className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
                      <path d="M16.7 5.3a1 1 0 010 1.4l-7 7a1 1 0 01-1.4 0l-3.5-3.5a1 1 0 011.4-1.4l2.8 2.8 6.3-6.3a1 1 0 011.4 0z" />
                    </svg>
                  </span>
                  <h3 className="mt-4 text-base md:text-lg font-semibold text-[var(--ink)] m-0 leading-snug">{it.label}</h3>
                  <p className="mt-2 text-sm text-[var(--body)] leading-7">{it.detail}</p>
                </div>
              </div>
            ))}
          </div>

          {/* Details */}
          <details className="mt-14 mx-auto max-w-3xl group rounded-2xl border border-[var(--color-border-subtle)] bg-[var(--canvas)] transition-colors duration-200">
            <summary className="flex items-center justify-between cursor-pointer list-none px-5 py-4 rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)]/40">
              <span className="text-sm font-semibold text-[var(--ink)]">{C.whatTitle}</span>
              <svg aria-hidden="true" className="w-4 h-4 text-[var(--subtle)] transition-transform duration-200 group-open:rotate-180" viewBox="0 0 20 20" fill="currentColor">
                <path d="M10 12.5l-4.7-4.7a1 1 0 011.4-1.4L10 9.7l3.3-3.3a1 1 0 011.4 1.4L10 12.5z" />
              </svg>
            </summary>
            <ol className="px-5 pb-5 space-y-2 text-sm text-[var(--body)] leading-6">
              {C.whatItems.map((it, i) => (
                <li key={it} className="flex items-start gap-2">
                  <span className="mt-0.5 inline-flex w-5 h-5 items-center justify-center rounded-full bg-[var(--brand-tint)] text-[10.5px] font-bold text-[var(--brand-strong)] tabular-nums shrink-0">
                    {i + 1}
                  </span>
                  <span>{it}</span>
                </li>
              ))}
            </ol>
          </details>

          <p className="mt-8 text-center text-sm text-[var(--subtle)]">
            <a
              href="https://github.com/openmagi"
              target="_blank" rel="noopener noreferrer"
              className="text-[var(--brand)] hover:text-[var(--brand-strong)] hover:underline"
            >
              {C.repoLink} →
            </a>
            <span className="mx-2 text-[var(--ink)]/20">·</span>
            <a
              href="mailto:kevin@openmagi.ai?subject=magi-control-plane%20support"
              className="text-[var(--brand)] hover:text-[var(--brand-strong)] hover:underline"
            >
              {C.docs} →
            </a>
          </p>
        </div>
      </section>
    </div>
  )
}

// ── transcript rendering ────────────────────────────────────────────
type TranscriptLine =
  | { kind: "shell"; text: string }
  | { kind: "banner"; text: string }
  | { kind: "step"; text: string }
  | { kind: "ok"; text: string }
  | { kind: "done"; text: string }
  | { kind: "note"; text: string }
  | { kind: "blank" }

type Transcript = {
  caption: string
  lines: TranscriptLine[]
}

function TranscriptLines({ t, oneLiner }: { t: Transcript; oneLiner: string }) {
  return (
    <>
      <span>{"$ "}</span><span className="text-[var(--panel-bright)]">{oneLiner}</span>{"\n"}
      {t.lines.map((line, i) => {
        const key = i
        if (line.kind === "blank") return <span key={key}>{"\n"}</span>
        if (line.kind === "shell") return <span key={key}>{line.text}{"\n"}</span>
        if (line.kind === "banner") {
          return (
            <span key={key}>
              <span className="text-[var(--brand-ring)]">{"▸ "}</span>
              <span className="text-[var(--panel-bright)]">{line.text}</span>{"\n"}
            </span>
          )
        }
        if (line.kind === "step") {
          return (
            <span key={key}>
              <span className="text-[var(--brand-ring)]">{"→ "}</span>
              <span className="text-[var(--term-out)]">{line.text}</span>{"\n"}
            </span>
          )
        }
        if (line.kind === "ok") {
          return (
            <span key={key}>
              <span>{"  "}</span>
              <span className="text-[var(--term-prompt)]">{"✓ "}</span>
              <span className="text-[var(--term-dim)]">{line.text}</span>{"\n"}
            </span>
          )
        }
        if (line.kind === "done") {
          return (
            <span key={key}>
              <span className="text-[var(--term-prompt)]">{"✓ "}</span>
              <span className="text-[var(--panel-bright)]">{line.text}</span>{"\n"}
            </span>
          )
        }
        // note
        return (
          <span key={key}>
            <span className="text-[var(--term-dim)]">{line.text}</span>{"\n"}
          </span>
        )
      })}
    </>
  )
}

// ── copy ──────────────────────────────────────────────────────────
const KO_INSTALL = {
  eyebrow: "INSTALL",
  title: "한 줄로 self-host 띄우기",
  subtitle: "Docker만 있으면 됩니다. 인스톨러가 compose.yml 다운로드, .env 자동 생성, 이미지 pull, Claude Code 배선까지 한 흐름으로 처리합니다.",
  commandHint: "본인 머신에서 docker compose로 돕니다. 외부 호스팅 의존 없음. 포트 3000/8787이 사용 중이면 자동으로 인접 빈 포트를 잡습니다.",
  walkthroughEyebrow: "WHAT HAPPENS",
  walkthroughHeading: "인스톨러가 자동으로 도는 흐름",
  walkthroughBody: "복사해야 할 키도, 띄워야 할 컨테이너도 없습니다. 인스톨러가 GHCR에서 공식 이미지 두 개(컨트롤 플레인 + 대시보드)를 pull하고, .env에 랜덤 키를 만들고, docker compose로 띄운 뒤 Claude Code에 바로 배선합니다.",
  checklist: [
    {
      label: "Docker만 있으면 OK",
      detail: "Docker + Compose v2가 전부입니다. 포트 충돌 시 인스톨러가 3000~3050, 8787~8837 범위에서 빈 포트를 자동 선택합니다.",
    },
    {
      label: "공식 이미지 + 대시보드 동봉",
      detail: "ghcr.io/openmagi/magi-cp (FastAPI) + ghcr.io/openmagi/magi-cp-dashboard (Next.js) 두 컨테이너가 docker compose로 함께 떠서, localhost에서 대시보드까지 그대로 봅니다.",
    },
    {
      label: "Claude Code 자동 배선",
      detail: "~/.claude/managed-settings.json + ~/.local/bin/magi-gate.sh 배치, 키/URL을 ~/.config/magi-cp/env에 0600으로 저장, 셸 rc 자동 소싱.",
    },
  ],
  transcriptTitle: "claude-code · install transcript",
  transcript: {
    lines: [
      { kind: "banner", text: "Open Magi · Control Plane installer (self-host)" },
      { kind: "blank" },
      { kind: "step", text: "Checking Docker" },
      { kind: "ok",   text: "docker 25.0.3  +  compose v2.24.5" },
      { kind: "step", text: "Picking host ports" },
      { kind: "ok",   text: "dashboard → :3000  ·  cloud → :8787" },
      { kind: "step", text: "Downloading docker-compose.yml from https://cp.openmagi.ai" },
      { kind: "ok",   text: "wrote ~/.magi/control-plane/docker-compose.yml" },
      { kind: "step", text: "Generating ~/.magi/control-plane/.env with random keys" },
      { kind: "ok",   text: "wrote .env (0600)" },
      { kind: "step", text: "docker compose up -d (pulling magi-cp + magi-cp-dashboard images)" },
      { kind: "ok",   text: "compose up" },
      { kind: "step", text: "Waiting for /healthz at http://localhost:8787" },
      { kind: "ok",   text: "healthy after 22s" },
      { kind: "step", text: "Wiring Claude Code (managed-settings.json + magi-gate.sh)" },
      { kind: "ok",   text: "rewrote managed-settings → cloud=http://localhost:8787" },
      { kind: "step", text: "Persisting key + cloud URL → ~/.config/magi-cp/env" },
      { kind: "ok",   text: "saved (0600)" },
      { kind: "blank" },
      { kind: "done", text: "Install complete." },
      { kind: "note", text: "Dashboard: http://localhost:3000   API: http://localhost:8787" },
      { kind: "note", text: "Open the dashboard URL in your browser to start." },
    ] as TranscriptLine[],
    caption: "실제 인스톨러 실행 화면 (정상 경로). 포트가 사용 중이면 자동으로 인접 빈 포트를 잡습니다.",
  },
  whatTitle: "스크립트가 정확히 뭘 하나요?",
  whatItems: [
    "Docker + Docker Compose v2 설치 확인 (없으면 install 가이드 출력 후 종료)",
    "3000(대시보드) · 8787(컨트롤 플레인) 포트가 비어있는지 lsof로 확인, 충돌 시 +50 범위 내에서 빈 포트 자동 선택, .env에 영구화",
    "https://cp.cp.openmagi.ai/self-host/docker-compose.yml을 ~/.magi/control-plane/docker-compose.yml로 다운로드 (소스 클론 아님 · 공식 compose 파일만)",
    "랜덤 키 4개 (MAGI_CP_API_KEY, MAGI_CP_HITL_API_KEY, MAGI_CP_ADMIN_API_KEY, MAGI_CP_ADMIN_HMAC_SECRET)를 openssl로 생성, ~/.magi/control-plane/.env에 0600으로 저장 (재실행 시 기존 키 보존)",
    "docker compose up -d → ghcr.io/openmagi/magi-cp + ghcr.io/openmagi/magi-cp-dashboard 이미지 자동 pull",
    "http://localhost:<CLOUD_PORT>/healthz 폴링 (최대 90초)",
    "~/.claude/managed-settings.json + ~/.local/bin/magi-gate.sh 배치, 게이트가 picked 포트의 컨트롤 플레인을 가리키도록 자동 재작성",
    "키 + cloud URL을 ~/.config/magi-cp/env에 0600으로 저장, ~/.zshrc · ~/.bashrc에 자동 소싱 라인 추가",
  ],
  docs: "지원/문의 이메일",
  repoLink: "openmagi GitHub org",
}

const EN_INSTALL = {
  eyebrow: "INSTALL",
  title: "Self-host in one line",
  subtitle: "Docker is the only prerequisite. The installer downloads compose.yml, generates a .env, pulls official images, and wires Claude Code in one flow.",
  commandHint: "Runs entirely on your machine via docker compose. No external hosting dependency. If 3000 / 8787 are in use the installer auto-bumps to a free adjacent port.",
  walkthroughEyebrow: "WHAT HAPPENS",
  walkthroughHeading: "What the installer runs for you",
  walkthroughBody: "No key to copy from a portal first, no container to boot by hand. The installer pulls two official images from GHCR (control plane + dashboard), generates a .env with random keys, brings them up with docker compose, and wires it into Claude Code.",
  checklist: [
    {
      label: "Docker is the only prereq",
      detail: "Docker + Compose v2 is all you need. If 3000 / 8787 collide with other services, the installer scans 3000-3050 and 8787-8837 for a free port automatically.",
    },
    {
      label: "Official images + dashboard included",
      detail: "ghcr.io/openmagi/magi-cp (FastAPI) and ghcr.io/openmagi/magi-cp-dashboard (Next.js) come up together via docker compose; you get the full dashboard on localhost.",
    },
    {
      label: "Claude Code auto-wired",
      detail: "Drops ~/.claude/managed-settings.json + ~/.local/bin/magi-gate.sh, persists the key + URL to ~/.config/magi-cp/env (0600), auto-sources from shell rcs.",
    },
  ],
  transcriptTitle: "claude-code · install transcript",
  transcript: {
    lines: [
      { kind: "banner", text: "Open Magi · Control Plane installer (self-host)" },
      { kind: "blank" },
      { kind: "step", text: "Checking Docker" },
      { kind: "ok",   text: "docker 25.0.3  +  compose v2.24.5" },
      { kind: "step", text: "Picking host ports" },
      { kind: "ok",   text: "dashboard → :3000  ·  cloud → :8787" },
      { kind: "step", text: "Downloading docker-compose.yml from https://cp.openmagi.ai" },
      { kind: "ok",   text: "wrote ~/.magi/control-plane/docker-compose.yml" },
      { kind: "step", text: "Generating ~/.magi/control-plane/.env with random keys" },
      { kind: "ok",   text: "wrote .env (0600)" },
      { kind: "step", text: "docker compose up -d (pulling magi-cp + magi-cp-dashboard images)" },
      { kind: "ok",   text: "compose up" },
      { kind: "step", text: "Waiting for /healthz at http://localhost:8787" },
      { kind: "ok",   text: "healthy after 22s" },
      { kind: "step", text: "Wiring Claude Code (managed-settings.json + magi-gate.sh)" },
      { kind: "ok",   text: "rewrote managed-settings → cloud=http://localhost:8787" },
      { kind: "step", text: "Persisting key + cloud URL → ~/.config/magi-cp/env" },
      { kind: "ok",   text: "saved (0600)" },
      { kind: "blank" },
      { kind: "done", text: "Install complete." },
      { kind: "note", text: "Dashboard: http://localhost:3000   API: http://localhost:8787" },
      { kind: "note", text: "Open the dashboard URL in your browser to start." },
    ] as TranscriptLine[],
    caption: "Actual installer transcript on the happy path. The installer auto-bumps to a free adjacent port if 3000/8787 are in use.",
  },
  whatTitle: "What exactly does the script do?",
  whatItems: [
    "Verifies Docker + Docker Compose v2 are installed (prints install hint and exits otherwise)",
    "Checks ports 3000 (dashboard) and 8787 (control plane) with lsof; on conflict, scans 50 ports up and writes the chosen ports to .env",
    "Downloads https://cp.cp.openmagi.ai/self-host/docker-compose.yml to ~/.magi/control-plane/docker-compose.yml (just the compose file; no source code is fetched)",
    "Generates four random keys (MAGI_CP_API_KEY, MAGI_CP_HITL_API_KEY, MAGI_CP_ADMIN_API_KEY, MAGI_CP_ADMIN_HMAC_SECRET) via openssl into ~/.magi/control-plane/.env (0600); existing keys are preserved on re-run",
    "docker compose up -d pulls ghcr.io/openmagi/magi-cp + ghcr.io/openmagi/magi-cp-dashboard automatically",
    "Polls http://localhost:<CLOUD_PORT>/healthz for up to 90s",
    "Drops ~/.claude/managed-settings.json + ~/.local/bin/magi-gate.sh and rewrites the gate command to point at the picked port",
    "Persists the key + cloud URL to ~/.config/magi-cp/env (0600) and adds an auto-source line to ~/.zshrc + ~/.bashrc",
  ],
  docs: "Support / contact",
  repoLink: "openmagi GitHub org",
}
