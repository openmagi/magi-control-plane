# 설치 가이드 / Install Guide

> Alpha pilot. Korean firms primary — English mirrored below.

## 한국어 (Korean)

### 1. 알파 키 받기

`https://cloud.openmagi.ai/welcome` 에서 알파 신청 → 영업일 1일 내 이메일로
`mcp_…` API 키와 본 가이드 링크를 보내드립니다.

### 2. 한 줄 설치

```bash
curl -fsSL https://cloud.openmagi.ai/install.sh | bash -s -- mcp_YOUR_KEY
```

스크립트가 자동으로:

1. `python3.11+` 존재 확인 (없으면 안내)
2. `pip install --user magi-cp` (PyPI GA 전엔 GitHub 소스)
3. `~/.claude/managed-settings.json` + `~/.local/bin/magi-gate.sh` 다운로드
4. 환경변수(`MAGI_CP_API_KEY`, `MAGI_CP_CLOUD_URL`)를 `~/.config/magi-cp/env` 에 저장하고
   `~/.zshrc` / `~/.bashrc` 에 자동 소싱 라인 추가 (0600 권한)
5. 스모크 테스트 실행 — 게이트가 sentinel 명령에 대해 올바르게 deny 하는지 확인

### 3. Claude Code 재시작

`~/.claude/managed-settings.json` 을 Claude Code 가 다시 읽도록 재시작합니다.

### 4. 동작 확인

스모크 테스트는 언제든 다시 실행 가능:

```bash
bash <(curl -fsSL https://cloud.openmagi.ai/install/smoke-test.sh)
```

### 5. 정책 작성

대시보드 `/policies/compile` 에서 자연어로 정책을 작성하거나, `/presets` 에서
한국 법무 도메인용 사전 정의 프리셋을 활성화합니다.

### 트러블슈팅

| 증상 | 원인 / 해결 |
|------|------------|
| `python3.11+ not found` | `brew install python@3.12` (macOS) 또는 `apt install python3.12` (Debian/Ubuntu) |
| `pip install failed` | `/tmp/magi-cp-install.log` 확인. 네트워크 문제 시 회사 프록시 환경변수(`HTTPS_PROXY`) 설정. |
| 스모크 테스트 `cloud unreachable` | `curl https://cloud.openmagi.ai/healthz` 로 확인. 회사 방화벽이 차단 시 IT 팀에 `cloud.openmagi.ai:443` 화이트리스트 요청. |
| 스모크 테스트 `key rejected` | 이메일로 받은 키와 정확히 일치하는지 확인. 만료 시 kevin@openmagi.ai 로 연락. |
| `magi-cp-gate not on PATH` | `~/.local/bin` 이 PATH 에 없음. `~/.zshrc` 에 `export PATH="$HOME/.local/bin:$PATH"` 추가 후 새 셸. |
| Claude Code 가 게이트를 호출하지 않음 | (1) Claude Code 재시작했는지 (2) `~/.claude/managed-settings.json` 의 `hooks.PreToolUse[0].hooks[0].command` 경로가 실제 게이트 실행 파일을 가리키는지. |

---

## English

### 1. Get your alpha key

Apply at `https://cloud.openmagi.ai/welcome`. We email your `mcp_…` API
key and a link to this guide within 1 business day.

### 2. One-line install

```bash
curl -fsSL https://cloud.openmagi.ai/install.sh | bash -s -- mcp_YOUR_KEY
```

The script automatically:

1. Confirms `python3.11+` is on PATH (prints install hint otherwise)
2. `pip install --user magi-cp` (from GitHub source until PyPI GA)
3. Downloads `~/.claude/managed-settings.json` + `~/.local/bin/magi-gate.sh`
4. Persists `MAGI_CP_API_KEY` + `MAGI_CP_CLOUD_URL` to
   `~/.config/magi-cp/env` (0600), wires up `~/.zshrc` / `~/.bashrc`
5. Runs the smoke test — proves the gate correctly DENIES a synthetic
   sentinel command when no signed verifier token is present in the WAL

### 3. Restart Claude Code

So it re-reads `~/.claude/managed-settings.json`.

### 4. Verify install

The smoke test is re-runnable:

```bash
bash <(curl -fsSL https://cloud.openmagi.ai/install/smoke-test.sh)
```

### 5. Author policy

Use the dashboard at `/policies/compile` (natural-language → IR) or
activate a built-in preset at `/presets`.

### Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `python3.11+ not found` | `brew install python@3.12` (macOS) or your distro's `python3.12` package |
| `pip install failed` | check `/tmp/magi-cp-install.log`. Behind corporate proxy → set `HTTPS_PROXY` |
| smoke test `cloud unreachable` | `curl https://cloud.openmagi.ai/healthz`. If blocked, ask IT to whitelist `cloud.openmagi.ai:443` |
| smoke test `key rejected` | confirm key matches the alpha email exactly. If expired, contact kevin@openmagi.ai |
| `magi-cp-gate not on PATH` | add `~/.local/bin` to PATH (`export PATH="$HOME/.local/bin:$PATH"` in `~/.zshrc`) |
| Claude Code doesn't invoke the gate | (1) did you restart Claude Code? (2) does `hooks.PreToolUse[0].hooks[0].command` in `~/.claude/managed-settings.json` point to your gate path? |

---

## How it works (architectural overview)

The gate runs **out of loop** — Claude Code doesn't know it's being
governed. Every `Bash` tool call:

1. Claude Code calls the registered PreToolUse hook (`magi-gate.sh`).
2. The hook reads the command on stdin; sentinel matcher (`FILE_COURT_<subject>_<payload_hash>`)
   determines whether policy applies.
3. If the command is a sentinel, the hook looks at the local WAL
   (`~/.magi-cp/local/wal.jsonl`) for a verifier token bound to
   `(subject, payload_hash)` issued by the cloud after `citation_verify=pass`.
4. Token present + signature valid (Ed25519, key cached locally with kid pinning)
   → exit 0 = allow.
5. Token missing / stale / wrong-kid → JSON deny on stdout = Claude Code
   refuses to run the command.

The user (lawyer) sees this as a Claude Code permission denial with a
plain-Korean reason. They go to `/policies/compile` or `/hitl` to fix
the underlying cause, then retry.

**Cloud unreachable = fail-closed.** This is intentional: license expiry
equals bundle expiry. Lawyers cannot fall back to ungoverned execution
the moment the cloud blips. If you need a soft-fail mode for unit-test
environments, set `MAGI_CP_LOCAL_DIR` to an empty path before launching
Claude Code.
