import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Backend target: docker-compose exposes :9000, local uvicorn often :8000.
// Override with VITE_API_TARGET when running the API elsewhere.
const target = process.env.VITE_API_TARGET || 'http://localhost:9000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': target,
      // Dataset photos are served by the backend, so the browser can use them directly.
      '/dataset': target,
      // Trader-uploaded verification photos live on the backend too.
      '/uploads': target,
    },
  },
})
