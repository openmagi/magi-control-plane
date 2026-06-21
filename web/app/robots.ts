import type { MetadataRoute } from "next"

const SITE_URL =
  process.env.MAGI_CP_PUBLIC_SITE_URL ||
  process.env.MAGI_CP_PUBLIC_CLOUD_URL ||
  "https://cloud.openmagi.ai"

/** Block crawlers from indexing the operational dashboard. /welcome,
 * /signup, and /legal/* are explicitly allowed; everything else is
 * Disallow'd because it either requires auth or shouldn't be indexed. */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: ["/welcome", "/install", "/legal/", "/install.sh"],
        disallow: [
          "/",
          "/policies/",
          "/verify",
          "/hitl/",
          "/ledger",
          "/presets",
          "/setup",
          "/api/",
        ],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
  }
}
