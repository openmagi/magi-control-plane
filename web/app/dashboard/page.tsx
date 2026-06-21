import { redirect } from "next/navigation"

/** Convenience alias: /dashboard → /overview. */
export default function DashboardAlias() {
  redirect("/overview")
}
