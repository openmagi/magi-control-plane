#!/usr/bin/env python3
"""
B1-live — 진짜 law.go.kr 판례로 verify_citations 실측 (OC=clawy).

시나리오:
  1) 판례 P0의 판결요지(=정당한 인용 원천)에서 문장 하나를 *그대로* 따와 인용으로 사용
  2) P0이 인용한 *참조판례* P_ref를 fetch — 변호사가 실제로 인용할 진짜 ref 텍스트
  3) verify_citations에 (인용텍스트, ref) 쌍을 넣어 ok/review/missing 측정
  4) 가짜·misquote 변형도 같이 → catastrophic 차단 회귀 확인

데이터: 명예훼손 사건(2022도10369)이 참조한 2016도11318.
"""
import json, re, html, urllib.request, urllib.parse
from verify_citations import Citation, verify_citation, normalize

OC = "clawy"
def fetch_prec(prec_id: str) -> dict:
    url = f"http://www.law.go.kr/DRF/lawService.do?OC={OC}&target=prec&ID={prec_id}&type=JSON"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)["PrecService"]

def search_prec(query: str, display: int = 3) -> list[dict]:
    url = f"http://www.law.go.kr/DRF/lawSearch.do?OC={OC}&target=prec&type=JSON&query={urllib.parse.quote(query)}&display={display}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)["PrecSearch"]["prec"]

# law.go.kr 텍스트에 섞이는 마크업/유니코드 청소 → 우리 corpus 형태로
def clean_html(s: str) -> str:
    s = html.unescape(s)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def extract_holding_sentence(judgment: str) -> str:
    """판결요지에서 한 문장(. 로 끝나는) 추출 — 변호사가 따올 법한 단위."""
    t = clean_html(judgment)
    # "[1] ... [2] ..." 마커 제거하고 첫 충분히 긴 문장
    t = re.sub(r"\[\d+\]\s*", "", t)
    for sent in re.split(r"(?<=다\.)\s+", t):
        if 30 < len(sent) < 250:
            return sent.strip()
    return t[:200]

if __name__ == "__main__":
    P0_ID = "612979"   # 2022도10369 (위 검색에서 얻음)
    print(f"\n=== B1-live: 진짜 law.go.kr 판례로 verify_citations 실측 ===\n")
    print(f"[1/4] P0 fetch (판례일련번호 {P0_ID})…")
    p0 = fetch_prec(P0_ID)
    print(f"   사건: {p0['사건번호']} — {clean_html(p0['사건명'])[:60]}…")

    # P0의 참조판례에서 ref 텍스트 추출 (= 변호사가 실제로 쓰는 인용 형태)
    ref_raw = clean_html(p0["참조판례"])
    print(f"   참조판례 원문: {ref_raw[:100]}…")
    ref_match = re.search(r"(대법원\s*\d{2,4}\.\s*\d+\.\s*\d+\.\s*선고\s*(\d{2,4}[가-힣]\d+)\s*판결)", ref_raw)
    ref_text = ref_match.group(1) if ref_match else ref_raw[:60]
    P_REF_NO = ref_match.group(2) if ref_match else None
    print(f"   추출 ref: {ref_text!r}  (사건번호 {P_REF_NO})")

    # P_ref 찾아서 fetch
    print(f"\n[2/4] P_ref({P_REF_NO}) fetch…")
    candidates = search_prec(P_REF_NO, display=3)
    p_ref_id = next((c["판례일련번호"] for c in candidates if c["사건번호"] == P_REF_NO), None)
    if not p_ref_id:
        print(f"   ⚠️ search로 못 찾음, P0 판결요지를 P_ref 원본으로 대용(같은 문장 자체는 P0에 등장)")
        p_ref = p0
    else:
        p_ref = fetch_prec(p_ref_id)
        print(f"   사건: {p_ref['사건번호']} — {clean_html(p_ref['사건명'])[:60]}…")

    # 인용 원본 = P_ref의 판결요지에서 한 문장
    source_text = clean_html(p_ref["판결요지"])
    holding = extract_holding_sentence(p_ref["판결요지"])
    print(f"\n[3/4] 인용원본 문장: {holding!r}")

    # B1-live가 노출한 결함의 해법: 정적 dict가 아니라 resolver(콜러블)로 주입.
    live_corpus = { p_ref["사건번호"]: source_text }
    def live_resolver(case_no: str) -> str | None:
        return live_corpus.get(case_no)   # 실제론 law.go.kr search→hit이면 fetch

    print(f"\n[4/4] verify_citations 실측 (라이브 corpus):\n")
    cases = {
        "① 정당(원문 그대로)": Citation(quote=holding, ref=ref_text),
        "② 정당(부분인용 앞부분)": Citation(quote=holding[:max(20,len(holding)//2)], ref=ref_text),
        "③ 정당(생략 ‘…’ 사용)": Citation(
            quote=holding[:30] + " … " + holding[-30:], ref=ref_text),
        "④ misquote(원문 변형)": Citation(
            quote=holding.replace("판단", "추정") if "판단" in holding else holding[:30]+"는 무효이다",
            ref=ref_text),
        "⑤ 가짜(존재X 사건번호)": Citation(quote=holding[:40], ref="대법원 2099. 1. 1. 선고 2099도99999 판결"),
    }
    print(f"   {'케이스':<25}{'사건번호':<12}{'exists':<8}{'verbatim':<10}{'status':<10}")
    print("   " + "-"*70)
    fp = fn = 0; legit_total = 0
    for label, c in cases.items():
        v = verify_citation(c, resolver=live_resolver)
        legit = label.startswith(("①","②","③"))
        if legit and label != "③ 정당(생략 ‘…’ 사용)":
            legit_total += 1
            if v.status != "ok": fp += 1
        if label.startswith("⑤") and v.status != "missing": fn += 1
        if label.startswith("④") and v.status == "ok":      fn += 1  # misquote가 통과면 FN
        print(f"   {label:<25}{str(v.case_number):<12}{str(v.exists):<8}{str(v.verbatim):<10}{v.status:<10}")
    print("   " + "-"*70)
    print(f"\n결과: 직접인용 적용 정당 {legit_total}건 중 FP {fp}, catastrophic FN {fn}")
    print(f"  생략(…)은 review로 강등 = 정상(B0 설계대로 NLI/사람 검토 위임)")
    print(f"  ⑤ 가짜=missing(하드DENY) → catastrophic 차단 회귀 ✅" if fn==0 else "  ⚠️ catastrophic 회귀 FAIL")
