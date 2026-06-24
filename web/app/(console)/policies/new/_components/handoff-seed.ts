/**
 * D57g: pure utilities for the "Continue in conversation" handoff
 * seed. Separated from `HandoffLink.tsx` so the encoder / decoder can
 * be exercised under vitest without the client component's
 * `@`-aliased imports (the dashboard's vitest config does not
 * resolve the Next.js path alias).
 *
 * The seed is a base64-encoded JSON object with the shape:
 *   {
 *     wizard_state?: object,
 *     draft_ir?: object,
 *     origin?: "guided" | "advanced" | "review"
 *   }
 *
 * UTF-8 safe: operator descriptions / patterns may contain Hangul or
 * emoji. We round-trip through TextEncoder / TextDecoder (browser)
 * or Buffer (Node).
 */

export type HandoffSeedPayload = {
  wizard_state?: Record<string, unknown>
  draft_ir?: Record<string, unknown>
  origin?: "guided" | "advanced" | "review"
}

export function encodeSeed(payload: unknown): string {
  const json = JSON.stringify(payload)
  if (typeof window === "undefined") {
    return Buffer.from(json, "utf-8").toString("base64")
  }
  const bytes = new TextEncoder().encode(json)
  let binary = ""
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i])
  }
  return window.btoa(binary)
}

export function decodeSeed(seed: string): HandoffSeedPayload | null {
  try {
    let json: string
    if (typeof window === "undefined") {
      json = Buffer.from(seed, "base64").toString("utf-8")
    } else {
      const binary = window.atob(seed)
      const bytes = new Uint8Array(binary.length)
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
      json = new TextDecoder("utf-8").decode(bytes)
    }
    const parsed = JSON.parse(json)
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return null
    }
    return parsed as HandoffSeedPayload
  } catch {
    return null
  }
}
