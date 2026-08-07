import { resolve } from "node:path";

import preact from "@preact/preset-vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [preact()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    lib: {
      entry: resolve(import.meta.dirname, "src/main.tsx"),
      formats: ["es"],
      fileName: "editor-foundation",
      cssFileName: "editor-foundation",
    },
  },
});
