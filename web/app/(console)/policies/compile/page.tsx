import { redirect } from "next/navigation"

/** Retired in v2.2. /policies/new now has the NL→IR compile section
 * at the top of the same page as the IR fields + Save. Keeping a
 * redirect so old bookmarks + the sidebar's legacy /policies/compile
 * link still land in the right place. */
export default async function CompileRedirect() {
  redirect("/policies/new")
}
