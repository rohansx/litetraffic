/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const src = fileURLToPath(new URL("./src", import.meta.url));
// The Python package serves this bundle; it is committed so `pip install -e .` needs no node.
const outDir = fileURLToPath(new URL("../../../src/litetraffic/dashboard_ui", import.meta.url));

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": src } },
  build: { outDir, emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8780" } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
