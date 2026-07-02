import { defineConfig } from "vitest/config"
import { fileURLToPath } from "node:url"

// Mirror the tsconfig `@/*` path alias so route tests that `await import` a
// route module can resolve its `@/lib/*` imports. Without this, vitest leaves
// `@/…` specifiers unresolved (there was no vitest config), which is why the
// existing route tests avoided importing route modules that use `@/`.
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
})
