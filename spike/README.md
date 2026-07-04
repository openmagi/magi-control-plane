# magi-control-plane — PoC spike

> 2026-06-18~19. 전략 = clawy `docs/notes/2026-06-18-magi-governance-control-plane-decision.md`.
> v0 plan = clawy `2026-06-18-magi-control-plane-v0-plan (private planning repo)` (§8 컨트롤플레인 아키텍처).
> magi-agent OSS와 sibling(별도 프로젝트). 본 spike는 검증 끝나면 별도 리포로 분리.

## 검증 상태 (전부 PASS)

| M | 무엇 | 상태 |
|---|---|---|
| **M0** | managed-forced 비우회성 (실 Claude Code, 우회 매트릭스 8개) | ✅ 실증 |
| **M1** | `verify_citations` — 사건번호 존재(결정론, 가짜 100% 차단) + verbatim + 한국 인용형식 정규화 | ✅ |
| **B0** | FP stress: naive verbatim 50% FP → **3-way verdict(ok/review/deny)** 설계 교정 | ✅ |
| **B1-lite** | 사건번호 파서 robustness (pre-2000/헌가/헌바, 8/8) | ✅ |
| **B1-live** | 진짜 law.go.kr(OC=clawy) 판례로 verify_citations 실측 → SourceResolver 추상화 결함 발견·수정 | ✅ |
| **M3** | end-to-end 단일프로세스 sim (가짜차단·swap·tamper·정당통과·감사) | ✅ |
| **M4** | 진짜 cloud↔local 분리 — Ed25519 비대칭 + HTTP, F1 모순 구조적 해소 | ✅ |
| **M5** | 실 CC PreToolUse hook이 cloud-signed 토큰 검증 (sudo install 필요) | ✅ helper 단위테스트 PASS, 통합은 M5-DEMO.md |
| **M6** | Policy IR + 결정론 컴파일러 (IR → managed-settings.json, LLM 없음) | ✅ |

## 파일 지도

### 런타임 코어
| 파일 | 책임 |
|---|---|
| `verify_citations.py` | 결정론 verifier — exists / verbatim / SourceResolver / 3-way verdict |
| `cloud_signer.py` | **클라우드 서버** — Ed25519 private 소유, `/citation_verify`·`/pubkey`·`/ledger` HTTP, hash-chain ledger. `serve`/`keygen` |
| `local_gate.py` | **로컬 게이트/emit** — public key fetch, WAL, file_court_gate, CLI(`gate`/`emit`) — magi-gate.sh가 호출 |
| `magi-gate.sh` | CC PreToolUse hook entrypoint — sentinel 파싱 후 local_gate.py 위임 |
| `managed-settings.json` | **빌드 산출물** (`build.sh`로 생성, hand-edit 금지) |
| `policies/legal_filing_v1.json` | Policy IR — 한국 법률 filing 정책 |
| `policy_ir.py` | Policy IR 스키마 + 결정론 컴파일러 + selftest |
| `build.sh` | `policies/*.json` → `managed-settings.json` 컴파일 |
| `install.sh` | sudo install — hook·helper·managed-settings 배포 |

### 데모 / 테스트
| 파일 | 책임 |
|---|---|
| `m3_demo.py` | 단일프로세스 sim (가짜·swap·tamper·정당) |
| `m4_demo.py` | 진짜 cloud↔local 분리 (HTTP, Ed25519) |
| `b0_fp_stress.py` | verbatim FP stress (synthetic) — 3-way 설계 도출 |
| `b1_parser_check.py` | 사건번호 파서 robustness (pre-2000 등) |
| `b1_live.py` | law.go.kr 실데이터로 verify_citations 실측 |
| `check-perms.sh` | non-root 변조 차단 자동 검사 (4·5·8) |
| `M5-DEMO.md` | 실 CC 통합 데모 시퀀스 (sudo + claude -p) |

### 폐기
- `make-evidence.sh` (대칭 HMAC, F1 모순) — M4 이후 사용 안 함. 정리시 삭제 권장.

## 빠른 흐름 (개발자용)

```bash
# 정책 변경
vi policies/legal_filing_v1.json && ./build.sh

# 단위 검증 (sudo 불필요)
python3 policy_ir.py selftest
python3 verify_citations.py
python3 b1_live.py
python3 cloud_signer.py serve &   # 백그라운드
python3 m4_demo.py
kill %1

# 실 CC 통합 (sudo 1회)
sudo ./install.sh
# Terminal 1
python3 cloud_signer.py serve
# Terminal 2 — M5-DEMO.md 시퀀스
```

## 다음 작업 (자율 검증 끝, 빌드/GTM)

1. **CC plugin 패키징** (현재는 raw hook + sudo. 진짜 제품은 `.claude-plugin` 마켓플레이스 번들)
2. **MCP 어댑터** — `public-data-worker`의 law.go.kr fetch를 stdio MCP 서버로 노출 (`mcp__lbox__fetch` / `mcp__magi__verify_citations`)
3. **HITL queue + 대시보드 v0** — review→사람승인→서명
4. **Auth/billing/Org** — license 만료 = 정책번들 만료 (§8.5)
5. **별도 리포 분리** — agent-agnostic 메시지·라이선스 경계
6. **GTM** — 첫 design partner(법률) 컨택

## 정직한 잔여

- 통합 데모(M5)는 sudo install + 별도 터미널 클라우드 필요 — 진짜 배포는 launchd/systemd + cloud SaaS.
- review→HITL 흐름은 helper에 없음 (cloud verdict=review면 토큰 미발행만; UI/queue v1).
- NLI advisory 미통합(B0 review 강등이 결정론까지만, 의미검증 v1).
- Codex / OpenCode 어댑터 없음 (v1.x).
