/** @type {import('next').NextConfig} */
const nextConfig = {
  // Cloud env vars: server-side only. Never expose api keys to client bundle.
  env: { MAGI_CP_CLOUD_URL: process.env.MAGI_CP_CLOUD_URL || "http://127.0.0.1:8787" },
}
export default nextConfig
