import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

// Relative base + HashRouter => the built app is fully self-contained under
// /sms/ with NO server config changes and passes the existing
// scripts/validate-frontend.mjs (assets resolve as ./assets/...).
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  build: {
    outDir: resolve(here, "../frontendall/sms"),
    emptyOutDir: true,
  },
  server: {
    port: 5501,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/socket.io": { target: "http://127.0.0.1:8000", ws: true },
    },
  },
});
