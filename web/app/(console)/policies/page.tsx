import { redirect } from "next/navigation"

// Policies list was folded into /rules. Detail pages /policies/[...id],
// authoring at /policies/new, and the legacy /policies/compile path
// still live under app/(console)/policies/* — only the list view moved.
export default function PoliciesListRedirect() {
  redirect("/rules")
}
