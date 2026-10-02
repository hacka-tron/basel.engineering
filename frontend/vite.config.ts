import { fileURLToPath } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { portfolioPlugin } from './vite-plugins/portfolio.ts'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    // corpus/portfolio/*.md -> `virtual:portfolio` (spec 2026-10-02 §7). In
    // the Docker build the corpus is copied to the same relative place.
    portfolioPlugin({
      corpusDir: fileURLToPath(new URL('../corpus/portfolio', import.meta.url)),
      publicDir: fileURLToPath(new URL('./public', import.meta.url)),
    }),
  ],
  server: {
    proxy: {
      // Override for a second local API (e.g. another worktree on :8001):
      // GLASSBOX_API_PROXY=http://localhost:8001 npm run dev -- --port 5174
      '/api': process.env.GLASSBOX_API_PROXY ?? 'http://localhost:8000',
    },
  },
})
