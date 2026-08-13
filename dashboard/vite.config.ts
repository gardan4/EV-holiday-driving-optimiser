import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { defineConfig } from "vite"

// Built straight into the Python package so the `dashboard` process role can
// serve it with StaticFiles (see `app/main.py`, bottom). One image, one build
// output, nothing to copy by hand.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../src/dashboard_static",
    emptyOutDir: true,
    // The CSP the dashboard role sends is `script-src 'self'` with no
    // 'unsafe-inline' — unlike the public app, which needs it for Next's
    // bootstrap. So nothing may be inlined into the HTML.
    assetsInlineLimit: 0,
  },
  server: {
    port: 5273,
    // `npm run dev` talks to a locally-running dashboard role on 8101, so the
    // session cookie is same-origin in development exactly as it is in prod.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8101",
        changeOrigin: false,
      },
    },
  },
})
