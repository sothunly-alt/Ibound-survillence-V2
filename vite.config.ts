import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const root = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
