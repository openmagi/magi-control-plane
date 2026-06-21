import type { MetadataRoute } from "next"

const SITE_URL =
  process.env.MAGI_CP_PUBLIC_SITE_URL ||
  process.env.MAGI_CP_PUBLIC_CLOUD_URL ||
  "https://cloud.openmagi.ai"

/** Sitemap for crawlers. Only publicly-reachable pages — dashboard
 * surfaces (/, /policies, /hitl, /admin/*) are excluded since they 401
 * without admin keys and shouldn't be indexed anyway. */
export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date("2026-06-20T00:00:00Z")
  const routes = [
    { url: "/welcome",         changeFreq: "weekly",  priority: 1.0 },
    { url: "/install",         changeFreq: "monthly", priority: 0.8 },
    { url: "/legal/terms",     changeFreq: "yearly",  priority: 0.3 },
    { url: "/legal/privacy",   changeFreq: "yearly",  priority: 0.3 },
  ] as const
  return routes.map(r => ({
    url: `${SITE_URL}${r.url}`,
    lastModified: now,
    changeFrequency: r.changeFreq as MetadataRoute.Sitemap[number]["changeFrequency"],
    priority: r.priority,
  }))
}
