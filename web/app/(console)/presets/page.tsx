import { redirect } from "next/navigation"

// /presets was renamed → /rules. Keep this stub so existing links (the
// emptied-state CTA from older policies pages, browser history, docs)
// don't 404. The /presets/_components and actions.ts modules are still
// imported by the new /rules page during the gradual migration, so we
// don't delete them yet.
export default function PresetsRedirect() {
  redirect("/rules")
}
