#!/usr/bin/env python3
"""
magi-control-plane M1/B0 — verbatim 매칭 false-positive stress test (외부데이터 0).

목적: "진짜 법률 인용이 verbatim 매칭을 어떻게 깨는가"를 통제 변형으로 측정.
실제 판례/서면 인용엔 생략(…)·삽입([])·부분인용·재배열·paraphrase가 흔함.
naive 'contiguous substring'이 *정당한* 인용을 얼마나 막는지(false-positive) +
어떤 변형은 결정론으로 OK, 어떤 변형은 NLI(advisory)로 보내야 하는지 경계를 그림.

→ B1(공개 판례 self-citation 그래프)로 같은 측정을 진짜 N으로 확장.
"""
from __future__ import annotations
from dataclasses import dataclass
from verify_citations import normalize

SOURCE = ("형법 제307조 제1항의 명예훼손죄는 공연히 사실을 적시하여 사람의 사회적 평가를 "
          "저하시킬 만한 구체적 사실을 드러내는 것을 말하고, 적시된 사실이 진실인 경우에도 성립할 수 있다.")

@dataclass
class V:
    text: str
    kind: str          # 변형 종류
    legit: bool        # 정당한 인용인가 (True면 통과해야 정상)
    needs_nli: bool = False  # 결정론 verbatim으론 부적합, NLI로 보내야 정당

# 정당(legit) 변형 = 진짜 서면/판결문에 나오는 합법적 인용 형태
VARIANTS = [
    V("공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한 구체적 사실을 드러내는 것", "exact", True),
    V("  공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한 구체적 사실을  드러내는 것 ", "공백변형", True),
    V("공연히 사실을 적시하여 사람의 사회적 평가를 저하시킬 만한", "부분인용(prefix)", True),
    V("공연히 사실을 적시하여 … 구체적 사실을 드러내는 것", "생략(…)", True),
    V("공연히 사실을 적시하여 [공공연하게] 사람의 사회적 평가를 저하시킬 만한 구체적 사실을 드러내는 것", "삽입([])", True),
    V("구체적 사실을 드러내어 사람의 사회적 평가를 저하시키는 것", "재배열/축약", True),
    V("남의 사회적 평판을 떨어뜨릴 구체적 사실을 드러내면 명예훼손이 성립한다", "paraphrase(원용)", True, needs_nli=True),
    # 부정(illegit) = 막아야 정상
    V("명예훼손죄는 허위사실인 경우에만 성립한다", "의미왜곡(misquote)", False),
    V("인공지능이 작성한 서면은 무효이다", "원문에 없음(날조)", False),
]

def verbatim_ok(quote: str) -> bool:
    return normalize(quote) in normalize(SOURCE)

if __name__ == "__main__":
    print("\n=== B0: verbatim 매칭 false-positive stress test ===\n")
    print(f"{'변형종류':<22}{'정당?':<7}{'verbatim':<10}판정")
    print("-" * 60)
    fp = fn = legit_det = legit_total = 0
    for v in VARIANTS:
        ok = verbatim_ok(v.text)
        if v.legit and not v.needs_nli:
            legit_total += 1
            if ok: legit_det += 1
            else:  fp += 1; verdict = "❌ FALSE POSITIVE (정당한데 막힘)"
            if ok: verdict = "✅ 정상 통과"
        elif v.legit and v.needs_nli:
            verdict = "→ NLI로(verbatim 부적합, 정상)" if not ok else "✅(우연히 substring)"
        else:  # illegit
            if ok: fn += 1; verdict = "❌ FALSE NEGATIVE (날조 통과!)"
            else:  verdict = "✅ 정상 차단"
        print(f"{v.kind:<22}{('정당' if v.legit else '부정'):<7}{('pass' if ok else 'fail'):<10}{verdict}")
    print("-" * 60)
    fpr = fp / legit_total if legit_total else 0
    print(f"\n결정론 verbatim 적용대상(직접인용) {legit_total}건 중:")
    print(f"  정상통과 {legit_det}  /  FALSE POSITIVE {fp}  →  FP율 = {fpr:.0%}")
    print(f"날조/왜곡 차단: FALSE NEGATIVE {fn}건 (0이어야 정상)")
    print(f"\n해석:")
    print("  • 결정론 verbatim이 견고한 영역 = exact·공백·부분인용(contiguous substring).")
    print("  • FALSE POSITIVE 유발 = 생략(…)·삽입([])·재배열  → naive substring으로 부족.")
    print("    → 해법: (a) 생략/삽입 토큰을 인지하는 alignment(여전히 결정론) 또는")
    print("            (b) substring 실패 시 NLI(advisory)로 강등 + 사람 확인.")
    print("  • paraphrase(원용)는 *정당하게* verbatim 실패 → 처음부터 NLI 레이어 담당.")
    print("  • 핵심: '날조(존재X)+의미왜곡'은 결정론으로 100% 차단(FN=0) = catastrophic은 안전,")
    print("          FP(정당한데 막힘)만 줄이면 됨 = NLI 강등으로 해결 가능한 종류.")
