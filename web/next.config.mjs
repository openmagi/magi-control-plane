/** @type {import('next').NextConfig} */
const nextConfig = {
  // DO NOT add `env:` for MAGI_CP_CLOUD_URL. The `env` block inlines
  // values into the build, which means the docker image bakes in
  // whatever value was set at `next build` time (usually nothing →
  // 127.0.0.1:8787 → the container's own loopback). Server-side code
  // reads `process.env.MAGI_CP_CLOUD_URL` directly at runtime, so the
  // compose-supplied `http://cloud:8787` flows through naturally.
  //
  // Standalone output bundles a self-contained `.next/standalone/server.js`
  // so the docker image can run with just node (no full node_modules
  // layer). ~80MB image vs ~400MB without.
  output: "standalone",
}
export default nextConfig
