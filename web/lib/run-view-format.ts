/** Pure formatting helpers for the public shared-run page (`app/r/[token]`).
 *  Kept here (not in the server component) so they are unit-testable. */

const SUP: Record<string, string> = {
  "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
  "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
}

/** Turn bracketed footnotes `[1]` into superscript `¹` (citation style), without
 *  touching markdown links `[1](url)`. Pure text transform, XSS-safe. */
export function citeify(md: string): string {
  return md.replace(/\[(\d{1,3})\](?!\()/g, (_, n: string) =>
    [...n].map((d) => SUP[d] ?? d).join(""),
  )
}

/** Compact display form for a long URL: `host/…/last-segment`, ellipsized.
 *  The full URL stays the href; only the visible text shrinks so it never wraps. */
export function shortUrl(url: string, max = 46): string {
  const bare = url.replace(/^https?:\/\//i, "")
  if (bare.length <= max) return bare
  try {
    const u = new URL(url)
    const host = u.hostname.replace(/^www\./, "")
    const segs = u.pathname.split("/").filter(Boolean)
    const tail = segs.length ? segs[segs.length - 1] : ""
    const cand = tail ? `${host}/…/${tail}` : host
    return cand.length <= max ? cand : `${cand.slice(0, max - 1)}…`
  } catch {
    return `${bare.slice(0, max - 1)}…`
  }
}

/** Drop a trailing footnote/reference list from the answer (e.g. `[1] http://…`
 *  or a `Sources:` block). The numbered Sources panel is the canonical list, so
 *  keeping the agent's own tail would duplicate it. Only strips from the end. */
export function stripFootnoteTail(md: string): string {
  const lines = md.replace(/\s+$/, "").split("\n")
  const isDef = (l: string) =>
    /^\s*(\[\d{1,3}\]:?|\d{1,3}\.|[⁰¹²³⁴⁵⁶⁷⁸⁹]+)\s+https?:\/\/\S+\s*$/.test(l) ||
    /^\s*[-*]?\s*https?:\/\/\S+\s*$/.test(l)
  const isHeader = (l: string) =>
    /^\s*#{0,4}\s*(sources?|references?|citations?)\s*:?\s*$/i.test(l) ||
    /^\s*\*\*(sources?|references?|citations?)\*\*\s*:?\s*$/i.test(l)
  let end = lines.length
  let sawDef = false
  for (let i = lines.length - 1; i >= 0; i--) {
    const l = lines[i]
    if (l.trim() === "") { end = i; continue }
    if (isDef(l)) { sawDef = true; end = i; continue }
    if (sawDef && isHeader(l)) { end = i; continue }
    break
  }
  return sawDef ? lines.slice(0, end).join("\n").replace(/\s+$/, "") : md
}
