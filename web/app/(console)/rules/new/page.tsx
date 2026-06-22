import { redirect } from "next/navigation"

// /rules/new is the canonical authoring entry going forward. For now
// the policy-authoring flow lives at /policies/new (picker → NL ↔
// guided ↔ advanced). Step 3 extends that picker with a "custom
// verifier" option; for the moment we forward there to keep the
// sidebar's "New rule" button working.
export default function RulesNewRedirect(
  { searchParams }: { searchParams: { mode?: string; edit?: string } },
) {
  const qs = new URLSearchParams()
  if (searchParams.mode) qs.set("mode", searchParams.mode)
  if (searchParams.edit) qs.set("edit", searchParams.edit)
  const suffix = qs.toString() ? `?${qs.toString()}` : ""
  redirect(`/policies/new${suffix}`)
}
