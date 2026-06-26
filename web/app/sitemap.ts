import type { MetadataRoute } from "next"
import { DOCS_INDEX } from "@/lib/docs"

const SITE_URL =
  process.env.MAGI_CP_PUBLIC_SITE_URL ||
  process.env.MAGI_CP_PUBLIC_CLOUD_URL ||
  "https://cloud.openmagi.ai"

/** Sitemap for crawlers. Only publicly-reachable pages. dashboard
 * surfaces (/, /policies, /hitl, /admin/*) are excluded since they 401
 * without admin keys and shouldn't be indexed anyway. */
export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date("2026-06-25T00:00:00Z")
  const routes: { url: string; changeFreq: string; priority: number }[] = [
    { url: "/welcome",         changeFreq: "weekly",  priority: 1.0 },
    { url: "/install",         changeFreq: "monthly", priority: 0.8 },
    { url: "/docs",            changeFreq: "weekly",  priority: 0.9 },
    { url: "/legal/terms",     changeFreq: "yearly",  priority: 0.3 },
    { url: "/legal/privacy",   changeFreq: "yearly",  priority: 0.3 },
  ]
  for (const doc of DOCS_INDEX) {
    routes.push({
      url: `/docs/${doc.slug}`,
      changeFreq: "weekly",
      priority: 0.7,
    })
  }
  return routes.map(r => ({
    url: `${SITE_URL}${r.url}`,
    lastModified: now,
    changeFrequency: r.changeFreq as MetadataRoute.Sitemap[number]["changeFrequency"],
    priority: r.priority,
  }))
}
