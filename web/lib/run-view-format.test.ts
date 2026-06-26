import { describe, it, expect } from "vitest"

import { citeify, shortTool, shortUrl, stripFootnoteTail } from "./run-view-format"

describe("shortTool", () => {
  it("strips an mcp__server__ prefix", () => {
    expect(shortTool("mcp__trading__verify_source")).toBe("verify_source")
  })
  it("leaves a plain tool name unchanged", () => {
    expect(shortTool("Read")).toBe("Read")
  })
})

describe("citeify", () => {
  it("turns [1] into an in-page source link", () => {
    expect(citeify("positive [1] yes")).toBe("positive [1](#src-1) yes")
  })
  it("handles multi-digit", () => {
    expect(citeify("see [12]")).toBe("see [12](#src-12)")
  })
  it("leaves existing markdown links intact", () => {
    expect(citeify("[1](http://x)")).toBe("[1](http://x)")
  })
  it("only ever produces internal #src anchors", () => {
    const out = citeify("a [1] b [2] c")
    expect(out).toBe("a [1](#src-1) b [2](#src-2) c")
    expect(out).not.toMatch(/\]\((?!#src-)/)
  })
  it("no brackets -> unchanged", () => {
    expect(citeify("plain text")).toBe("plain text")
  })
})

describe("shortUrl", () => {
  it("returns bare url when short", () => {
    expect(shortUrl("https://sec.gov/x")).toBe("sec.gov/x")
  })
  it("ellipsizes a long url to host/…/tail", () => {
    const u = "https://www.sec.gov/Archives/edgar/data/1318605/000162828026026673/tsla-20260331.htm"
    const out = shortUrl(u)
    expect(out).toBe("sec.gov/…/tsla-20260331.htm")
    expect(out.length).toBeLessThanOrEqual(46)
  })
  it("falls back to a plain ellipsis on an unparseable but long string", () => {
    const out = shortUrl(`http://${"a".repeat(80)}`)
    expect(out.endsWith("…")).toBe(true)
    expect(out.length).toBeLessThanOrEqual(46)
  })
})

describe("stripFootnoteTail", () => {
  it("removes a trailing [1] url footnote", () => {
    const md = "Operating income positive [1]\n\n[1] https://sec.gov/x"
    expect(stripFootnoteTail(md)).toBe("Operating income positive [1]")
  })
  it("removes a Sources: header + list", () => {
    const md = "Body text\n\nSources:\n[1] https://a.test\n[2] https://b.test"
    expect(stripFootnoteTail(md)).toBe("Body text")
  })
  it("removes a bare-url bullet list under a Sources header", () => {
    const md = "Done.\n\nSources:\n- https://a.test\n- https://b.test"
    expect(stripFootnoteTail(md)).toBe("Done.")
  })
  it("does NOT strip a bare-url bullet list with no header or anchor", () => {
    const md = "Top results:\n- https://a.test\n- https://b.test"
    expect(stripFootnoteTail(md)).toBe(md)
  })
  it("does NOT strip an answer that is only a link", () => {
    const md = "https://example.com/result"
    expect(stripFootnoteTail(md)).toBe(md)
  })
  it("does NOT strip a final bare-url paragraph after prose", () => {
    const md = "Here is the deploy.\n\nhttps://example.com/deploy"
    expect(stripFootnoteTail(md)).toBe(md)
  })
  it("does not strip a sentence that merely ends in a url", () => {
    const md = "See the filing at https://sec.gov/x for details."
    expect(stripFootnoteTail(md)).toBe(md)
  })
  it("leaves prose without a footnote tail untouched", () => {
    const md = "Just a normal answer.\n\nWith two paragraphs."
    expect(stripFootnoteTail(md)).toBe(md)
  })
})
