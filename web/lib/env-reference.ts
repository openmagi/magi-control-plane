/**
 * D78: typed list of every MAGI_CP_* env var the operator might encounter.
 *
 * Single source for the /docs/env-reference page. Each entry pairs a one-line
 * description in Korean and English with the default value and an optional
 * "allowed values" hint. The descriptions are intentionally one line so the
 * reference table stays scannable; deep dives belong in the topic pages.
 *
 * Drift gates (env-reference.test.ts):
 *  1. Spot-check: a hand-curated REQUIRED list of names that doc pages
 *     explicitly cite must remain present.
 *  2. Bidirectional grep gate: every `MAGI_CP_*` name found in
 *     `src/magi_cp/` and `web/` (excluding the env-reference file itself
 *     and its test) must appear in `ENV_REFERENCE`, and every
 *     `MAGI_CP_*` name documented here must be referenced somewhere in
 *     the same scan. New env vars added in source without a matching
 *     row fail the test loudly.
 */
export interface EnvVarEntry {
  /** Full env var name. */
  name: string
  /** One of: cloud | local | dashboard | provider. Used for grouping. */
  group: "cloud" | "local" | "dashboard" | "provider"
  /** Default value when unset; literal "(required)" when there is no safe default. */
  default: string
  /** Hint about allowed values (e.g. "URL", "uuid", "0|1"). Optional. */
  allowed?: string
  /** Korean one-liner. */
  ko: string
  /** English one-liner. */
  en: string
}

