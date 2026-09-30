import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // Override for a second local API (e.g. another worktree on :8001):
      // GLASSBOX_API_PROXY=http://localhost:8001 npm run dev -- --port 5174
      '/api': process.env.GLASSBOX_API_PROXY ?? 'http://localhost:8000',
    },
  },
})
