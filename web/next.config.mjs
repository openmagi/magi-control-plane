/** @type {import('next').NextConfig} */
const nextConfig = {
  // Cloud env vars: server-side only. Never expose api keys to client bundle.
  env: { MAGI_CP_CLOUD_URL: process.env.MAGI_CP_CLOUD_URL || "http://127.0.0.1:8787" },
  // Standalone output bundles a self-contained `.next/standalone/server.js`
  // so the docker image can run with just node (no full node_modules layer).
  // ~80MB image vs ~400MB without.
  output: "standalone",
}
export default nextConfig
