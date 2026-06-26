/** Pure formatting helpers for the public shared-run page (`app/r/[token]`).
 *  Kept here (not in the server component) so they are unit-testable. */

/** `mcp__server__tool` -> `tool` for a readable transcript label. */
export function shortTool(name: string): string {
  return name.includes("__") ? name.split("__").pop() || name : name
}

/** Turn bracketed footnotes `[1]` into an in-page citation link `[1](#src-1)`,
 *  without touching existing markdown links `[1](url)`. The renderer styles
 *  `#src-N` anchors as a raised green chip that jumps to the matching source.
 *  XSS-safe: the only href produced is an internal `#src-N` fragment. */
export function citeify(md: string): string {
  return md.replace(/\[(\d{1,3})\](?!\()/g, (_, n: string) => `[${n}](#src-${n})`)
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
 *  keeping the agent's own tail would duplicate it. Only strips from the end.
 *
 *  Conservative: bare or numbered URL lines are stripped ONLY when the trailing
 *  block is clearly a reference list, signalled by an anchored `[n]`/superscript
 *  footnote or a `Sources:`/`References:` header. A final answer that simply IS
 *  a link (or a list of result links) is left untouched. */
export function stripFootnoteTail(md: string): string {
  const lines = md.replace(/\s+$/, "").split("\n")
  // An anchored footnote definition: `[1] url`, `[1]: url`, or `¹ url`.
  const anchored = (l: string) =>
    /^\s*(\[\d{1,3}\]:?|[⁰¹²³⁴⁵⁶⁷⁸⁹]+)\s+https?:\/\/\S+\s*$/.test(l)
  // A loose reference line: a bare/bulleted/numbered URL (only strippable when
  // the block also carries an anchor or a header).
  const looseRef = (l: string) =>
    /^\s*([-*]\s+|\d{1,3}\.\s+)?https?:\/\/\S+\s*$/.test(l)
  const isHeader = (l: string) =>
    /^\s*#{0,4}\s*(sources?|references?|citations?)\s*:?\s*$/i.test(l) ||
    /^\s*\*\*(sources?|references?|citations?)\*\*\s*:?\s*$/i.test(l)

  let end = lines.length
  let sawAnchor = false
  let sawHeader = false
  for (let i = lines.length - 1; i >= 0; i--) {
    const l = lines[i]
    if (l.trim() === "") { end = i; continue }
    if (anchored(l)) { sawAnchor = true; end = i; continue }
    if (looseRef(l)) { end = i; continue }
    if (isHeader(l)) { sawHeader = true; end = i; break }
    break
  }
  // Strip only when the trailing block is unambiguously a reference list.
  return sawAnchor || sawHeader ? lines.slice(0, end).join("\n").replace(/\s+$/, "") : md
}
