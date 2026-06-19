#!/usr/bin/env python3
"""
magi-control-plane M6 — Policy IR + 결정론 컴파일러.

원칙(§8.1): 저작은 (정책팩/구조화빌더/인간검토 NL어시스트) 셋 다 *결정론 IR*로 떨어지고,
컴파일러는 LLM 없이 host 표면(CC managed-settings + plugin hook)을 *생성*.

IR 스키마:
  Policy = {
    id, version, description,
    trigger: { host: 'claude-code', event: 'PreToolUse', matcher: 'Bash' },
    sentinel_re: str,              # tool_input.command에서 추출할 정규식; named (?P<matter>) (?P<doc_id>)
    requires: [ { step, verdict } ], # ledger에 어떤 evidence 토큰이 'pass'로 있어야 하나
    on_missing: 'deny' | 'ask',
    on_signature_invalid: 'deny',
    gate_binary: '/usr/local/bin/magi-gate.sh',
  }

컴파일 산출물 (host 표면):
  - managed-settings.json (allowManagedHooksOnly + PreToolUse hooks 엔트리)
  - 향후 (v1): plugin 매니페스트, MCP 어댑터 매니페스트

컴파일러 보장:
  - LLM 호출 0 (순수 함수)
  - 입력=IR, 출력=결정론 (같은 입력 → 같은 출력)
  - 정책 N개 → hooks 배열에 N개 엔트리, matcher precedence는 IR 순서 유지
"""
from __future__ import annotations
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Literal


@dataclass
class Trigger:
    host: Literal["claude-code"] = "claude-code"
    event: Literal["PreToolUse", "PostToolUse", "Stop"] = "PreToolUse"
    matcher: str = "Bash"      # CC tool name or pattern (e.g. "mcp__court__file")


@dataclass
class EvidenceReq:
    step: str                  # e.g. "citation_verify"
    verdict: str = "pass"      # 토큰의 verdict 필드와 정확 일치


@dataclass
class Policy:
    id: str
    description: str
    trigger: Trigger
    sentinel_re: str           # named groups: (?P<matter>...)(?P<doc_id>...)
    requires: list[EvidenceReq]
    on_missing: Literal["deny", "ask"] = "deny"
    on_signature_invalid: Literal["deny"] = "deny"
    gate_binary: str = "/usr/local/bin/magi-gate.sh"
    version: str = "0.1"

    def validate(self) -> None:
        import re
        # named-group 존재 확인 (host에 도달하기 전에 인간 검토 게이트와 함께 강제 실패)
        rx = re.compile(self.sentinel_re)
        if "matter" not in rx.groupindex or "doc_id" not in rx.groupindex:
            raise ValueError(f"policy '{self.id}': sentinel_re는 named groups (?P<matter>) (?P<doc_id>) 필요")
        if self.trigger.event not in ("PreToolUse", "PostToolUse", "Stop"):
            raise ValueError(f"policy '{self.id}': trigger.event 미지원: {self.trigger.event}")
        if not self.requires:
            raise ValueError(f"policy '{self.id}': requires가 비어 있음 (=강제 의미 없음)")


def load_policy(path: str) -> Policy:
    raw = json.load(open(path, "r", encoding="utf-8"))
    p = Policy(
        id=raw["id"],
        description=raw.get("description", ""),
        trigger=Trigger(**raw["trigger"]),
        sentinel_re=raw["sentinel_re"],
        requires=[EvidenceReq(**r) for r in raw["requires"]],
        on_missing=raw.get("on_missing", "deny"),
        on_signature_invalid=raw.get("on_signature_invalid", "deny"),
        gate_binary=raw.get("gate_binary", "/usr/local/bin/magi-gate.sh"),
        version=raw.get("version", "0.1"),
    )
    p.validate()
    return p


def compile_to_managed_settings(policies: list[Policy]) -> dict:
    """결정론 컴파일러: Policy IR → CC managed-settings.json dict."""
    # 1) host별로 정책 검증
    for p in policies:
        p.validate()
        if p.trigger.host != "claude-code":
            raise ValueError(f"policy '{p.id}': host 'claude-code'만 지원(v0)")

    # 2) event별로 그룹핑하여 hooks 배열 구성
    events: dict[str, list[dict]] = {}
    for p in policies:
        ev = p.trigger.event
        events.setdefault(ev, []).append({
            "matcher": p.trigger.matcher,
            "hooks": [{
                "type": "command",
                "command": p.gate_binary,     # sentinel + WAL은 gate_binary가 처리
            }],
        })

    return {
        "allowManagedHooksOnly": True,                # 유저/프로젝트 hook 무력화
        "permissions": {"defaultMode": "default"},
        "hooks": events,
        # 정책 ID는 audit/디버깅용 메타데이터로 별도 키에 보관(공식 키 아님; 무시되어도 무해)
        "_magi_policies": [{"id": p.id, "version": p.version, "description": p.description} for p in policies],
    }


def compile_files(policy_paths: list[str], out_path: str) -> dict:
    policies = [load_policy(p) for p in policy_paths]
    settings = compile_to_managed_settings(policies)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return settings


# ── self-test: 동일 입력 → 동일 출력 (결정론) + IR 회귀 검증 ──────────
def _selftest():
    import re, hashlib
    here = os.path.dirname(os.path.abspath(__file__))
    ir = os.path.join(here, "policies", "legal_filing_v1.json")
    out1 = os.path.join(here, "/tmp/managed1.json")
    out2 = os.path.join(here, "/tmp/managed2.json")
    s1 = compile_files([ir], out1)
    s2 = compile_files([ir], out2)
    h1 = hashlib.sha256(open(out1, "rb").read()).hexdigest()
    h2 = hashlib.sha256(open(out2, "rb").read()).hexdigest()
    assert h1 == h2, "❌ 컴파일러가 비결정론적(동일 IR → 다른 출력)"
    p = load_policy(ir)
    rx = re.compile(p.sentinel_re)
    m = rx.search("echo FILE_COURT_M123_DOC1 motion.pdf")
    assert m and m.group("matter") == "M123" and m.group("doc_id") == "DOC1", "❌ sentinel_re가 sentinel 매칭 실패"
    print("✅ M6 self-test: 결정론 컴파일러 + IR 회귀 통과")
    print(f"   IR: {ir}")
    print(f"   산출 sha256: {h1[:16]}…")
    print(f"   생성된 managed-settings keys: {list(s1.keys())}")
    print(f"   hooks[PreToolUse] 엔트리 수: {len(s1['hooks']['PreToolUse'])}")
    return s1


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "compile":
        out = compile_files(sys.argv[2:-1], sys.argv[-1])
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif len(sys.argv) >= 2 and sys.argv[1] == "selftest":
        _selftest()
    else:
        print("usage:\n  policy_ir.py compile <policy.json> [...] <out.json>"
              "\n  policy_ir.py selftest")
