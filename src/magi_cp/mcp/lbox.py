"""law.go.kr precedent adapter (Korean Open API).

Ported from the legacy public-data-worker.js but trimmed to the two endpoints
the verifier needs: search and detail-by-id. OC=clawy is a registered ID (not
a secret). API doc: http://www.law.go.kr/DRF
"""
from __future__ import annotations
import html
import json
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

OC = "clawy"
# v2.0-W7: HTTPS to defeat path-MITM injecting fake judgments. law.go.kr
# supports HTTPS; the http://… form auto-redirects to https://… on the
# server, but we explicitly request HTTPS so a downgrade attacker cannot
# silently strip the redirect.
SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"
TIMEOUT = 20

_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
# Korean case number e.g. "2018도13694", "2008헌가23", "94다35718"
_CASE_NO_NORM = re.compile(r"\s+")


def clean_html(s: str) -> str:
    """Decode entities, drop <br>, drop other tags, collapse whitespace."""
    if not s:
        return ""
    s = html.unescape(s)
    s = _BR.sub(" ", s)
    s = _TAG.sub(" ", s)
    return _WS.sub(" ", s).strip()


def normalize_case_no(s: str) -> str:
    """Strip internal whitespace from a case number, e.g. '2018 도 13694' → '2018도13694'."""
    return _CASE_NO_NORM.sub("", s).strip()


def extract_case_holding(judgment_text: str) -> str:
    """Pick a single sentence from 판결요지 — useful for source-citation pair generation
    in tests/fixtures. Drops `[1]` `[2]` numbering markers.
    """
    t = clean_html(judgment_text)
    t = re.sub(r"\[\d+\]\s*", "", t)
    for sent in re.split(r"(?<=다\.)\s+", t):
        if 30 < len(sent) < 250:
            return sent.strip()
    return t[:200].strip()


def _get(url: str, params: dict) -> dict:
    qs = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
    req = Request(f"{url}?{qs}", headers={"Accept": "application/json"})
    with urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def search_precedent(query: str, *, display: int = 20, page: int = 1) -> list[dict]:
    """Search precedents by free text. Returns simplified hits."""
    raw = _get(SEARCH_URL, {
        "OC": OC, "target": "prec", "type": "JSON",
        "query": query, "display": display, "page": page,
    })
    items = raw.get("PrecSearch", {}).get("prec", [])
    out = []
    for it in items if isinstance(items, list) else [items]:
        out.append({
            "id": it.get("판례일련번호", ""),
            "case_no": it.get("사건번호", ""),
            "title": clean_html(it.get("사건명", "")),
            "court": it.get("법원명", ""),
            "date": it.get("선고일자", ""),
        })
    return out


def fetch_precedent(prec_id: str) -> dict:
    """Fetch precedent detail by 판례일련번호. Returns text fields with HTML stripped."""
    raw = _get(SERVICE_URL, {
        "OC": OC, "target": "prec", "ID": prec_id, "type": "JSON",
    })
    ps = raw.get("PrecService", {})
    return {
        "id": prec_id,
        "case_no": ps.get("사건번호", ""),
        "title": clean_html(ps.get("사건명", "")),
        "court": ps.get("법원명", ""),
        "date": ps.get("선고일자", ""),
        "holding": clean_html(ps.get("판시사항", "")),
        "judgment_summary": clean_html(ps.get("판결요지", "")),
        "judgment_full": clean_html(ps.get("판례내용", "")),
        "references": clean_html(ps.get("참조판례", "")),
        "articles": clean_html(ps.get("참조조문", "")),
    }


def fetch_by_case_number(case_no: str) -> dict | None:
    """Convenience: search by case number, fetch first matching detail."""
    case_no = normalize_case_no(case_no)
    hits = search_precedent(case_no, display=5)
    hit = next((h for h in hits if h["case_no"] == case_no), None)
    if not hit:
        return None
    return fetch_precedent(hit["id"])
