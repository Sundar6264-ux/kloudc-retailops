import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    port: 5173,
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
