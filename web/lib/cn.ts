import clsx, { type ClassValue } from "clsx"

/** className composer. Re-exports clsx with a stable name; later we can
 * swap in tailwind-merge here without touching call sites. */
export function cn(...inputs: ClassValue[]): string {
  return clsx(...inputs)
}
