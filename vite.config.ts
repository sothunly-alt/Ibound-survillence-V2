import path from "node:path";
import { fileURLToPath } from "node:url";
import { copyFileSync, mkdirSync } from "node:fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const root = path.dirname(fileURLToPath(import.meta.url));

function copyLandingJs() {
  return {
    name: "copy-landing-js",
    closeBundle() {
      const from = path.resolve(root, "js/main.js");
      const toDir = path.resolve(root, "dist/js");
      mkdirSync(toDir, { recursive: true });
      copyFileSync(from, path.resolve(toDir, "main.js"));
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), copyLandingJs()],
  resolve: {
    alias: {
      "@": path.resolve(root, "src"),
    },
  },
  build: {
    rollupOptions: {
      input: {
        main: path.resolve(root, "index.html"),
        dashboard: path.resolve(root, "dashboard.html"),
        privacy: path.resolve(root, "privacy.html"),
        notFound: path.resolve(root, "404.html"),
        ogPreview: path.resolve(root, "og-preview.html"),
        maintenance: path.resolve(root, "503.html"),
        thankYou: path.resolve(root, "thank-you.html"),
        about: path.resolve(root, "about.html"),
        terms: path.resolve(root, "terms.html"),
        team: path.resolve(root, "team.html"),
      },
    },
  },
});
