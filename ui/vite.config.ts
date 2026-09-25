/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `pnpm dev` proxies /api to `pigtail ui serve` on 127.0.0.1:8080; `pnpm build` writes dist/,
// which FastAPI serves in production (one process).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: { "/api": "http://127.0.0.1:8080" },
  },
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "jsdom", include: ["src/**/*.test.tsx", "src/**/*.test.ts"] },
});
