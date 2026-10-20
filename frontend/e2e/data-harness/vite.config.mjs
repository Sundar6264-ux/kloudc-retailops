// Screen harness for track B's pages (docs/WEBSITE_READY_CONTRACT.md B1-B5):
// renders Setup > Data & usage and the console's Usage & plan / Data panels
// with the server's response shapes (fixtures.js), so they can be checked at
// 1440/375 in light and dark before track A routes them.
//   npx vite build --config e2e/data-harness/vite.config.mjs  (outDir /tmp/so-wrb-harness)
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const HERE = fileURLToPath(new URL(".", import.meta.url));
export default defineConfig({
  root: HERE,
  base: "./",
  plugins: [react()],
  resolve: { alias: { "@help": fileURLToPath(new URL("../../../content/help", import.meta.url)) } },
  build: { outDir: process.env.HARNESS_OUT || "/tmp/so-wrb-harness", emptyOutDir: true, chunkSizeWarningLimit: 4000 },
});
