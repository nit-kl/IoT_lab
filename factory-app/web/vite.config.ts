import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// npm run dev のときは、/api を app_server.py（127.0.0.1:8770）へ転送する。
// changeOrigin で Host ヘッダーを 127.0.0.1:8770 にするので、app_server.py の localhost 以外の拒否に引っかからない。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8770", changeOrigin: true } },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 2000 },
});
