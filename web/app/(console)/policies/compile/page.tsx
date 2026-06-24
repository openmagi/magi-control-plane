import { redirect } from "next/navigation"

/** Retired in v2.2; NL compose retired in D56b. Redirects directly to
 * the Conversational compose surface so we avoid a double-hop through
 * /policies/new (which itself routes to ?mode=conversational by
 * default). Kept so old bookmarks + any legacy sidebar link still land
 * in the right place. */
export default async function CompileRedirect() {
  redirect("/policies/new?mode=conversational")
}