export const ENV_REFERENCE: ReadonlyArray<EnvVarEntry> = [
  // ── cloud (control-plane server) ─────────────────────────────────────────
  {
    name: "MAGI_CP_API_KEY",
    group: "cloud",
    default: "(required)",
    allowed: "uuid or hex token",
    ko: "테넌트 API 키. 플러그인이 클라우드에 붙을 때 사용합니다.",
    en: "Tenant API key. The plugin uses it to call the control plane.",
  },
  {
    name: "MAGI_CP_ADMIN_API_KEY",
    group: "cloud",
    default: "(required)",
    allowed: "uuid or hex token",
    ko: "관리자 API 키. 정책 생성·삭제, 팩 토글 등 변경 작업에 필요합니다.",
    en: "Admin API key. Required for create/delete and pack toggle calls.",
  },
  {
    name: "MAGI_CP_HITL_API_KEY",
    group: "cloud",
    default: "(required)",
    allowed: "uuid or hex token",
    ko: "HITL(리뷰 대기열) 작업용 API 키. 리뷰 승인·반려에 사용합니다.",
    en: "HITL queue API key. Used to approve or reject pending reviews.",
  },
  {
    name: "MAGI_CP_ADMIN_HMAC_SECRET",
    group: "cloud",
    default: "(required)",
    allowed: "32+ byte hex",
    ko: "Stripe webhook 등 외부 시스템에서 들어오는 admin POST의 HMAC 서명 키.",
    en: "HMAC signing secret for admin POSTs from external systems (Stripe, etc).",
  },
  {
    name: "MAGI_CP_METRICS_TOKEN",
    group: "cloud",
    default: "(unset: /metrics has no auth)",
    allowed: "opaque token",
    ko: "설정하면 /metrics가 `Authorization: Bearer <token>`를 요구합니다. 노출된 인스턴스에서 테넌트 메타데이터 유출을 막습니다.",
    en: "When set, /metrics requires `Authorization: Bearer <token>`. Prevents tenant metadata leakage on an exposed instance.",
  },
  {
    name: "MAGI_CP_DASHBOARD_SESSION_SECRET",
    group: "dashboard",
    default: "(falls back to MAGI_CP_ADMIN_HMAC_SECRET)",
    allowed: "32+ byte secret",
    ko: "대시보드 콘솔 세션 쿠키의 서명 키. 미설정 시 비-loopback 콘솔 접근은 fail-closed(거부)됩니다.",
    en: "Signing key for the dashboard console session cookie. When unset, non-loopback console access fails closed (denied).",
  },
  {
    name: "MAGI_CP_TRUST_LOOPBACK_HEADER",
    group: "dashboard",
    default: "0",
    allowed: "0|1",
    ko: "기본 0(fail-closed): 모든 콘솔 요청에 세션을 요구합니다. host 헤더는 위조 가능하므로, loopback 전용 바인딩임을 아는 로컬 운영자만 1로 설정해 로그인 없는 편의를 켭니다.",
    en: "Default 0 (fail-closed): every console request requires a session. The host header is spoofable, so only a local operator who knows the bind is loopback-only sets 1 to enable the no-login convenience.",
  },
  {
    name: "MAGI_CP_ISSUER",
    group: "cloud",
    default: "magi-control-plane",
    ko: "토큰 발급자 식별 문자열. 멀티테넌트일 때만 의미가 있습니다.",
    en: "Token issuer string. Only meaningful when running multi-tenant.",
  },
  {
    name: "MAGI_CP_DSN",
    group: "cloud",
    default: "sqlite:///./magi-cp.sqlite",
    allowed: "SQLAlchemy DSN",
    ko: "DB DSN. 단일 자체호스트는 sqlite, 호스티드는 Postgres를 권장합니다.",
    en: "Database DSN. SQLite for single-host installs, Postgres for hosted.",
  },
  {
    name: "MAGI_CP_KEY_DIR",
    group: "cloud",
    default: "./_devdb/keys",
    ko: "토큰 서명에 사용할 키 디렉터리.",
    en: "Directory for token signing keys.",
  },
  {
    name: "MAGI_CP_POLICY_STORE",
    group: "cloud",
    default: "sqlite",
    allowed: "sqlite | file",
    ko: "정책 저장소 구현체. 기본 sqlite, 파일 백업이 필요하면 file.",
    en: "Policy store backend. Defaults to sqlite; use file for flat-file backups.",
  },
  {
    name: "MAGI_CP_POLICY_STORE_PATH",
    group: "cloud",
    default: "./_devdb/policies",
    ko: "POLICY_STORE=file 일 때 정책 파일을 둘 디렉터리.",
    en: "Directory for policy files when POLICY_STORE=file.",
  },
  {
    name: "MAGI_CP_PACK_STORE",
    group: "cloud",
    default: "./_devdb/packs",
    ko: "팩 메타데이터(=토글 상태)를 저장할 디렉터리.",
    en: "Directory for pack metadata (toggle state).",
  },
  {
    name: "MAGI_CP_CUSTOM_VERIFIER_STORE",
    group: "cloud",
    default: "./_devdb/custom_verifiers",
    ko: "사용자 정의 verifier 저장소.",
    en: "Custom verifier store directory.",
  },
  {
    name: "MAGI_CP_SCRIPT_STORE_DIR",
    group: "cloud",
    default: "./_devdb/scripts",
    ko: "run_command 정책이 실행할 첨부 스크립트를 보관하는 디렉터리.",
    en: "Directory holding attached scripts that run_command policies execute.",
  },
  {
    name: "MAGI_CP_RUN_COMMAND_LEDGER",
    group: "cloud",
    default: "./_devdb/run_command_ledger.jsonl",
    ko: "run_command 호출 감사 로그 파일.",
    en: "JSONL audit log for run_command invocations.",
  },
  {
    name: "MAGI_CP_ALLOW_RUN_COMMAND",
    group: "cloud",
    default: "0",
    allowed: "0 | 1",
    ko: "run_command 액션을 전역 허용할지 결정. 기본 차단(0).",
    en: "Globally allow run_command actions. Default off (0).",
  },
  {
    name: "MAGI_CP_REQUIRE_SIGNED_RUN_COMMAND_SPEC",
    group: "cloud",
    default: "0",
    allowed: "0 | 1",
    ko: "run_command 스펙에 서명을 강제할지 여부. 호스티드는 1 권장.",
    en: "Require signed run_command specs. Recommended on for hosted.",
  },
  {
    name: "MAGI_CP_STRICT_SHACL_TARGETS",
    group: "cloud",
    default: "1",
    allowed: "0 | 1",
    ko: "SHACL verifier가 target 노드를 발견하지 못하면 즉시 실패할지 여부.",
    en: "Fail SHACL verifiers immediately when no target nodes match.",
  },
  {
    name: "MAGI_CP_CONTEXT_TEMPLATES_DIR",
    group: "cloud",
    default: "./_devdb/context_templates",
    ko: "inject_context 액션에서 끼워 넣을 템플릿 모음 디렉터리.",
    en: "Directory of templates used by inject_context actions.",
  },
  {
    name: "MAGI_CP_PACK_CENTRIC_RUNTIME",
    group: "cloud",
    default: "1",
    allowed: "0 | 1",
    ko: "팩 중심·세션 스코프 정책 런타임(P5부터 기본 켜짐). 부팅 마이그레이션이 enabled 정책을 floor 팩으로 옮깁니다. 0으로 설정하면 기존 per-policy enabled 경로로 롤백합니다.",
    en: "Pack-centric, session-scoped policy runtime (default ON since P5). The boot migration moves enabled policies into the floor pack. Set to 0 to roll back to the legacy per-policy enabled path.",
  },
  {
    name: "MAGI_CP_CODEX_RUNTIME_ENABLED",
    group: "cloud",
    default: "1",
    allowed: "0 | 1",
    ko: "Codex CLI 런타임 어댑터의 전역 가용성 스위치(기본 켜짐). 켜져 있으면 대시보드 런타임 선택기가 Codex 전환을 허용합니다. 단 이것은 가용성만 켤 뿐이고, 테넌트는 runtime_id가 codex일 때만 실제로 Codex 경로를 탑니다. 명시적 falsy 값(0/false/off)으로 설정하면 어댑터가 꺼지고 Claude Code 경로가 바이트 동일합니다.",
    en: "Global availability switch for the Codex CLI runtime adapter (default ON). When on, the dashboard runtime picker allows a switch to Codex; this only makes Codex selectable (a tenant reaches the Codex path only when its runtime_id is codex). Set an explicit falsy value (0/false/off) to disable the adapter, after which the Claude Code path is byte-identical.",
  },
  {
    name: "MAGI_CP_RUNTIME",
    group: "local",
    default: "(auto)",
    allowed: "cc | codex",
    ko: "게이트 디스패처가 어떤 런타임 표준입력을 파싱할지 강제 지정. Codex managed config가 이 값을 codex로 고정합니다. 없으면 페이로드 스니핑 후 CC로 폴백.",
    en: "Forces which runtime stdin the gate dispatcher parses. Codex managed config pins this to codex; unset falls back to payload sniff then CC.",
  },
  {
    name: "MAGI_CP_CODEX_ETC_DIR",
    group: "local",
    default: "/etc/codex",
    ko: "Codex managed config(requirements.toml 등)를 설치할 디렉터리 오버라이드. 테스트나 rootless 설치에서 쓰기 가능한 경로로 바꿉니다.",
    en: "Override for the Codex managed-config directory (requirements.toml, etc). Point at a writable path for tests or rootless installs.",
  },
  {
    name: "MAGI_CP_INSTALL_RUNTIME",
    group: "local",
    default: "auto",
    allowed: "auto | cc | codex | both",
    ko: "install.sh가 어떤 런타임 어댑터를 설치할지 선택. auto는 codex 바이너리가 있으면 Codex 어댑터도 설치.",
    en: "Selects which runtime adapter install.sh installs. auto also installs the Codex adapter when the codex binary is present.",
  },
  {
    name: "MAGI_CP_INSTALL_CODEX_ONLY",
    group: "local",
    default: "0",
    allowed: "0 | 1",
    ko: "install.sh가 Codex 어댑터만 설치하도록 강제(CC 설치 건너뜀).",
    en: "Forces install.sh to install only the Codex adapter (skips the CC install).",
  },
  {
    name: "MAGI_CP_LLM_COMPILER",
    group: "cloud",
    default: "(unset)",
    allowed: "module:object",
    ko: "자연어 → IR 컴파일러 LLM 제공자. 끄면 대화형 정책 작성기가 비활성.",
    en: "LLM provider for NL→IR compile. When unset, conversational authoring is disabled.",
  },
  {
    name: "MAGI_CP_LLM_REVIEWER",
    group: "cloud",
    default: "(unset)",
    allowed: "module:object",
    ko: "IR 리뷰 단계용 LLM. 컴파일 결과를 검수해 위험한 IR을 잡아냅니다.",
    en: "LLM provider for the IR review step. Catches dangerous compile output.",
  },
  {
    name: "MAGI_CP_STALE_ENDPOINT_SECONDS",
    group: "cloud",
    default: "300",
    allowed: "integer seconds",
    ko: "엔드포인트 하트비트가 이 시간 동안 없으면 stale 로 표시합니다.",
    en: "Mark endpoints stale after this many seconds of silence.",
  },
  {
    name: "MAGI_CP_HEARTBEAT_MIN_INTERVAL",
    group: "cloud",
    default: "10",
    allowed: "integer seconds",
    ko: "엔드포인트 하트비트의 최소 허용 간격(초).",
    en: "Minimum allowed heartbeat interval in seconds.",
  },
  {
    name: "MAGI_CP_ACCEPT_LEGACY_TOKEN_SHAPE_UNTIL",
    group: "cloud",
    default: "(unset)",
    allowed: "ISO8601",
    ko: "구 토큰 포맷을 받아들이는 마이그레이션 데드라인.",
    en: "Migration deadline for accepting legacy token shapes.",
  },
  {
    name: "MAGI_CP_ALLOW_NO_REGISTRY",
    group: "cloud",
    default: "0",
    allowed: "0 | 1",
    ko: "verifier 레지스트리 없이도 부팅 허용. 개발용.",
    en: "Allow boot without a verifier registry. Dev only.",
  },
  {
    name: "MAGI_CP_REQUIRE_REGISTRY",
    group: "cloud",
    default: "1",
    allowed: "0 | 1",
    ko: "verifier 레지스트리 부재 시 부팅 실패. 운영용 안전장치.",
    en: "Fail boot when no verifier registry is present. Production safety.",
  },
  {
    name: "MAGI_CP_SHARE_BASE_URL",
    group: "cloud",
    default: "(derived)",
    ko: "magi-cp share 가 생성하는 공개 URL의 베이스. cloud.openmagi.ai 가 기본.",
    en: "Base URL that magi-cp share embeds in public links. Defaults to cloud.openmagi.ai.",
  },
  {
    name: "MAGI_CP_SHARE_TTL_SECONDS",
    group: "cloud",
    default: "604800",
    allowed: "integer seconds",
    ko: "공유 런 링크의 기본 만료 시간(초). 기본 7일.",
    en: "Default expiry for shared run links, in seconds. 7 days by default.",
  },

  // ── local (CC plugin / on-laptop gate) ───────────────────────────────────
  {
    name: "MAGI_CP_LOCAL_DIR",
    group: "local",
    default: "~/.config/magi-cp",
    ko: "플러그인이 정책 캐시·HITL 큐를 둘 로컬 디렉터리.",
    en: "Local directory where the plugin caches policies and HITL queue.",
  },
  {
    name: "MAGI_CP_CLOUD_URL",
    group: "local",
    default: "https://cloud.openmagi.ai",
    allowed: "URL",
    ko: "플러그인이 가리킬 클라우드 URL. 자체 호스트면 자기 IP.",
    en: "Cloud URL the plugin should call. Set to your own host for self-host.",
  },
  {
    name: "MAGI_CP_MANAGED_SETTINGS_PATH",
    group: "local",
    default: "(derived)",
    ko: "managed-settings.json 이 쓸 경로. 기본은 CC 표준 위치.",
    en: "Where managed-settings.json is written. Defaults to CC's standard location.",
  },
  {
    name: "MAGI_CP_ENDPOINT_ID",
    group: "local",
    default: "(generated)",
    ko: "이 머신을 식별하는 ID. 비우면 hostname+UUID 로 자동 생성.",
    en: "Stable id for this machine. Auto-derived from hostname+UUID when unset.",
  },
  {
    name: "MAGI_CP_ENDPOINT_LABEL",
    group: "local",
    default: "(hostname)",
    ko: "엔드포인트 페이지에서 보일 사람-친화 이름.",
    en: "Human-friendly label shown on the endpoints page.",
  },
  {
    name: "MAGI_CP_AGENT_VERSION",
    group: "local",
    default: "(plugin version)",
    ko: "보고할 에이전트 버전 문자열. 보통 비워둡니다.",
    en: "Agent version string to report. Usually leave unset.",
  },
  {
    name: "MAGI_CP_ALLOW_PLAIN_HTTP",
    group: "local",
    default: "0",
    allowed: "0 | 1",
    ko: "HTTPS가 아닌 클라우드 URL 도 허용. 로컬 테스트 외에는 끄세요.",
    en: "Allow non-HTTPS cloud URLs. Keep off outside local tests.",
  },
  {
    name: "MAGI_CP_STATE_DIR",
    group: "local",
    default: "~/.magi-cp/state",
    ko: "게이트 캐시 무효화 신호 파일이 놓일 디렉터리. 세션 팩 활성화가 여기에 신호를 남깁니다.",
    en: "Directory for the gate's cache-invalidation signal files. Session pack activation touches sentinels here.",
  },
  {
    name: "MAGI_CP_STICKY_PACKS_FILE",
    group: "local",
    default: "~/.magi-cp/sticky-packs.json",
    ko: "프로젝트별 sticky 팩 목록 파일. 세션 부팅 시 자동 재활성화할 팩을 기록합니다.",
    en: "Per-project sticky pack list. Records packs that auto-reactivate on session boot.",
  },
  {
    name: "MAGI_CP_SESSION_ID",
    group: "local",
    default: "(resolved)",
    ko: "현재 Claude Code 세션 id. session pack CLI 가 없으면 상태 파일에서 읽습니다.",
    en: "Current Claude Code session id. The session pack CLI falls back to the state file when unset.",
  },
  {
    name: "MAGI_CP_SESSION_FILE",
    group: "local",
    default: "$MAGI_CP_STATE_DIR/session.json",
    ko: "게이트가 마지막으로 본 세션 id 를 기록하는 파일. CLI 는 여기서 세션 id 를 읽습니다.",
    en: "File the gate writes the last-seen session id to. The CLI reads the session id from here.",
  },
  {
    name: "MAGI_CP_TENANT_ID",
    group: "local",
    default: "default",
    ko: "로컬 캐시 신호를 키잉할 테넌트 id. 단일 테넌트 셀프호스트에서는 default 입니다.",
    en: "Tenant the local cache sentinel is keyed on. In the single-tenant self-host it is default.",
  },
  {
    name: "MAGI_CP_INSTALL_COMMANDS_ONLY",
    group: "local",
    default: "0",
    allowed: "0 | 1",
    ko: "설치 스크립트를 슬래시 커맨드 파일만 떨구고 종료하는 모드로 실행(테스트/재실행용).",
    en: "Run the installer in a mode that drops only the slash command files and exits (test / re-run helper).",
  },

  // ── dashboard (Next.js web) ─────────────────────────────────────────────
  {
    name: "MAGI_CP_DASH_PORT",
    group: "dashboard",
    default: "3000",
    allowed: "integer 1-65535",
    ko: "install.sh / quickstart.sh 가 대시보드 컨테이너에 매핑할 포트 (기본 3000, 충돌 시 자동 +50 범위 탐색).",
    en: "Port that install.sh / quickstart.sh map the dashboard container to (default 3000; auto-scans +50 on collision).",
  },
  {
    name: "MAGI_CP_CLOUD_PORT",
    group: "dashboard",
    default: "8787",
    allowed: "integer 1-65535",
    ko: "클라우드(API) 포트.",
    en: "Cloud (API) port.",
  },
  {
    name: "MAGI_CP_PUBLIC_CLOUD_URL",
    group: "dashboard",
    default: "(derived)",
    allowed: "URL",
    ko: "공개 마케팅 페이지가 광고할 클라우드 URL.",
    en: "Public cloud URL advertised on marketing pages.",
  },
  {
    name: "MAGI_CP_PUBLIC_SITE_URL",
    group: "dashboard",
    default: "https://openmagi.ai",
    allowed: "URL",
    ko: "사이트의 공개 베이스 URL. 메타태그/링크에 사용.",
    en: "Public base URL of the site. Used in meta tags and links.",
  },
  {
    name: "MAGI_CP_SITE_URL",
    group: "dashboard",
    default: "(derived)",
    allowed: "URL",
    ko: "내부에서 대시보드가 자기 자신을 어떻게 부를지.",
    en: "How the dashboard refers to itself internally.",
  },
  {
    name: "MAGI_CP_INSTALL_DIR",
    group: "dashboard",
    default: "(unset)",
    ko: "install.sh 가 풀어 놓는 디렉터리 힌트. 기본은 사용자가 선택.",
    en: "Hint for where install.sh extracts. Defaults to user choice.",
  },
  {
    name: "MAGI_CP_MARKETING_ONLY",
    group: "dashboard",
    default: "0",
    allowed: "0 | 1",
    ko: "콘솔을 끄고 마케팅 라우트만 노출. cloud.openmagi.ai 미러용.",
    en: "Hide console routes and expose only marketing. Used for the public site mirror.",
  },

  // ── provider hints ──────────────────────────────────────────────────────
  {
    name: "ANTHROPIC_API_KEY",
    group: "provider",
    default: "(required for compiler)",
    ko: "MAGI_CP_LLM_COMPILER=anthropic_default 일 때 필요.",
    en: "Required when MAGI_CP_LLM_COMPILER=anthropic_default.",
  },
  {
    name: "ANTHROPIC_MODEL",
    group: "provider",
    default: "(provider default)",
    ko: "anthropic_default 가 사용할 모델 id 오버라이드. 비우면 provider 기본 모델.",
    en: "Override the model id used by anthropic_default. Unset means provider default.",
  },
  {
    name: "OPENAI_API_KEY",
    group: "provider",
    default: "(required for reviewer)",
    ko: "MAGI_CP_LLM_REVIEWER=openai_default 일 때 필요.",
    en: "Required when MAGI_CP_LLM_REVIEWER=openai_default.",
  },
  {
    name: "OPENAI_MODEL",
    group: "provider",
    default: "(provider default)",
    ko: "openai_default 가 사용할 모델 id 오버라이드. 비우면 provider 기본 모델.",
    en: "Override the model id used by openai_default. Unset means provider default.",
  },
  {
    name: "CLAUDE_PROJECTS_DIR",
    group: "provider",
    default: "~/.claude/projects",
    ko: "magi-cp share 가 Claude Code 세션 자료를 읽어들이는 디렉터리.",
    en: "Directory `magi-cp share` reads Claude Code session data from.",
  },
] as const

/** Group → entries lookup; used to render the table sectioned by surface. */
export function groupEntries(): Record<EnvVarEntry["group"], EnvVarEntry[]> {
  const out: Record<EnvVarEntry["group"], EnvVarEntry[]> = {
    cloud: [], local: [], dashboard: [], provider: [],
  }
  for (const e of ENV_REFERENCE) out[e.group].push(e)
  return out
}
