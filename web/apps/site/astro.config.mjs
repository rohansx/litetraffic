// @ts-check
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "astro/config";

// Static landing page; output goes to apps/site/dist.
export default defineConfig({
  output: "static",
  vite: { plugins: [tailwindcss()] },
});
