"use client"

import { useEffect } from "react"

/**
 * Cross-fade view transitions for App Router client navigation.
 *
 * Next 14 has no built-in `viewTransition` config, but the App Router
 * drives every client navigation through `history.pushState` /
 * `replaceState`. Wrapping those in `document.startViewTransition` gives a
 * cross-fade between the outgoing and incoming route (list -> detail
 * continuity) with zero new dependencies.
 *
 * Progressive enhancement: a no-op where `startViewTransition` is
 * unsupported, and skipped entirely under prefers-reduced-motion, so the
 * navigation always happens either way. The actual fade is defined in
 * globals.css (::view-transition-old/new).
 */
export function ViewTransitions() {
  useEffect(() => {
    const doc = document as Document & {
      startViewTransition?: (cb: () => void) => unknown
    }
    if (typeof doc.startViewTransition !== "function") return

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)")

    function wrap<T extends (...args: never[]) => unknown>(fn: T): T {
      return function (this: unknown, ...args: Parameters<T>) {
        // Respect reduced-motion live (no snapshot, just navigate).
        if (reduce.matches) return fn.apply(this, args)
        // startViewTransition runs the callback synchronously, so the
        // history entry is pushed exactly as before; only the paint is
        // deferred into the transition.
        let ret: unknown
        doc.startViewTransition!(() => { ret = fn.apply(this, args) })
        return ret
      } as T
    }

    const origPush = history.pushState
    const origReplace = history.replaceState
    history.pushState = wrap(origPush.bind(history))
    history.replaceState = wrap(origReplace.bind(history))

    return () => {
      history.pushState = origPush
      history.replaceState = origReplace
    }
  }, [])

  return null
}

export default ViewTransitions
