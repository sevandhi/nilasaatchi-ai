import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// NilaSaatchi AI web workspace (phase6-workspace-ui). API base is configurable via
// VITE_API_BASE (defaults to the local FastAPI dev server, `make api`).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
  preview: {
    port: 4173,
  },
});
