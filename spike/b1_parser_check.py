#!/usr/bin/env python3
"""
B1-lite — 사건번호 파서 robustness (외부데이터 0, 실제 인용 표기 형태).
파싱 실패 = "존재X" = 하드 DENY = 정당한 인용을 '날조'로 오차단(최악의 false-positive).
"""
from verify_citations import extract_case_number

# (인용 문자열, 기대 사건번호) — 실제 한국 판례 인용에 등장하는 형태들
SAMPLES = [
    ("대법원 2018. 9. 13. 선고 2018도13694 판결", "2018도13694"),
    ("대법원 2009. 4. 23. 선고 2008다29918 판결", "2008다29918"),
    ("대법원 1995. 6. 16. 선고 94다35718 판결", "94다35718"),       # pre-2000 (2자리 연도)
    ("대법원 1999. 2. 24. 선고 99도1234 판결", "99도1234"),          # pre-2000
    ("헌법재판소 2010. 2. 25. 2008헌가23 결정", "2008헌가23"),        # 헌재 (헌가)
    ("헌재 2011. 6. 30. 2009헌바55", "2009헌바55"),                  # 헌재 (헌바)
    ("대법원 2017. 9. 21. 선고 2017도7843 전원합의체 판결", "2017도7843"),  # 전합
    ("서울고등법원 2014. 11. 20. 선고 2014나2034 판결", "2014나2034"),  # 하급심
]

if __name__ == "__main__":
    print("\n=== B1-lite: 사건번호 파서 robustness ===\n")
    ok = miss = 0
    for s, expected in SAMPLES:
        got = extract_case_number(s)
        good = (got == expected)
        ok += good; miss += (not good)
        flag = "✅" if good else "❌ 파싱실패→하드DENY (정당한데 날조로 차단)"
        print(f"  {flag}  expected {expected:<12} got {str(got):<12}  «{s[:38]}…»")
    print(f"\n  파싱 {ok}/{len(SAMPLES)}  실패 {miss}  → 실패는 전부 정당한 인용의 false-positive")
