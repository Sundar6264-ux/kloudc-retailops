import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Help content (pages, guides, FAQ, glossary concepts) lives once for the
// server, the website and the app in ../content/help (docs/DESIGN_SYSTEM.md
// 5.2). The website imports it at build time through "@help", so help works
// logged out, with no request and no flash.
const HELP_DIR = fileURLToPath(new URL("../content/help", import.meta.url));

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  resolve: {
    alias: { "@help": HELP_DIR },
  },
  server: {
    port: 5173,
    // The dev server may read ../content (the help JSON) as well as this folder.
    fs: { allow: [".", ".."] },
  },
  build: {
    // The mobile (Capacitor) build bakes in an absolute API URL (see
    // .env.mobile) instead of the web build's relative "" one - it must
    // never land in plain `dist/`, since that's the exact directory the
    // backend serves for the real web app (see app/main.py's StaticFiles
    // mount). A stray mobile build there would silently ship a
    // hardcoded-today's-domain bundle to every web visitor.
    outDir: mode === "mobile" ? "dist-mobile" : "dist",
  },
}));
