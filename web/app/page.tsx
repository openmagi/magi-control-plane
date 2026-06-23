import { redirect } from "next/navigation"

/**
 * Root entry.
 *
 * Most people reaching `/` here are people who self-hosted via the
 * installer and just opened http://localhost:3000 from the post-install
 * hint. For them the rules list is the right landing (it has the
 * "Create your first policy" affordance when empty). The /welcome
 * marketing page stays reachable via nav but isn't the auto-land.
 *
 * On the marketing-only Vercel deploy, middleware.ts intercepts `/`
 * BEFORE this route runs and sends visitors to /welcome instead.
 */
export default function Root() {
  redirect("/rules")
}
