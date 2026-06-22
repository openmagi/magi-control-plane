import { redirect } from "next/navigation"

// /rules/new is the canonical "add a rule" entry. Policy is the only
// first-class entity (pure-derivation pivot), so this always forwards
// to the policy authoring picker.
export default function RulesNewRedirect(
  { searchParams }: { searchParams: { mode?: string } },
) {
  const suffix = searchParams.mode ? `?mode=${encodeURIComponent(searchParams.mode)}` : ""
  redirect(`/policies/new${suffix}`)
}
