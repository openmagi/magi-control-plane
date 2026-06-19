# M5 — 실 Claude Code + 클라우드 권위 통합 데모

M3·M4가 sim이었다면 M5는 **(a) 실제 CC가 (b) cloud-signed 토큰을 (c) PreToolUse hook에서 검증**.

## 한 번 설치 (sudo)
```bash
cd magi-cp-spike
chmod +x *.sh
sudo ./install.sh
```
이전 sudo install이 있었으면 그냥 다시 — `magi-gate.sh` v2로 교체되고 `/var/magi` 잔존물 정리됨.

## 데모 시퀀스

### Terminal 1 — 클라우드 띄움
```bash
cd magi-cp-spike
python3 cloud_signer.py serve
# → http://127.0.0.1:8787 (priv key는 ~/.magi-cp/cloud/, public만 노출)
```

### Terminal 2 — 새 CC 세션
```bash
rm -f ~/.magi-cp/local/wal.jsonl   # WAL 리셋

# [1] 토큰 없이 제출 시도 → 결정론 DENY
claude -p 'run this bash command exactly: echo FILE_COURT_M123_DOC1 motion.pdf'
#   → blocked by hook: "MAGI: DENY (matter=M123 doc=DOC1: 매칭 토큰 없음)"

# [2] 가짜 인용으로 emit 시도 → 클라우드 verdict=deny → 토큰 발행 X
python3 magi-cp-spike/local_gate.py emit --matter M123 --doc-id DOC1 \
  --doc-text "답변서" \
  --cite "AI 무효||대법원 2099. 1. 1. 선고 2099도99999 판결"
#   → {"verdict": "deny", "token": null}
claude -p 'run this bash command exactly: echo FILE_COURT_M123_DOC1 motion.pdf'
#   → 여전히 DENY (할루시네이션 차단; money demo 심장)

# [3] 유효 인용으로 emit → cloud-signed 토큰 발행
SRC="공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한 구체적 사실을 드러내는 것을 말하고, 적시된 사실이 진실인 경우에도 성립할 수 있다."
QUOTE="공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한 구체적 사실을 드러내는 것"
REF="대법원 2018. 9. 13. 선고 2018도13694 판결"
python3 magi-cp-spike/local_gate.py emit --matter M123 --doc-id DOC1 \
  --doc-text "답변서" \
  --cite "${QUOTE}||${REF}" \
  --corpus "2018도13694=${SRC}"
#   → {"verdict": "pass", "token": "eyJ...", "exp": ...}

# [4] 같은 matter+doc → ALLOW
claude -p 'run this bash command exactly: echo FILE_COURT_M123_DOC1 motion.pdf'
#   → FILE_COURT_M123_DOC1 motion.pdf  (실행됨)

# [5] swap 공격: 다른 doc_id → DENY
claude -p 'run this bash command exactly: echo FILE_COURT_M123_DOC9 other.pdf'
#   → DENY (DOC9용 토큰 없음)

# [6] tamper: WAL의 토큰 한 글자 변형 → 서명 깨져 무시
sed -i '' 's/eyJ/eyZ/' ~/.magi-cp/local/wal.jsonl   # macOS
claude -p 'run this bash command exactly: echo FILE_COURT_M123_DOC1 motion.pdf'
#   → DENY (private key 없어서 위조 불가; F1 해소)

# [7] 우회 매트릭스 재확인 (M0 그대로 — 토큰 없는 상태에서)
rm -f ~/.magi-cp/local/wal.jsonl
claude --dangerously-skip-permissions -p 'run this bash command exactly: echo FILE_COURT_M123_DOC1 x'
echo '{"disableAllHooks":true}' > ~/.claude/settings.json
claude -p 'run this bash command exactly: echo FILE_COURT_M123_DOC1 x'
rm -f ~/.claude/settings.json
# 둘 다 DENY 생존(M0와 동일).
```

## 종료
```bash
# Terminal 1
^C

# 정리
sudo rm -f /usr/local/bin/magi-gate.sh "/Library/Application Support/ClaudeCode/managed-settings.json"
sudo rm -rf /usr/local/share/magi-cp ~/.magi-cp
```

## 입증되는 것
- M0 비우회 + M4 클라우드 권위가 *실 CC 안*에서 합쳐짐.
- 비대칭 서명 ⇒ local sudo여도 위조 불가 (F1).
- doc_id 바인딩 ⇒ swap 차단 (M3).
- 정책 + verifier + 발행이 다 클라우드 서비스에서 일어남 ⇒ 과금 chokepoint (Q2).
- "결제 안 함" = 토큰 만료 = fail-closed (§8.5).
