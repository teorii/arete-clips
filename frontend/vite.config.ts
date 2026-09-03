import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // The built app is mounted at /app by the FastAPI server, so asset URLs must
  // be prefixed to match.
  base: '/app/',
  server: {
    // Proxy in dev so the app can use same-origin relative URLs in both modes.
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
