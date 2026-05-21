import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Allow overriding the dev API target from the environment so the proxy can
// quickly switch between local dev backends running on different ports.
const apiTarget = process.env.VITE_DEV_API_TARGET || "http://127.0.0.1:8001"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        // Long timeouts cover slow OCR / large SOP imports in dev (browser fetch + proxy).
        timeout: 600000,
      },
    },
  },
})
