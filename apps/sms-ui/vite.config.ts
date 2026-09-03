import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { readFileSync } from "node:fs";

const here = dirname(fileURLToPath(import.meta.url));

// In production /brand.js is served from the site root by the static portal.
// `vite dev` has no such root, so serve the same file straight from
// apps/frontendall — keeping ONE copy of the palette.
const serveBrandJs = {
  name: "serve-brand-js",
  configureServer(server: { middlewares: { use: (fn: unknown) => void } }) {
    server.middlewares.use((req: any, res: any, next: () => void) => {
      if ((req.url || "").split("?")[0] !== "/brand.js") return next();
      res.setHeader("Content-Type", "application/javascript");
      res.end(readFileSync(resolve(here, "../frontendall/brand.js"), "utf8"));
    });
  },
};

// Relative base + HashRouter => the built app is fully self-contained under
// /sms/ with NO server config changes and passes the existing
// scripts/validate-frontend.mjs (assets resolve as ./assets/...).
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss(), serveBrandJs],
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
